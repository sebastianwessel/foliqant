"""Private append-only journal of finished attempts, so a long evaluation can resume.

A live evaluation of hundreds of cases against a slow model can take hours. With an
``EvaluationCheckpoint``, ``evaluate`` appends every attempt whose pipeline returned
a ``completed`` or ``needs_review`` result the moment it finishes. Running the same
evaluation again with the same checkpoint reuses each recorded attempt instead of
calling the pipeline, and scores it against the *current* gold, exactly like replay.
Failed, cancelled, timed-out and errored attempts are never recorded, so they run
again. An interruption loses at most the attempts in flight.

The checkpoint belongs to one evaluation identity per suite: suite name, variant name
and revision, configuration revision and target. A recorded attempt is reused only
for the same case ID, repetition and identical input. Resuming with another variant
or configuration revision fails instead of mixing results; use a new checkpoint.
The file holds private inputs and complete results: keep it out of Git and CI logs.
"""

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from foliqant.core.json import JsonValue

from .contracts import EvaluationVariant

MAX_CHECKPOINT_BYTES = 1024 * 1024 * 1024
_KIND = "evaluation_attempt"


class CheckpointMismatchError(ValueError):
    """The checkpoint records this suite for another variant, configuration or target."""


@dataclass(frozen=True, slots=True)
class SavedAttempt:
    """One recorded attempt: its exact input, invocation seconds and public result."""

    input: JsonValue
    elapsed_seconds: float
    result: JsonValue


@dataclass(frozen=True, slots=True)
class EvaluationProgress:
    """Content-free progress of one suite, reported after every finished attempt.

    ``completed`` includes ``resumed`` attempts taken from a checkpoint and
    ``failed`` attempts (failed, cancelled or error). ``remaining_seconds``
    extrapolates the mean duration of the attempts executed in this run; None
    before the first executed attempt.
    """

    suite: str
    completed: int
    total: int
    resumed: int
    failed: int
    elapsed_seconds: float

    @property
    def remaining_seconds(self) -> float | None:
        executed = self.completed - self.resumed
        if executed <= 0:
            return None
        return self.elapsed_seconds / executed * (self.total - self.completed)


type ProgressCallback = Callable[[EvaluationProgress], None]


def _json(value: str) -> Any:
    def constant(item: str) -> object:
        raise ValueError("nonfinite JSON number")

    return json.loads(value, parse_constant=constant)


def checkpoint_identity(suite: str, variant: EvaluationVariant) -> dict[str, JsonValue]:
    """What a recorded attempt of suite ``suite`` must share with an evaluation to be reused."""
    return {
        "suite": suite,
        "variant": variant.name,
        "variant_revision": variant.revision,
        "configuration_revision": variant.configuration_revision,
        "workflow": variant.workflow,
        "flow": variant.flow,
        "step": variant.step,
    }


class EvaluationCheckpoint:
    """A JSONL journal at ``path`` (created owner-readable on the first record).

    One file may hold several suites; each suite keeps its own identity.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise ValueError("checkpoint path must be a pathlib.Path")
        self.path = path

    def load(self, identity: Mapping[str, JsonValue]) -> dict[tuple[str, int], SavedAttempt]:
        """Recorded attempts of the identity's suite (the last record of an attempt wins).

        A torn final line (hard kill during a write) is ignored; any other invalid
        record fails, as does a record of the same suite with another identity.
        """
        if not self.path.exists():
            return {}
        details = self.path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint must be a bounded regular file")
        lines = self.path.read_text(encoding="utf-8").split("\n")
        saved: dict[tuple[str, int], SavedAttempt] = {}
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = _json(line)
            except ValueError:
                if index == len(lines) - 1:
                    continue  # torn last write
                raise ValueError("checkpoint contains an invalid record") from None
            if (
                not isinstance(record, dict)
                or record.get("kind") != _KIND
                or not isinstance(record.get("identity"), dict)
            ):
                raise ValueError("checkpoint contains an invalid record")
            if record["identity"].get("suite") != identity["suite"]:
                continue
            if record["identity"] != dict(identity):
                raise CheckpointMismatchError(
                    "checkpoint records this suite for another variant, configuration or target"
                )
            case_id, repetition = record.get("case"), record.get("repetition")
            elapsed = record.get("elapsed_seconds")
            if (
                not isinstance(case_id, str)
                or type(repetition) is not int
                or repetition < 1
                or type(elapsed) not in (int, float)
                or cast(int | float, elapsed) < 0
                or "input" not in record
                or not isinstance(record.get("result"), dict)
            ):
                raise ValueError("checkpoint contains an invalid record")
            saved[case_id, repetition] = SavedAttempt(
                record["input"], float(cast(int | float, elapsed)), record["result"]
            )
        return saved

    def recorded_cases(self, suite: str, variant: EvaluationVariant) -> frozenset[str]:
        """Case IDs of ``suite`` with a recorded attempt of ``variant`` (any input).

        Hosts use it to select which cases to resume, or to rescore only recorded
        cases offline. A record of the suite with another identity fails.
        """
        return frozenset(case for case, _ in self.load(checkpoint_identity(suite, variant)))

    def record(
        self,
        identity: Mapping[str, JsonValue],
        case_id: str,
        repetition: int,
        case_input: JsonValue,
        elapsed_seconds: float,
        result: JsonValue,
    ) -> None:
        """Durably append one finished attempt (fsync), never following a symlink."""
        line = json.dumps(
            {
                "kind": _KIND,
                "identity": dict(identity),
                "case": case_id,
                "repetition": repetition,
                "input": case_input,
                "elapsed_seconds": elapsed_seconds,
                "result": result,
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            size = os.fstat(descriptor).st_size
            prefix = b""
            if size:
                with self.path.open("rb") as stream:
                    stream.seek(size - 1)
                    if stream.read(1) != b"\n":
                        prefix = b"\n"  # never extend a torn line
            data = prefix + line.encode("ascii") + b"\n"
            if size + len(data) > MAX_CHECKPOINT_BYTES:
                raise ValueError("checkpoint exceeds size limit")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

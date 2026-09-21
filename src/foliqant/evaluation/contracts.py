"""Immutable, caller-owned golden cases and evaluation reports.

Reports omit business values by default. Explicitly requested details are private
caller-owned snapshots. Callers own serialization/storage and must choose safe IDs.
"""

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from hashlib import sha256
from typing import Literal, cast

from pydantic import BaseModel

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult, Usage
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson, JsonValue, freeze_json, thaw_json

from .metrics import MetricReport
from .spans import source_span, source_span_source
from .summaries import LatencySummary, UsageSummary, summarize_latency, summarize_usage

type Comparison = Literal["exact", "set", "source_span", "custom"]
type CheckOutcome = Literal["passed", "failed", "missing", "skipped", "error"]
type Pipeline = Callable[[Envelope], Awaitable[ExecutionResult]]
type Scorer = Callable[[FrozenJson, FrozenJson], Awaitable[bool]]


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("evaluation identifiers must be nonblank strings up to 512 characters")


def canonical(value: FrozenJson) -> str:
    return json.dumps(thaw_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Expectation:
    """One explicit JSON-pointer assertion against the public ExecutionResult.

    Exact comparison preserves array order and JSON scalar types. Set comparison
    requires arrays and ignores only their top-level ordering and duplicates.
    Source-span comparison uses independently authored ranges over case input.
    A custom comparison names a registered async scorer; its revision is recorded.
    """

    name: str
    path: str
    expected: FrozenJson
    comparison: Comparison = "exact"
    scorer: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.name)
        if (
            not isinstance(self.path, str)
            or (self.path and not self.path.startswith("/"))
            or re.search(r"~(?:[^01]|$)", self.path)
            or len(self.path.split("/")) > MAX_JSON_DEPTH + 1
        ):
            raise ValueError("expectation path must be an RFC 6901 JSON pointer")
        object.__setattr__(self, "expected", freeze_json(self.expected))
        if self.comparison not in {"exact", "set", "source_span", "custom"}:
            raise ValueError("unknown comparison")
        if self.comparison == "set" and not isinstance(self.expected, tuple):
            raise ValueError("set expectations require a JSON array")
        if self.comparison == "source_span":
            source_span(self.expected)
        if self.comparison == "custom":
            if self.scorer is None:
                raise ValueError("custom expectations require a scorer name")
            _identifier(self.scorer)
        elif self.scorer is not None:
            raise ValueError("only custom expectations accept a scorer")


@dataclass(frozen=True, slots=True, init=False)
class EvaluationCase:
    """Snapshot one envelope and its gold assertions; each run gets a fresh copy."""

    id: str
    input: AcceptedEnvelope
    expectations: tuple[Expectation, ...]

    def __init__(self, id: str, envelope: Envelope, expectations: tuple[Expectation, ...]) -> None:
        _identifier(id)
        snapshot = Envelope.model_validate(envelope.model_dump(mode="json"), strict=True)
        checks = tuple(expectations)
        if not checks or not all(isinstance(check, Expectation) for check in checks):
            raise ValueError("each case requires typed expectations")
        if len({check.name for check in checks}) != len(checks):
            raise ValueError("expectation names must be unique within a case")
        case_input = freeze_json(
            {
                "payload": snapshot.payload,
                "metadata": snapshot.metadata.model_dump(mode="json"),
            },
            max_depth=MAX_JSON_DEPTH + 1,
        )
        for check in checks:
            if check.comparison == "source_span":
                source_span_source(check.expected, case_input)
        object.__setattr__(self, "id", id)
        object.__setattr__(
            self,
            "input",
            AcceptedEnvelope(
                payload=snapshot.payload, metadata=snapshot.metadata.model_dump(mode="json")
            ),
        )
        object.__setattr__(self, "expectations", checks)

    def envelope(self) -> Envelope:
        """Return independent validated input; variant mutations cannot alter gold."""
        return Envelope.model_validate(
            {"payload": thaw_json(self.input.payload), "metadata": thaw_json(self.input.metadata)},
            strict=True,
        )


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    """A versioned, nonempty in-memory golden suite with content fingerprint."""

    name: str
    revision: str
    cases: tuple[EvaluationCase, ...]
    _fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _identifier(self.name)
        _identifier(self.revision)
        object.__setattr__(self, "cases", tuple(self.cases))
        if not self.cases or not all(isinstance(case, EvaluationCase) for case in self.cases):
            raise ValueError("suite requires typed cases")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        object.__setattr__(self, "_fingerprint", self._compute_fingerprint())

    @property
    def fingerprint(self) -> str:
        """Hash suite identity, ordered inputs and gold; never infer model identity."""
        return self._fingerprint

    def _compute_fingerprint(self) -> str:
        content = {
            "name": self.name,
            "revision": self.revision,
            "cases": [
                {
                    "id": case.id,
                    "payload": thaw_json(case.input.payload),
                    "metadata": thaw_json(case.input.metadata),
                    "expectations": [
                        {
                            "name": check.name,
                            "path": check.path,
                            "expected": thaw_json(check.expected),
                            "comparison": check.comparison,
                            "scorer": check.scorer,
                        }
                        for check in case.expectations
                    ],
                }
                for case in self.cases
            ],
        }
        # Individual inputs and gold are already bounded, validated JSON. Do not
        # reapply their depth limit to the additional suite/container structure.
        encoded = json.dumps(
            content, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class EvaluationVariant:
    """Caller-selected pipeline and explicit nonsecret configuration identities.

    Revisions describe caller configuration, not verified provider model weights.
    Close over a prepared application's run or run_step method in ``run``.
    """

    name: str
    revision: str
    run: Pipeline
    configuration_revision: str
    step: str | None = None
    workflow: str | None = None

    def __post_init__(self) -> None:
        for value in (self.name, self.revision, self.configuration_revision):
            _identifier(value)
        if not callable(self.run):
            raise ValueError("variant requires an async pipeline callable")
        if self.step is not None:
            _identifier(self.step)
        if self.workflow is not None:
            _identifier(self.workflow)


@dataclass(frozen=True, slots=True)
class RegisteredScorer:
    """An explicitly versioned async predicate over immutable actual and gold JSON."""

    name: str
    revision: str
    score: Scorer

    def __post_init__(self) -> None:
        _identifier(self.name)
        _identifier(self.revision)
        if not callable(self.score):
            raise ValueError("scorer requires an async callable")


@dataclass(frozen=True, slots=True)
class CheckDetails:
    """Private detached values; presence distinguishes absent output from JSON null."""

    actual_present: bool
    actual: FrozenJson
    expected: FrozenJson


@dataclass(frozen=True, slots=True)
class CheckReport:
    name: str
    path: str
    outcome: CheckOutcome
    step: str | None
    reason_code: str | None = None
    details: CheckDetails | None = None


@dataclass(frozen=True, slots=True)
class CaseDetails:
    """Opt-in private input, authored expectations and complete public result JSON.

    Values are immutable snapshots. A result is absent when invocation or result
    validation failed; exception messages are never retained.
    """

    input: FrozenJson
    expectations: tuple[Expectation, ...]
    result: FrozenJson


@dataclass(frozen=True, slots=True)
class StepReport:
    name: str
    status: str
    elapsed_seconds: float | None
    usage: Usage | None


@dataclass(frozen=True, slots=True)
class CaseReport:
    """One attempt; ``id`` retains source identity, ``repetition`` is one-based.

    ``elapsed_seconds`` measures invocation and output validation, excluding
    assertion scoring. Replay preserves the saved source invocation measurement.
    Group attempts by ``id`` to inspect variation without treating repeat inputs
    as independent new gold cases.
    """

    id: str
    status: str
    checks: tuple[CheckReport, ...]
    steps: tuple[StepReport, ...]
    elapsed_seconds: float
    usage: Usage | None
    workflow: str | None
    workflow_revision: str | None
    error_code: str | None
    details: CaseDetails | None = None
    repetition: int = 1

    @property
    def passed(self) -> bool:
        return all(check.outcome == "passed" for check in self.checks)


@dataclass(frozen=True, slots=True)
class CheckSummary:
    """All declared checks remain in total, including unavailable observations."""

    total: int
    passed: int
    failed: int
    missing: int
    skipped: int
    errors: int

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def coverage(self) -> float:
        return (self.passed + self.failed) / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class StepSummary:
    name: str
    checks: CheckSummary
    observed_cases: int
    skipped_cases: int
    failed_cases: int
    review_cases: int
    latency: LatencySummary = field(default_factory=lambda: summarize_latency(()))
    usage: UsageSummary = field(default_factory=lambda: summarize_usage(()))


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Immutable measurements; serialization is explicit and performs no I/O.

    pass_rate is assertion agreement with authored gold, not model confidence or
    a claim about population accuracy. Failed/review runs may match expected gold.
    ``case_pass_rate`` is all-assertion agreement across attempts; every source has
    ``repeat`` attempts and therefore equal weight. ``source_case_count`` counts
    authored cases; ``len(cases)`` counts attempts, including failures. Suite wall
    ``elapsed_seconds`` includes scheduling/scoring; ``latency`` summarizes source
    invocation measurements. Per-step summaries retain missing observations.
    """

    suite_name: str
    suite_revision: str
    suite_fingerprint: str
    variant_name: str
    variant_revision: str
    configuration_revision: str
    scorer_revisions: tuple[tuple[str, str], ...]
    max_concurrency: int
    timeout: float
    cases: tuple[CaseReport, ...]
    checks: CheckSummary
    steps: tuple[StepSummary, ...]
    case_pass_rate: float
    failure_rate: float
    review_rate: float
    elapsed_seconds: float
    metrics: tuple[MetricReport, ...] = ()
    target_step: str | None = None
    target_workflow: str | None = None
    repeat: int = 1
    source_case_count: int = 0
    latency: LatencySummary | None = None
    usage: UsageSummary | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a fresh JSON-compatible report, including explicit metric denominators."""
        value = cast(dict[str, JsonValue], _json(self))
        value["check_pass_rate"] = self.checks.pass_rate
        value["check_coverage"] = self.checks.coverage
        value["case_count"] = self.source_case_count or len({case.id for case in self.cases})
        value["attempt_count"] = len(self.cases)
        return value


def _json(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json(getattr(value, field.name))
            for field in fields(value)
            if not (field.name == "details" and getattr(value, field.name) is None)
        }
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return cast(JsonValue, value)

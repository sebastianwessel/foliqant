"""Resumable evaluations: checkpoint journal, progress callbacks, identity guard."""

import asyncio
import json
import stat
from pathlib import Path

import pytest
from test_evaluation import result, variant

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import to_execution_result
from foliqant.core.errors import ErrorCode
from foliqant.core.execution import Failure, RunResult, Usage
from foliqant.core.json import freeze_json
from foliqant.evaluation import (
    CheckpointMismatchError,
    EvaluationCase,
    EvaluationCheckpoint,
    EvaluationProgress,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    evaluate,
)


def suite(labels: list[str], name: str = "resume") -> EvaluationSuite:
    return EvaluationSuite(
        name,
        "gold-1",
        tuple(
            EvaluationCase(
                str(index),
                Envelope(payload={"index": index}),
                (Expectation("label", "/payload/label", label),),
            )
            for index, label in enumerate(labels)
        ),
    )


def failed_result():
    return to_execution_result(
        RunResult(
            "failed",
            "inbox",
            "r1",
            "failed",
            freeze_json({}),
            {},
            (),
            Usage(),
            Failure(ErrorCode.RUN_TIMEOUT),
        )
    )


class Pipeline:
    """Counts calls; `fail` / `interrupt` select indices that fail or stop the run."""

    def __init__(self, *, fail=(), interrupt=None):
        self.calls: list[int] = []
        self.fail = set(fail)
        self.interrupt = interrupt

    async def __call__(self, envelope):
        index = envelope.payload["index"]
        self.calls.append(index)
        if index == self.interrupt:
            raise asyncio.CancelledError  # the host interrupts the evaluation
        if index in self.fail:
            return failed_result()
        return result({"label": "a"})


async def test_interrupted_run_resumes_without_repeating_finished_attempts(tmp_path: Path) -> None:
    path = tmp_path / "private" / "checkpoint.jsonl"
    checkpoint = EvaluationCheckpoint(path)
    gold = suite(["a", "a", "b", "a"])
    first = Pipeline(fail={1}, interrupt=3)
    with pytest.raises(asyncio.CancelledError):
        await evaluate(gold, variant(first), checkpoint=checkpoint)
    assert first.calls == [0, 1, 2, 3]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    recorded = [json.loads(line) for line in path.read_text().splitlines()]
    # Completed attempts are recorded as they finish; the failed one is not.
    assert [(item["case"], item["repetition"]) for item in recorded] == [("0", 1), ("2", 1)]

    events: list[EvaluationProgress] = []
    second = Pipeline()
    report = await evaluate(gold, variant(second), checkpoint=checkpoint, progress=events.append)
    assert second.calls == [1, 3]  # the failed and the interrupted attempt run again
    assert report.resumed_attempts == 2
    assert [case.resumed for case in report.cases] == [True, False, True, False]
    # Recorded results are scored against the current gold like any other result.
    assert [case.passed for case in report.cases] == [True, True, False, True]
    assert [(e.completed, e.resumed, e.total) for e in events] == [
        (1, 1, 4),
        (2, 1, 4),
        (3, 2, 4),
        (4, 2, 4),
    ]
    assert events[-1].remaining_seconds == 0

    third = Pipeline()
    revised = await evaluate(suite(["a", "a", "a", "a"]), variant(third), checkpoint=checkpoint)
    assert third.calls == []  # fully resumed, rescored with revised gold
    assert revised.case_pass_rate == 1


async def test_checkpoint_refuses_another_configuration_and_reruns_changed_inputs(
    tmp_path: Path,
) -> None:
    checkpoint = EvaluationCheckpoint(tmp_path / "checkpoint.jsonl")
    await evaluate(suite(["a", "a"]), variant(Pipeline()), checkpoint=checkpoint)

    async def run(envelope):
        return result({"label": "a"})

    other = EvaluationVariant("baseline", "prompt-v2", run, "configuration-v1")
    with pytest.raises(CheckpointMismatchError):
        await evaluate(suite(["a", "a"]), other, checkpoint=checkpoint)
    # Another suite name in the same file is independent.
    await evaluate(suite(["a"], name="other"), other, checkpoint=checkpoint)

    changed = EvaluationSuite(
        "resume",
        "gold-1",
        (
            EvaluationCase(
                "0",
                Envelope(payload={"index": 0, "text": "changed"}),
                (Expectation("label", "/payload/label", "a"),),
            ),
            suite(["a", "a"]).cases[1],
        ),
    )
    pipeline = Pipeline()
    report = await evaluate(changed, variant(pipeline), checkpoint=checkpoint)
    assert pipeline.calls == [0]  # a changed input is never answered from the journal
    assert report.resumed_attempts == 1


async def test_torn_last_line_is_ignored_and_never_extended(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.jsonl"
    checkpoint = EvaluationCheckpoint(path)
    await evaluate(suite(["a"]), variant(Pipeline()), checkpoint=checkpoint)
    with path.open("a") as stream:
        stream.write('{"kind": "evaluation_attempt", "trunc')
    pipeline = Pipeline()
    report = await evaluate(suite(["a", "a"]), variant(pipeline), checkpoint=checkpoint)
    assert pipeline.calls == [1] and report.resumed_attempts == 1
    lines = path.read_text().split("\n")
    assert json.loads(lines[-2])["case"] == "1"  # appended on a fresh line
    path.write_text("not json\n" + path.read_text())
    with pytest.raises(ValueError, match="invalid record"):
        await evaluate(suite(["a"]), variant(Pipeline()), checkpoint=checkpoint)


async def test_checkpoint_never_follows_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        await evaluate(suite(["a"]), variant(Pipeline()), checkpoint=EvaluationCheckpoint(link))
    assert target.read_text() == ""


@pytest.mark.parametrize("kwargs", [{"checkpoint": "path.jsonl"}, {"progress": "not callable"}])
async def test_invalid_checkpoint_and_progress_arguments(kwargs) -> None:
    with pytest.raises(ValueError):
        await evaluate(suite(["a"]), variant(Pipeline()), **kwargs)


async def test_recorded_cases_lists_resumable_case_ids(tmp_path: Path) -> None:
    checkpoint = EvaluationCheckpoint(tmp_path / "checkpoint.jsonl")
    assert checkpoint.recorded_cases("resume", variant(Pipeline())) == frozenset()
    await evaluate(suite(["a", "a", "a"]), variant(Pipeline(fail={1})), checkpoint=checkpoint)
    assert checkpoint.recorded_cases("resume", variant(Pipeline())) == {"0", "2"}

    async def run(envelope):
        return result({})

    with pytest.raises(CheckpointMismatchError):
        checkpoint.recorded_cases("resume", EvaluationVariant("other", "2", run, "c"))

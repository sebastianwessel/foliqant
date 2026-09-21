"""Shared transition tests exercise recovery without any model or storage I/O."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_runner import _DECISION, _HANDLER, accepted, make_plan

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Failure, StepOutcome, StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json
from foliqant.core.machine import ExecutionMachine
from foliqant.core.plan import BindingPlan, WorkflowPlan
from foliqant.core.runner import ExecutionLimits
from foliqant.core.storage import Checkpoint


def machine(
    plan: WorkflowPlan,
    checkpoints: tuple[Checkpoint, ...] = (),
    *,
    limits: ExecutionLimits | None = None,
) -> ExecutionMachine:
    return ExecutionMachine(
        plan,
        accepted(),
        identity=Identity(),
        execution_id="persisted-execution-id",
        limits=limits or ExecutionLimits(),
        checkpoints=checkpoints,
    )


def test_resume_resolves_committed_inputs_and_preserves_chronological_records(
    tmp_path: Path,
) -> None:
    plan = make_plan(
        tmp_path,
        {
            "first": _HANDLER + "next: second\n",
            "second": (
                "type: handler\nhandler: echo\n"
                "input: {prior: {pointer: /steps/first/result/value}}\nnext: done\n"
            ),
            "done": "type: finish\noutcome: completed\n",
        },
        "output: {pointer: /steps/second/result}\n",
    )
    initial = machine(plan)
    assert initial.prepare()[0].name == "first"
    checkpoint = initial.advance(StepOutcome(freeze_json({"value": 7})))
    resumed = machine(plan, (checkpoint,))
    step, inputs = resumed.prepare()
    assert step.name == "second" and inputs["prior"] == 7
    second = resumed.advance(StepOutcome("projected"))
    final_step, inputs = resumed.prepare()
    assert final_step.name == "done" and not inputs
    finish = resumed.advance(None)
    assert resumed.current is None
    usage = Usage(model_requests=3, tokens=TokenUsage())
    result = resumed.result(usage)
    assert result.execution_id == "persisted-execution-id"
    assert result.status == "completed" and result.payload == "projected"
    assert result.usage == usage
    assert tuple(name for name, _ in result.decisions) == ("first", "second", "done")
    assert dict(result.decisions)["first"] == checkpoint.record
    recovered_terminal = machine(plan, (checkpoint, second, finish))
    assert recovered_terminal.current is None
    assert recovered_terminal.result(usage) == result


@pytest.mark.parametrize("review", [False, True])
def test_explicit_finish_status_survives_recovery(tmp_path: Path, review: bool) -> None:
    status = "needs_review" if review else "completed"
    plan = make_plan(tmp_path, {"first": f"type: finish\noutcome: {status}\n"})
    original = machine(plan)
    original.prepare()
    checkpoint = original.advance(None)
    restored = machine(plan, (checkpoint,))
    assert restored.current is None
    assert restored.result(Usage()).status == status
    assert dict(restored.result(Usage()).decisions)["first"] == StepRecord(status, None, True)


def test_implicit_review_recovery_projects_output_and_skips_unselected_branch(
    tmp_path: Path,
) -> None:
    plan = make_plan(
        tmp_path,
        {"first": _DECISION + "next: done\n", "done": "type: finish\noutcome: completed\n"},
        "output: {pointer: /steps/first/result}\n",
    )
    original = machine(plan)
    original.prepare()
    checkpoint = original.advance(StepOutcome("uncertain", needs_review=True))
    restored = machine(plan, (checkpoint,))
    result = restored.result(Usage())
    assert restored.current is None
    assert result.status == "needs_review" and result.payload == "uncertain"
    assert dict(result.decisions)["done"] == StepRecord("skipped")


def test_restore_native_branch_uses_only_authorized_targets(tmp_path: Path) -> None:
    plan = make_plan(
        tmp_path,
        {
            "first": _DECISION + "on_answer: {'true': 'yes', 'false': 'no'}\n",
            "yes": "type: finish\noutcome: completed\n",
            "no": "type: finish\noutcome: completed\n",
        },
    )
    checkpoint = Checkpoint("first", StepRecord("completed", "native result", True), "yes")
    restored = machine(plan, (checkpoint,))
    assert restored.prepare()[0].name == "yes"
    restored.advance(None)
    result = restored.result(Usage())
    assert tuple(name for name, _ in result.decisions) == ("first", "yes", "no")
    assert dict(result.decisions)["no"].status == "skipped"
    for invalid_target in ("first", "unknown", None):
        with pytest.raises(ServiceError) as caught:
            machine(plan, (replace(checkpoint, next_step=invalid_target),))
        assert caught.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "checkpoints",
    [
        (Checkpoint("done", StepRecord("completed", None, True), None),),
        (Checkpoint("first", StepRecord("failed"), "done"),),
        (Checkpoint("first", StepRecord("completed"), "done"),),
        (
            Checkpoint(
                "first", StepRecord("completed", None, True, Failure(ErrorCode.TIMEOUT)), "done"
            ),
        ),
        (Checkpoint("first", StepRecord("completed", None, True), None),),
        (
            Checkpoint("first", StepRecord("completed", None, True), "done"),
            Checkpoint("first", StepRecord("completed", None, True), "done"),
        ),
        (
            Checkpoint("first", StepRecord("completed", None, True), "done"),
            Checkpoint("done", StepRecord("needs_review", None, True), None),
        ),
        (
            Checkpoint("first", StepRecord("completed", None, True), "done"),
            Checkpoint("done", StepRecord("completed", "invalid finish result", True), None),
        ),
    ],
)
def test_restore_rejects_inconsistent_checkpoint_chains(
    tmp_path: Path, checkpoints: tuple[Checkpoint, ...]
) -> None:
    plan = make_plan(
        tmp_path, {"first": _HANDLER + "next: done\n", "done": "type: finish\noutcome: completed\n"}
    )
    with pytest.raises(ServiceError) as caught:
        machine(plan, checkpoints)
    assert caught.value.code == ErrorCode.INVALID_CONFIGURATION


def test_accepted_step_budget_survives_resume(tmp_path: Path) -> None:
    plan = make_plan(
        tmp_path, {"first": _HANDLER + "next: done\n", "done": "type: finish\noutcome: completed\n"}
    )
    checkpoint = Checkpoint("first", StepRecord("completed", None, True), "done")
    restored = machine(plan, (checkpoint,), limits=ExecutionLimits(max_steps=1))
    with pytest.raises(ServiceError) as caught:
        restored.prepare()
    assert caught.value.code == ErrorCode.BUDGET_EXHAUSTED


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_terminal_failure_preserves_input_and_completed_records(
    tmp_path: Path, cancelled: bool, prepared: bool
) -> None:
    plan = make_plan(
        tmp_path,
        {"first": _HANDLER + "next: second\n", "second": _HANDLER},
        "output: {literal: projected}\n",
    )
    checkpoint = Checkpoint("first", StepRecord("completed", "preserved", True), "second")
    restored = machine(plan, (checkpoint,))
    if prepared:
        restored.prepare()
    result = restored.result(Usage(), failure=Failure(ErrorCode.TIMEOUT), cancelled=cancelled)
    assert result.status == ("cancelled" if cancelled else "failed")
    assert result.payload == accepted().payload
    assert result.decisions[0] == (checkpoint.step_id, checkpoint.record)
    failed = dict(result.decisions)["second"]
    assert failed.status == result.status and not failed.has_result
    assert failed.error == result.error


def test_projection_failure_does_not_rewrite_checkpoint(tmp_path: Path) -> None:
    plan = make_plan(tmp_path, {"first": _HANDLER})
    plan = replace(plan, output=BindingPlan("pointer", "/steps/first/result/missing"))
    checkpoint = Checkpoint("first", StepRecord("completed", "preserved", True), None)
    restored = machine(plan, (checkpoint,))
    result = restored.result(Usage())
    assert result.error == Failure(ErrorCode.MISSING_BINDING)
    assert result.payload == accepted().payload
    assert result.decisions == (("first", checkpoint.record),)


def test_invalid_route_fails_active_step_without_committing_partial_result(tmp_path: Path) -> None:
    plan = make_plan(
        tmp_path,
        {
            "first": _DECISION + "on_answer: {'true': 'done', 'false': 'done'}\n",
            "done": "type: finish\noutcome: completed\n",
        },
    )
    running = machine(plan)
    running.prepare()
    with pytest.raises(ServiceError) as caught:
        running.advance(StepOutcome("answer", route_key="unexpected"))
    assert caught.value.code == ErrorCode.INVALID_OUTPUT
    assert running.current == "first"
    result = running.result(Usage(), failure=Failure(caught.value.code))
    assert dict(result.decisions)["first"] == StepRecord("failed", error=result.error)

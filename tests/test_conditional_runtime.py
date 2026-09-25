"""Runtime semantics of routed start, conditional routes, step guards and repeat."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from workflow_documents import (
    IDENTIFIER_INPUT,
    callable_flow,
    compile_document,
    flow,
    handler,
    step,
    when,
)

from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.admission import CapacityLimiter
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import RunResult, StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, thaw_json
from foliqant.core.plan import HandlerStepPlan, WorkflowPlan
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import OperationStep, StepContext

type Handler = Callable[[FrozenObject, StepContext], Awaitable[StepOutcome]]


class Handlers:
    """Dispatch handler steps by name and record every call's context."""

    def __init__(self, **handlers: Handler) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, str, dict[str, Any], StepContext]] = []

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        assert isinstance(step, HandlerStepPlan)
        self.calls.append((context.flow_id, step.name, thaw_json(inputs), context))  # type: ignore[arg-type]
        selected = self.handlers.get(step.handler)
        if selected is None:
            return StepOutcome(None)
        return await selected(inputs, context)


def _runner(plan: WorkflowPlan, executor: Handlers, *, max_steps: int = 32) -> WorkflowRunner:
    return WorkflowRunner(
        plan,
        executor=executor,
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=2, queue_limit=0),
        limits=ExecutionLimits(max_steps=max_steps),
    )


async def _run(
    plan: WorkflowPlan, executor: Handlers, payload: object, **limits: Any
) -> tuple[RunResult, dict[str, Any]]:
    result = await _runner(plan, executor, **limits).run(
        AcceptedEnvelope(payload=payload, metadata={}), identity=Identity()
    )
    public = to_execution_result(result).model_dump(mode="json")
    # Every public result round-trips through its JSON contract unchanged.
    assert ExecutionResult.model_validate_json(json.dumps(public)).model_dump(mode="json") == public
    return result, public


def _flows(result: RunResult) -> dict[str, Any]:
    return dict(result.flows)


# Routed start and conditional transitions ------------------------------------


@pytest.mark.parametrize(
    "payload,selected,index",
    [
        ({"report_type": "monthly"}, "extract", 0),
        ({}, "classify", 1),
        ({"report_type": None}, "classify", 1),
    ],
)
async def test_routed_start_selects_the_first_flow_from_the_envelope(
    tmp_path, payload, selected, index
):
    plan = compile_document(
        tmp_path,
        {"extract": flow(step("e", handler())), "classify": flow(step("c", handler()))},
        start={
            "route": [
                {"when": when("/payload/report_type", present=True), "flow": "extract"},
                {"flow": "classify"},
            ]
        },
    )
    executor = Handlers()
    result, public = await _run(plan, executor, payload)
    assert result.status == "completed"
    assert [call[0] for call in executor.calls] == [selected]
    assert public["start"] == {"flow": selected, "route": {"kind": "route", "index": index}}
    other = "classify" if selected == "extract" else "extract"
    assert public["flows"][other]["status"] == "skipped"
    assert public["flows"][other]["attempt_count"] == 0
    assert public["flows"][selected]["attempt_count"] == 1


def _lookup(status: str) -> Handler:
    async def respond(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": status, "fund": None})

    return respond


def _routing_plan(tmp_path: Path) -> WorkflowPlan:
    return compile_document(
        tmp_path,
        {
            "lookup_fund": flow(
                step("lookup", handler("lookup", identifier="/payload/identifier")),
                input={"identifier": {"pointer": "/payload/identifier"}},
                output={"pointer": "/steps/lookup/result"},
                transition={
                    "route": [
                        {
                            "when": when("/flows/lookup_fund/result/status", equals="found"),
                            "flow": "enrich",
                        },
                        {
                            "when": {
                                "all": [
                                    when("/flows/lookup_fund/result/status", equals="ambiguous"),
                                    when("/payload/strict", equals=True),
                                ]
                            },
                            "outcome": "needs_review",
                        },
                        {"flow": "fallback"},
                    ]
                },
            ),
            "enrich": flow(step("e", handler())),
            "fallback": flow(step("f", handler())),
        },
    )


@pytest.mark.parametrize(
    "status,strict,target,index",
    [
        ("found", False, {"flow": "enrich"}, 0),
        ("ambiguous", True, {"outcome": "needs_review"}, 1),
        ("ambiguous", False, {"flow": "fallback"}, 2),
        ("not_found", True, {"flow": "fallback"}, 2),
    ],
)
async def test_route_selects_the_first_true_entry_and_records_it(
    tmp_path, status, strict, target, index
):
    plan = _routing_plan(tmp_path)
    result, public = await _run(
        plan, Handlers(lookup=_lookup(status)), {"identifier": "LU1", "strict": strict}
    )
    first = public["transitions"][0]
    assert first == {
        "source": "lookup_fund",
        "reason": "completed",
        **target,
        "route": {"kind": "route", "index": index},
    }


async def test_direct_start_is_recorded(tmp_path):
    plan = compile_document(tmp_path, {"main": flow(step("a", handler()))})
    _, public = await _run(plan, Handlers(), {})
    assert public["start"] == {"flow": "main", "route": {"kind": "direct"}}


async def test_cases_record_the_matched_key_and_default_has_none(tmp_path):
    flows = {
        "lookup_fund": flow(
            step("lookup", handler("lookup", identifier="/payload/identifier")),
            input={"identifier": {"pointer": "/payload/identifier"}},
            output={"pointer": "/steps/lookup/result/status"},
            transition={
                "binding": {"pointer": "/flows/lookup_fund/result"},
                "cases": {"found": {"outcome": "completed"}},
                "default": {"outcome": "needs_review"},
                "default_covers": ["not_found", "ambiguous"],
            },
        )
    }
    plan = compile_document(tmp_path, flows)
    _, found = await _run(plan, Handlers(lookup=_lookup("found")), {"identifier": "LU1"})
    assert found["transitions"][0]["route"] == {"kind": "cases", "case": "found"}
    _, missing = await _run(plan, Handlers(lookup=_lookup("not_found")), {"identifier": "LU1"})
    assert missing["transitions"][0]["route"] == {"kind": "cases"}
    assert missing["execution"]["status"] == "needs_review"


async def test_review_route_records_issue_and_route_index(tmp_path):
    async def review(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome(None, needs_review=True, unresolved_issues=("conflicting_information",))

    flows = {
        "main": flow(
            step("check", handler("echo")),
            on_unresolved={
                "route": [
                    {"when": when("/payload/escalate", equals=True), "flow": "escalate"},
                    {"outcome": "needs_review"},
                ]
            },
        ),
        "escalate": flow(step("e", handler("text")), on_unresolved={"outcome": "needs_review"}),
    }
    plan = compile_document(tmp_path, flows)
    _, escalated = await _run(plan, Handlers(echo=review), {"escalate": True})
    assert escalated["transitions"][0]["route"] == {"kind": "review", "index": 0}
    assert escalated["transitions"][0]["flow"] == "escalate"
    _, ended = await _run(plan, Handlers(echo=review), {"escalate": False})
    assert ended["transitions"] == [
        {
            "source": "main",
            "reason": "needs_review",
            "outcome": "needs_review",
            "route": {"kind": "review", "index": 1},
        }
    ]


# Step guards, first_of and object outputs -------------------------------------


async def test_conditional_steps_are_skipped_and_defaults_fill_their_results(tmp_path):
    async def check(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": "invalid" if inputs["value"] == "bad" else "valid"})

    async def repair(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": "valid", "repaired": True})

    flows = {
        "extract_fields": flow(
            step("check", handler("check", value="/payload/value")),
            step(
                "repair",
                handler("repair"),
                when=when("/steps/check/result/status", equals="invalid"),
            ),
            step(
                "recheck",
                handler("recheck", repaired={"pointer": "/steps/repair/result", "default": None}),
                when=when("/steps/repair/result", present=True),
            ),
            input={"value": {"pointer": "/payload/value"}},
            output={
                "fields": {
                    "check": {
                        "first_of": [
                            {"pointer": "/steps/repair/result"},
                            {"pointer": "/steps/check/result"},
                        ]
                    },
                    "repaired": {"pointer": "/steps/repair/result/repaired", "default": False},
                    "input": {"pointer": "/payload/value"},
                }
            },
        )
    }
    plan = compile_document(tmp_path, flows)
    executor = Handlers(check=check, repair=repair)
    result, public = await _run(plan, executor, {"value": "good"})
    steps = public["flows"]["extract_fields"]["steps"]
    assert steps["repair"] == {"status": "skipped"}
    assert steps["recheck"] == {"status": "skipped"}
    assert public["flows"]["extract_fields"]["result"] == {
        "check": {"status": "valid"},
        "repaired": False,
        "input": "good",
    }
    assert [call[1] for call in executor.calls] == ["check"]
    executor = Handlers(check=check, repair=repair)
    result, public = await _run(plan, executor, {"value": "bad"})
    assert [call[1] for call in executor.calls] == ["check", "repair", "recheck"]
    assert executor.calls[2][2] == {"repaired": {"status": "valid", "repaired": True}}
    assert public["flows"]["extract_fields"]["result"]["check"] == {
        "status": "valid",
        "repaired": True,
    }
    # Skipped steps consume no step budget.
    tight = compile_document(tmp_path, flows)
    ok, _ = await _run(tight, Handlers(check=check, repair=repair), {"value": "good"}, max_steps=1)
    assert ok.status == "completed"


async def test_first_of_at_the_boundary_selects_the_branch_that_ran(tmp_path):
    async def label(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"from": context.flow_id})

    flows = {
        "split": flow(
            step("s", handler()),
            transition={
                "route": [
                    {"when": when("/payload/left", equals=True), "flow": "left"},
                    {"flow": "right"},
                ]
            },
        ),
        "left": flow(
            step("l", handler("label")),
            output={"pointer": "/steps/l/result"},
            transition={"flow": "join"},
        ),
        "right": flow(
            step("r", handler("label")),
            output={"pointer": "/steps/r/result"},
            transition={"flow": "join"},
        ),
        "join": flow(
            step("j", handler("pick", chosen="/payload/chosen")),
            input={
                "chosen": {
                    "first_of": [
                        {"pointer": "/flows/left/result"},
                        {"pointer": "/flows/right/result"},
                    ],
                    "default": None,
                }
            },
        ),
    }
    plan = compile_document(tmp_path, flows)
    for left, source in ((True, "left"), (False, "right")):
        executor = Handlers(label=label)
        await _run(plan, executor, {"left": left})
        assert executor.calls[-1][2] == {"chosen": {"from": source}}


async def test_first_of_without_default_fails_when_no_member_is_present(tmp_path):
    flows = {
        "main": flow(
            step("a", handler("echo", value={"first_of": [{"pointer": "/payload/missing"}]})),
        )
    }
    plan = compile_document(tmp_path, flows)
    result, _ = await _run(plan, Handlers(), {})
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.MISSING_BINDING


# Repeat -----------------------------------------------------------------------


def _repeat_plan(
    tmp_path: Path, *, max_attempts: int = 3, retry: bool = True, continue_when: bool = False
) -> WorkflowPlan:
    repeat: dict[str, Any] = {
        "max_attempts": max_attempts,
        "until": when("/flows/lookup_fund/result/status", equals="found"),
    }
    if retry:
        repeat["retry"] = {
            "flow": "correct",
            "input": {
                "lookup": {"pointer": "/flows/lookup_fund/result"},
                "attempts": {"pointer": "/flows/lookup_fund/attempts"},
            },
        }
        if continue_when:
            repeat["retry"]["continue_when"] = when(
                "/flows/correct/result/status", equals="accepted"
            )
        repeat["retry_input"] = {"identifier": {"pointer": "/flows/correct/result/identifier"}}
    flows = {
        "lookup_fund": flow(
            step("lookup", handler("lookup", identifier="/payload/identifier")),
            input={"identifier": {"pointer": "/payload/identifier"}},
            output={"pointer": "/steps/lookup/result"},
            repeat=repeat,
            transition={
                "route": [
                    {
                        "when": when("/flows/lookup_fund/result/status", equals="found"),
                        "flow": "enrich",
                    },
                    {"flow": "manual"},
                ]
            },
            on_unresolved={
                "default": {"flow": "manual"},
                "no_supported_answer": {"flow": "escalate"},
            },
        ),
        "enrich": flow(
            step(
                "enrich",
                handler(
                    "echo",
                    fund="/payload/fund",
                    correction="/payload/correction",
                ),
            ),
            input={
                "fund": {"pointer": "/flows/lookup_fund/result"},
                "correction": (
                    {"pointer": "/flows/correct/result", "default": None}
                    if retry
                    else {"literal": None}
                ),
            },
        ),
        "manual": flow(step("m", handler()), on_unresolved={"outcome": "needs_review"}),
        "escalate": flow(step("x", handler()), on_unresolved={"outcome": "needs_review"}),
    }
    if retry:
        flows["correct"] = callable_flow(
            step(
                "correct",
                handler("correct", lookup="/payload/lookup", attempts="/payload/attempts"),
            ),
            output={"pointer": "/steps/correct/result"},
        )
    return compile_document(tmp_path, flows, max_steps=64)


def _found_on(identifier: str) -> Handler:
    async def respond(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        found = inputs["identifier"] == identifier
        return StepOutcome({"status": "found" if found else "not_found", "fund": None})

    return respond


def _corrector(status: str = "accepted") -> Handler:
    async def respond(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": status, "identifier": "LU0000000002"})

    return respond


async def test_repeat_retries_with_corrected_input_until_found(tmp_path):
    plan = _repeat_plan(tmp_path)
    executor = Handlers(lookup=_found_on("LU0000000002"), correct=_corrector())
    result, public = await _run(plan, executor, {"identifier": "LU0000000001"})
    assert result.status == "completed"
    lookup = public["flows"]["lookup_fund"]
    assert lookup["attempt_count"] == 2
    assert [item["attempt"] for item in lookup["attempts"]] == [1, 2]
    assert [item["result"]["status"] for item in lookup["attempts"]] == ["not_found", "found"]
    assert lookup["result"] == {"status": "found", "fund": None}
    assert lookup["repeat"] == {"stopped_by": "until"}
    assert lookup["attempts_usage"]["model_requests"] == 0
    assert "attempts_elapsed_seconds" in lookup
    correct = public["flows"]["correct"]
    assert correct["attempt_count"] == 1 and correct["result"]["status"] == "accepted"
    assert public["transitions"][0]["flow"] == "enrich"
    calls = [(flow_id, step_id, inputs) for flow_id, step_id, inputs, _ in executor.calls]
    assert calls[0] == ("lookup_fund", "lookup", {"identifier": "LU0000000001"})
    assert calls[1][0:2] == ("correct", "correct")
    assert calls[1][2]["lookup"] == {"status": "not_found", "fund": None}
    assert calls[2] == ("lookup_fund", "lookup", {"identifier": "LU0000000002"})
    assert calls[3][2]["correction"]["identifier"] == "LU0000000002"
    contexts = [context for *_, context in executor.calls]
    assert [(context.attempt, context.flow_role) for context in contexts] == [
        (1, "routed"),
        (1, "retry"),
        (2, "routed"),
        (1, "routed"),
    ]


async def test_retry_input_sees_every_prior_attempt(tmp_path):
    plan = _repeat_plan(tmp_path, max_attempts=3)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector())
    await _run(plan, executor, {"identifier": "LU0000000001"})
    retry_inputs = [inputs for flow_id, _, inputs, _ in executor.calls if flow_id == "correct"]
    assert [len(item["attempts"]) for item in retry_inputs] == [1, 2]
    assert retry_inputs[1]["attempts"][0] == {
        "attempt": 1,
        "status": "completed",
        "result": {"status": "not_found", "fund": None},
    }


async def test_exhausted_repeat_continues_with_the_last_result(tmp_path):
    plan = _repeat_plan(tmp_path, max_attempts=3)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector())
    result, public = await _run(plan, executor, {"identifier": "LU0000000001"})
    lookup = public["flows"]["lookup_fund"]
    assert lookup["attempt_count"] == 3 and lookup["repeat"] == {"stopped_by": "exhausted"}
    assert lookup["status"] == "completed"
    assert public["flows"]["correct"]["attempt_count"] == 2
    assert [item["attempt"] for item in public["flows"]["correct"]["attempts"]] == [1, 2]
    assert public["transitions"][0] == {
        "source": "lookup_fund",
        "reason": "completed",
        "flow": "manual",
        "route": {"kind": "route", "index": 1},
    }
    assert result.status == "completed"


async def test_continue_when_stops_after_a_rejected_correction(tmp_path):
    plan = _repeat_plan(tmp_path, continue_when=True)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector("rejected"))
    _, public = await _run(plan, executor, {"identifier": "LU0000000001"})
    lookup = public["flows"]["lookup_fund"]
    assert lookup["attempt_count"] == 1 and lookup["repeat"] == {"stopped_by": "continue_when"}
    assert public["flows"]["correct"]["result"]["status"] == "rejected"
    assert public["transitions"][0]["flow"] == "manual"


async def test_repeat_without_retry_repeats_the_same_input(tmp_path):
    plan = _repeat_plan(tmp_path, retry=False, max_attempts=3)
    seen: list[str] = []

    async def eventually(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        seen.append(str(inputs["identifier"]))
        return StepOutcome({"status": "found" if len(seen) == 2 else "not_found"})

    _, public = await _run(plan, Handlers(lookup=eventually), {"identifier": "LU1"})
    assert seen == ["LU1", "LU1"]
    assert public["flows"]["lookup_fund"]["repeat"] == {"stopped_by": "until"}
    assert "correct" not in public["flows"]


async def test_review_inside_an_attempt_stops_and_uses_the_review_route(tmp_path):
    plan = _repeat_plan(tmp_path)

    async def review_second(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        if context.attempt == 2:
            return StepOutcome(
                {"status": "ambiguous"},
                needs_review=True,
                unresolved_issues=("no_supported_answer",),
            )
        return StepOutcome({"status": "not_found"})

    _, public = await _run(
        plan, Handlers(lookup=review_second, correct=_corrector()), {"identifier": "LU1"}
    )
    lookup = public["flows"]["lookup_fund"]
    assert lookup["status"] == "needs_review" and lookup["attempt_count"] == 2
    assert lookup["repeat"] == {"stopped_by": "review"}
    assert public["transitions"][0] == {
        "source": "lookup_fund",
        "reason": "needs_review",
        "flow": "escalate",
        "route": {"kind": "review", "case": "no_supported_answer"},
    }


async def test_retry_review_marks_the_repeated_flow_for_review(tmp_path):
    plan = _repeat_plan(tmp_path)

    async def unsure(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome(
            {"status": "rejected", "identifier": ""},
            needs_review=True,
            unresolved_issues=("no_supported_answer",),
        )

    _, public = await _run(
        plan, Handlers(lookup=_found_on("never"), correct=unsure), {"identifier": "LU1"}
    )
    lookup = public["flows"]["lookup_fund"]
    assert lookup["status"] == "needs_review" and lookup["attempt_count"] == 1
    assert lookup["repeat"] == {"stopped_by": "review"}
    assert public["flows"]["correct"]["status"] == "needs_review"
    assert public["transitions"][0]["flow"] == "escalate"


async def test_retry_failure_fails_the_run_and_keeps_every_record(tmp_path):
    plan = _repeat_plan(tmp_path)

    async def broken(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)

    result, public = await _run(
        plan, Handlers(lookup=_found_on("never"), correct=broken), {"identifier": "LU1"}
    )
    assert result.status == "failed"
    assert public["execution"]["error"]["code"] == "dependency_failure"
    assert public["flows"]["lookup_fund"]["repeat"] == {"stopped_by": "failure"}
    assert public["flows"]["correct"]["status"] == "failed"
    assert public["transitions"] == []


async def test_unbindable_retry_input_fails_the_retry_and_keeps_the_last_attempt(tmp_path):
    flows = {
        "lookup_fund": flow(
            step("lookup", handler("lookup", identifier="/payload/identifier")),
            input={"identifier": {"pointer": "/payload/identifier"}},
            output={"pointer": "/steps/lookup/result"},
            repeat={
                "max_attempts": 2,
                "until": when("/flows/lookup_fund/result/status", equals="found"),
                "retry": {
                    "flow": "correct",
                    # `fund` is optional in the lookup output schema.
                    "input": {"fund": {"pointer": "/flows/lookup_fund/result/fund"}},
                },
            },
        ),
        "correct": callable_flow(step("correct", handler("correct", fund="/payload/fund"))),
    }
    plan = compile_document(tmp_path, flows)

    async def without_fund(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": "not_found"})

    executor = Handlers(lookup=without_fund)
    result, public = await _run(plan, executor, {"identifier": "LU1"})
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.MISSING_BINDING
    lookup = public["flows"]["lookup_fund"]
    assert lookup["status"] == "completed" and lookup["attempt_count"] == 1
    assert lookup["steps"]["lookup"]["status"] == "completed"
    assert lookup["result"] == {"status": "not_found"}
    assert lookup["repeat"] == {"stopped_by": "failure"}
    correct = public["flows"]["correct"]
    assert correct["status"] == "failed" and correct["error"]["code"] == "missing_binding"
    assert correct["attempt_count"] == 1 and correct["attempts"][0]["status"] == "failed"
    assert correct["steps"]["correct"]["status"] == "skipped"
    assert [call[0] for call in executor.calls] == ["lookup_fund"]


async def test_unbindable_next_attempt_input_fails_before_the_attempt(tmp_path):
    plan = _repeat_plan(tmp_path)

    async def without_identifier(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome({"status": "accepted"})

    executor = Handlers(lookup=_found_on("never"), correct=without_identifier)
    result, public = await _run(plan, executor, {"identifier": "LU1"})
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.MISSING_BINDING
    lookup = public["flows"]["lookup_fund"]
    # The second attempt never started: the flow keeps its first attempt.
    assert lookup["status"] == "completed" and lookup["attempt_count"] == 1
    assert lookup["steps"]["lookup"]["status"] == "completed"
    assert lookup["repeat"] == {"stopped_by": "failure"}
    assert public["flows"]["correct"]["status"] == "completed"
    assert [call[0] for call in executor.calls] == ["lookup_fund", "correct"]
    assert public["transitions"] == []


async def test_attempts_and_retries_share_the_step_budget(tmp_path):
    plan = _repeat_plan(tmp_path, max_attempts=3)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector())
    # lookup (1), retry (2), then the second attempt exceeds the shared budget.
    result, public = await _run(plan, executor, {"identifier": "LU1"}, max_steps=2)
    assert result.status == "failed" and result.error is not None
    assert result.error.code == ErrorCode.STEP_LIMIT_REACHED
    lookup = public["flows"]["lookup_fund"]
    assert lookup["status"] == "failed" and lookup["attempt_count"] == 2
    assert lookup["attempts"][1]["status"] == "failed"
    assert lookup["repeat"] == {"stopped_by": "failure"}


async def test_isolated_flow_run_executes_one_attempt(tmp_path):
    plan = _repeat_plan(tmp_path)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector())
    result = await _runner(plan, executor).run_flow(
        "lookup_fund",
        AcceptedEnvelope(payload={"identifier": "LU1"}, metadata={}),
        identity=Identity(),
    )
    assert result.status == "completed"
    assert len(executor.calls) == 1
    assert dict(result.flows)["lookup_fund"].attempt_count == 1
    # An isolated flow run selects no start.
    assert result.start is None
    assert "start" not in to_execution_result(result).model_dump(mode="json")


async def test_collection_children_receive_item_and_role(tmp_path):
    flows = {
        "main": flow(
            step(
                "collect",
                {
                    "type": "flow_collection",
                    "items": {
                        "literal": [
                            {"id": "first", "flow": "child", "input": {}},
                            {"id": "second", "flow": "child", "input": {}},
                        ]
                    },
                    "flows": ["child"],
                },
            )
        ),
        "child": callable_flow(step("work", handler())),
    }
    plan = compile_document(tmp_path, flows)
    executor = Handlers()
    _, public = await _run(plan, executor, {})
    assert [(context.collection_item, context.flow_role) for *_, context in executor.calls] == [
        ("first", "callable"),
        ("second", "callable"),
    ]
    items = public["flows"]["main"]["steps"]["collect"]["result"]["items"]
    assert [item["attempt_count"] for item in items] == [1, 1]


# Repeat on callable flows (per collection item) -------------------------------


def _item_repeat_plan(
    tmp_path: Path, *, max_attempts: int = 2, max_steps: int = 64
) -> WorkflowPlan:
    items = [
        {"id": "first", "flow": "lookup_item", "input": {"identifier": "LU1"}},
        {"id": "second", "flow": "lookup_item", "input": {"identifier": "LU0000000002"}},
    ]
    flows = {
        "main": flow(
            step(
                "collect",
                {
                    "type": "flow_collection",
                    "items": {"literal": items},
                    "flows": ["lookup_item"],
                },
            ),
            output={"pointer": "/steps/collect/result"},
        ),
        "lookup_item": {
            **callable_flow(
                step("lookup", handler("lookup", identifier="/payload/identifier")),
                output={"pointer": "/steps/lookup/result"},
                input_schema=IDENTIFIER_INPUT,
            ),
            "repeat": {
                "max_attempts": max_attempts,
                "until": when("/flows/lookup_item/result/status", equals="found"),
                "retry": {
                    "flow": "correct",
                    "input": {
                        "lookup": {"pointer": "/flows/lookup_item/result"},
                        "identifier": {"pointer": "/payload/identifier"},
                    },
                },
                "retry_input": {"identifier": {"pointer": "/flows/correct/result/identifier"}},
            },
        },
        "correct": callable_flow(
            step("correct", handler("correct", lookup="/payload/lookup")),
            output={"pointer": "/steps/correct/result"},
        ),
    }
    return compile_document(tmp_path, flows, max_steps=max_steps)


async def test_collection_items_repeat_until_found(tmp_path):
    plan = _item_repeat_plan(tmp_path)
    executor = Handlers(lookup=_found_on("LU0000000002"), correct=_corrector())
    result, public = await _run(plan, executor, {})
    assert result.status == "completed"
    first, second = public["flows"]["main"]["steps"]["collect"]["result"]["items"]
    assert first["attempt_count"] == 2 and first["repeat"] == {"stopped_by": "until"}
    assert [item["result"]["status"] for item in first["attempts"]] == ["not_found", "found"]
    assert first["retry"]["attempt_count"] == 1
    assert first["retry"]["result"]["identifier"] == "LU0000000002"
    # The second item is found at once: no retry runs for it.
    assert second["attempt_count"] == 1 and second["repeat"] == {"stopped_by": "until"}
    assert "retry" not in second
    # Item retry flows never become workflow-level records.
    assert "correct" not in public["flows"]
    contexts = [
        (flow_id, context.attempt, context.flow_role, context.collection_item)
        for flow_id, _, _, context in executor.calls
    ]
    assert contexts == [
        ("lookup_item", 1, "callable", "first"),
        ("correct", 1, "retry", "first"),
        ("lookup_item", 2, "callable", "first"),
        ("lookup_item", 1, "callable", "second"),
    ]


async def test_collection_items_exhaust_and_keep_the_last_attempt(tmp_path):
    plan = _item_repeat_plan(tmp_path, max_attempts=3)
    executor = Handlers(lookup=_found_on("never"), correct=_corrector())
    result, public = await _run(plan, executor, {})
    assert result.status == "completed"
    first, second = public["flows"]["main"]["steps"]["collect"]["result"]["items"]
    for item in (first, second):
        assert item["status"] == "completed" and item["attempt_count"] == 3
        assert item["repeat"] == {"stopped_by": "exhausted"}
        assert item["retry"]["attempt_count"] == 2
    assert public["flows"]["main"]["steps"]["collect"]["usage"]["tool_calls"] == 0


async def test_collection_item_retry_review_marks_the_item_for_review(tmp_path):
    plan = _item_repeat_plan(tmp_path)

    async def unsure(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        return StepOutcome(
            {"status": "rejected", "identifier": ""},
            needs_review=True,
            unresolved_issues=("no_supported_answer",),
        )

    executor = Handlers(lookup=_found_on("never"), correct=unsure)
    result, public = await _run(plan, executor, {})
    collect = public["flows"]["main"]["steps"]["collect"]
    assert collect["status"] == "needs_review"
    first, second = collect["result"]["items"]
    assert first["status"] == "needs_review" and first["repeat"] == {"stopped_by": "review"}
    assert first["retry"]["status"] == "needs_review"
    # A collection records review and continues with the next item.
    assert second["status"] == "needs_review"
    assert result.status == "needs_review"

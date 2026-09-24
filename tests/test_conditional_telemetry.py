"""Spans, events and log records for routes, skips, repeat attempts and retries."""

import logging
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_conditional_runtime import Handlers, _corrector, _found_on, _repeat_plan
from workflow_documents import compile_document, flow, handler, step, when

from foliqant.adapters.telemetry.logging import LogEvent, LogLabels, SafeJsonFormatter
from foliqant.adapters.telemetry.observation import WorkflowTelemetry
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.execution import to_execution_result
from foliqant.core.admission import CapacityLimiter
from foliqant.core.conditions import describe_condition
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.plan import WorkflowPlan
from foliqant.core.runner import WorkflowRunner


def _labels(plan: WorkflowPlan) -> TelemetryLabels:
    return TelemetryLabels(
        workflows=frozenset({plan.name}),
        flows=frozenset(flow.name for flow in plan.flows),
        steps=frozenset(step.name for flow in plan.flows for step in flow.steps),
        conditions=frozenset(
            describe_condition(step.when)
            for flow in plan.flows
            for step in flow.steps
            if step.when is not None
        ),
    )


async def _traced(
    plan: WorkflowPlan, executor: Handlers, payload: Any, *, conditions: bool = True
) -> tuple[Any, list[Any]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    labels = _labels(plan)
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    runner = WorkflowRunner(
        plan,
        executor=executor,
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        observer=WorkflowTelemetry(provider, labels=labels, conditions=conditions),
    )
    result = await runner.run(AcceptedEnvelope(payload=payload, metadata={}), identity=Identity())
    spans = list(exporter.get_finished_spans())
    provider.shutdown()
    return result, spans


def _events(span: Any, name: str) -> list[dict[str, Any]]:
    return [dict(event.attributes) for event in span.events if event.name == name]


async def test_repeat_attempts_retry_role_and_stop_are_traced(tmp_path):
    plan = _repeat_plan(tmp_path)
    executor = Handlers(lookup=_found_on("LU0000000002"), correct=_corrector())
    result, spans = await _traced(plan, executor, {"identifier": "PRIVATE-ID"})
    assert result.status == "completed"
    flows = [span for span in spans if span.instrumentation_scope.name == "foliqant.flow"]
    attempts = [
        span for span in flows if span.attributes.get("foliqant.flow.name") == "lookup_fund"
    ]
    assert [span.attributes["foliqant.flow.attempt"] for span in attempts] == [1, 2]
    assert {span.attributes["foliqant.flow.max_attempts"] for span in attempts} == {3}
    assert {span.attributes["foliqant.flow.role"] for span in attempts} == {"routed"}
    retry = next(span for span in flows if span.attributes.get("foliqant.flow.name") == "correct")
    assert retry.attributes["foliqant.flow.role"] == "retry"
    # Span names read as the configuration: the attempt suffix starts at the second run.
    assert [span.name for span in attempts] == ["flow lookup_fund", "flow lookup_fund #2"]
    assert retry.name == "flow correct"
    assert {span.name for span in spans if span.instrumentation_scope.name == "foliqant.step"} >= {
        "step lookup (handler)",
        "step correct (handler)",
    }
    # The retry runs between the first and the second attempt, inside the first.
    assert retry.parent.span_id == attempts[0].context.span_id
    assert _events(attempts[0], "repeat.stopped") == []
    assert _events(attempts[1], "repeat.stopped") == [{"stopped_by": "until"}]
    assert _events(attempts[1], "route.selected") == [
        {"kind": "route", "index": 0, "target": "enrich"}
    ]
    root = next(span for span in spans if span.instrumentation_scope.name == "foliqant.workflow")
    assert root.name == f"workflow {plan.name}"
    assert _events(root, "route.selected") == [{"kind": "direct", "target": "lookup_fund"}]
    assert result.start is not None and result.start.flow == "lookup_fund"
    evaluated = _events(attempts[0], "condition.evaluated")
    assert {"location": "repeat.until", "result": False} in evaluated
    assert _events(attempts[1], "condition.evaluated")[0] == {
        "location": "repeat.until",
        "result": True,
    }
    execution = root.attributes["foliqant.execution.id"]
    assert execution == result.execution_id
    assert all(span.attributes.get("foliqant.execution.id") == execution for span in spans)
    assert result.trace == (f"{root.context.trace_id:032x}", f"{root.context.span_id:016x}")
    assert "PRIVATE" not in "".join(span.to_json() for span in spans)
    assert "LU0000000002" not in "".join(span.to_json() for span in spans)


async def test_a_failure_before_the_next_attempt_stops_on_the_last_attempt_span(tmp_path):
    plan = _repeat_plan(tmp_path)

    async def without_identifier(inputs: Any, context: Any) -> StepOutcome:
        return StepOutcome({"status": "accepted"})

    executor = Handlers(lookup=_found_on("never"), correct=without_identifier)
    result, spans = await _traced(plan, executor, {"identifier": "LU1"})
    assert result.status == "failed"
    attempts = [
        span
        for span in spans
        if span.instrumentation_scope.name == "foliqant.flow"
        and span.attributes.get("foliqant.flow.name") == "lookup_fund"
    ]
    assert len(attempts) == 1
    assert _events(attempts[0], "repeat.stopped") == [{"stopped_by": "failure"}]


@pytest.mark.parametrize("payload,selected,index", [({"left": True}, "left", 0), ({}, "right", 1)])
async def test_routed_start_result_and_event_agree(tmp_path, payload, selected, index):
    plan = compile_document(
        tmp_path,
        {"left": flow(step("l", handler())), "right": flow(step("r", handler()))},
        start={
            "route": [
                {"when": when("/payload/left", equals=True), "flow": "left"},
                {"flow": "right"},
            ]
        },
    )
    result, spans = await _traced(plan, Handlers(), payload)
    root = next(span for span in spans if span.instrumentation_scope.name == "foliqant.workflow")
    public = to_execution_result(result).model_dump(mode="json")
    assert public["start"] == {"flow": selected, "route": {"kind": "route", "index": index}}
    assert _events(root, "route.selected") == [
        {"kind": "route", "index": index, "target": selected}
    ]


async def test_skipped_steps_record_a_span_and_condensed_condition(tmp_path):
    guard = when("/steps/check/result/status", equals="PRIVATE-OPERAND")
    plan = compile_document(
        tmp_path,
        {"main": flow(step("check", handler("check")), step("repair", handler(), when=guard))},
    )

    async def check(inputs, context):
        return StepOutcome({"status": "valid"})

    result, spans = await _traced(plan, Handlers(check=check), {})
    assert result.status == "completed"
    repair = next(
        span
        for span in spans
        if span.instrumentation_scope.name == "foliqant.step"
        and span.attributes.get("foliqant.step.name") == "repair"
    )
    assert repair.attributes["foliqant.step.skipped"] is True
    assert repair.attributes["foliqant.outcome"] == "skipped"
    assert _events(repair, "step.skipped") == [{"condition": "/steps/check/result/status equals"}]
    assert _events(repair, "condition.evaluated") == [
        {"location": "steps.repair.when", "result": False}
    ]
    assert "PRIVATE-OPERAND" not in "".join(span.to_json() for span in spans)


async def test_condition_events_are_opt_in_and_type_mismatches_are_reported(tmp_path):
    guard = when("/steps/check/result/value", gt=3)
    plan = compile_document(
        tmp_path,
        {"main": flow(step("check", handler("check")), step("next", handler(), when=guard))},
    )

    async def check(inputs, context):
        return StepOutcome({"value": "not-a-number"})

    _, quiet = await _traced(plan, Handlers(check=check), {}, conditions=False)
    names = {event.name for span in quiet for event in span.events}
    assert "condition.evaluated" not in names
    mismatch = [
        dict(event.attributes)
        for span in quiet
        for event in span.events
        if event.name == "condition.type_mismatch"
    ]
    assert mismatch == [
        {"location": "steps.next.when", "operator": "gt", "reason": "incompatible_type"}
    ]


async def test_overlong_match_values_are_false_and_reported_without_content(tmp_path):
    guard = when("/steps/check/result/value", matches="[a-z]+")
    plan = compile_document(
        tmp_path,
        {"main": flow(step("check", handler("check")), step("next", handler(), when=guard))},
    )

    async def check(inputs, context):
        return StepOutcome({"value": "private" * 200})

    executor = Handlers(check=check)
    result, spans = await _traced(plan, executor, {})
    assert result.status == "completed"
    assert [call[1] for call in executor.calls] == ["check"]
    mismatch = [
        dict(event.attributes)
        for span in spans
        for event in span.events
        if event.name == "condition.type_mismatch"
    ]
    assert mismatch == [
        {"location": "steps.next.when", "operator": "matches", "reason": "value_too_long"}
    ]
    assert "privateprivate" not in "".join(span.to_json() for span in spans)


async def test_handler_review_issues_are_traced(tmp_path):
    plan = compile_document(
        tmp_path,
        {"main": flow(step("check", handler("check")), on_unresolved={"outcome": "needs_review"})},
    )

    async def review(inputs, context):
        return StepOutcome(None, needs_review=True, unresolved_issues=("no_supported_answer",))

    _, spans = await _traced(plan, Handlers(check=review), {})
    check = next(span for span in spans if span.instrumentation_scope.name == "foliqant.step")
    assert _events(check, "handler.review") == [{"issues": ("no_supported_answer",)}]
    flow_span = next(span for span in spans if span.instrumentation_scope.name == "foliqant.flow")
    assert _events(flow_span, "route.selected") == [{"kind": "review", "target": "needs_review"}]


async def test_step_context_trace_is_the_step_span_carrier(tmp_path):
    plan = compile_document(tmp_path, {"main": flow(step("check", handler("check")))})
    carriers = []

    async def check(inputs, context):
        carriers.append(dict(context.trace))
        return StepOutcome(None)

    _, spans = await _traced(plan, Handlers(check=check), {})
    step_span = next(span for span in spans if span.instrumentation_scope.name == "foliqant.step")
    assert carriers[0]["traceparent"].split("-")[2] == f"{step_span.context.span_id:016x}"


@pytest.mark.parametrize(
    "event,fields,expected",
    [
        (
            LogEvent.ROUTE_SELECTED,
            {"route_kind": "route", "target": "enrich", "attempt": 2},
            {"route_kind": "route", "target": "enrich", "attempt": 2},
        ),
        (LogEvent.REPEAT_STOPPED, {"stopped_by": "exhausted"}, {"stopped_by": "exhausted"}),
        (
            LogEvent.HANDLER_REVIEW,
            {"issues": ("no_supported_answer", "PRIVATE")},
            {},
        ),
        (
            LogEvent.HANDLER_REVIEW,
            {"issues": ("no_supported_answer",)},
            {"issues": "no_supported_answer"},
        ),
        (
            LogEvent.CONDITION_TYPE_MISMATCH,
            {"operator": "gt", "location": "steps.next.when"},
            {"operator": "gt", "location": "steps.next.when"},
        ),
        (
            LogEvent.CONDITION_TYPE_MISMATCH,
            {"operator": "matches", "reason": "value_too_long"},
            {"operator": "matches", "reason": "value_too_long"},
        ),
        (LogEvent.CONDITION_TYPE_MISMATCH, {"reason": "PRIVATE-REASON"}, {}),
        (LogEvent.ROUTE_SELECTED, {"target": "PRIVATE-TARGET"}, {}),
        (
            LogEvent.STEP_SKIPPED,
            {"execution_id": "00000000-0000-4000-8000-000000000000"},
            {"execution_id": "00000000-0000-4000-8000-000000000000"},
        ),
    ],
)
def test_log_formatter_accepts_only_allowlisted_event_fields(event, fields, expected):
    import json

    from foliqant.adapters.telemetry.logging import emit_event

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("foliqant.test.conditional")
    handler_ = Capture()
    logger.addHandler(handler_)
    logger.setLevel(logging.INFO)
    try:
        emit_event(logger, event, **fields)
    finally:
        logger.removeHandler(handler_)
    formatted = json.loads(
        SafeJsonFormatter(LogLabels(flows=frozenset({"enrich"}))).format(records[0])
    )
    formatted.pop("level")
    assert formatted.pop("event") == event.value
    assert formatted == expected

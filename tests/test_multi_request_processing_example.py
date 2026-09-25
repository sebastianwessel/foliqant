"""Business policy remains explicit while core owns bounded child execution."""

import json
from pathlib import Path
from typing import Any

import pytest
from examples.multi_request_processing.evaluate import run_evaluations
from examples.multi_request_processing.policy import PlanningInput, plan_requests
from examples.multi_request_processing.run import DEMO_PAYLOAD, run_example

from foliqant.contracts.execution import FlowCollectionResult


def assessment(
    *units: dict[str, Any], relations: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "assessment": {
            "questionId": "identify",
            "type": "request_units",
            "answerability": {"status": "answerable", "issues": []},
            "answer": {"units": list(units), "relations": relations or []},
            "reason": "The supplied message establishes the requests.",
            "evidence_strength": "strong",
        },
        "language": "en",
    }


def unit(
    id: str,
    category: str = "request_status",
    subject: str | None = "FOI-2026-0142",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": id,
        "categoryId": category,
        "subject": subject,
        "status": status,
        "description": "Requested action.",
    }


def test_duplicate_and_repeated_category_policy() -> None:
    plan = plan_requests(
        PlanningInput.model_validate(
            assessment(unit("one"), unit("duplicate"), unit("two", subject="FOI-2026-0310"))
        )
    )
    assert plan.request_ids == {"task_1": "one", "task_2": "two"}
    assert [item.flow for item in plan.items] == ["lookup_status", "lookup_status"]
    assert [(item.id, item.reason) for item in plan.ignored] == [("duplicate", "duplicate")]
    assert len(plan.assessment.answer.units) == 3  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["withdrawn", "quoted"])
def test_historical_requests_do_not_spawn_work(status: str) -> None:
    plan = plan_requests(PlanningInput.model_validate(assessment(unit("old", status=status))))
    assert plan.items == []
    assert plan.ignored[0].reason == status


def test_condition_and_missing_reference_are_held_but_independent_guidance_proceeds() -> None:
    plan = plan_requests(
        PlanningInput.model_validate(
            assessment(
                unit("conditional", status="conditional"),
                unit("missing", subject=None),
                unit("guide", "guidance", None),
            )
        )
    )
    assert plan.request_ids == {"task_1": "guide"}
    assert [item.reason for item in plan.held] == ["conditional_request", "missing_reference"]


def test_partial_assessment_does_not_authorize_individual_units() -> None:
    value = assessment(unit("one"))
    value["assessment"]["answerability"] = {
        "status": "partially_answerable",
        "issues": ["no_supported_answer"],
    }
    plan = plan_requests(PlanningInput.model_validate(value))
    assert plan.items == []
    assert plan.held[0].reason == "incomplete_assessment"
    assert plan.assessment.evidence_strength == "strong"


def test_explicit_dependency_is_not_split_into_independent_tasks() -> None:
    value = assessment(
        unit("lookup"),
        unit("guide", "guidance", None),
        relations=[{"type": "requires", "requestId": "guide", "requiredRequestId": "lookup"}],
    )
    plan = plan_requests(PlanningInput.model_validate(value))
    assert plan.items == []
    assert plan.held[0].reason == "related_requests"
    assert plan.assessment.answer is not None
    assert len(plan.assessment.answer.relations) == 1


def test_application_limit_holds_excess_requests_instead_of_dropping_them() -> None:
    value = assessment(*(unit(f"request_{i}", subject=f"FOI-2026-{i:04d}") for i in range(10)))
    plan = plan_requests(PlanningInput.model_validate(value))
    assert len(plan.items) == 8
    assert [(item.id, item.reason) for item in plan.held] == [
        ("request_8", "too_many_requests"),
        ("request_9", "too_many_requests"),
    ]


async def test_realistic_pipeline_uses_model_loop_within_parent_collection() -> None:
    result = await run_example(DEMO_PAYLOAD)
    assert result.execution.status == "completed"
    assert result.execution.usage.model_requests == 3
    assert result.execution.usage.tool_calls == 1
    assert set(result.flows) == {"assess", "plan", "process", "finalize"}
    step = result.flows["process"].steps["requests"]
    assert step.kind == "flow_collection"
    ledger = FlowCollectionResult.model_validate(step.result)
    assert [(item.id, item.flow, item.status) for item in ledger.items] == [
        ("task_1", "lookup_status", "completed"),
        ("task_2", "prepare_guidance", "completed"),
    ]
    assert result.payload == {
        "disposition": "ready",
        "prepared": ["status", "guide"],
        "review": [],
        "ignored": [],
    }
    assert "message" not in json.dumps(ledger.model_dump())


async def test_all_gold_scopes_and_nested_measurements(tmp_path: Path) -> None:
    output = tmp_path / "multi-request.json"
    summary = await run_evaluations(output=output)
    assert summary["ok"] is True
    assert summary["suites"] == 13
    assert summary["cases"] == 33
    report = json.loads(output.read_text())["reports"][0]
    repeated = next(case for case in report["cases"] if case["id"] == "same_category_twice")
    lookups = [step for step in repeated["steps"] if step["flow"] == "lookup_status"]
    assert len(lookups) == 2
    assert len({step["invocation_path"] for step in lookups}) == 2


def test_model_request_ids_are_not_used_as_framework_identifiers() -> None:
    plan = plan_requests(PlanningInput.model_validate(assessment(unit("Request-1"))))
    assert plan.items[0].id == "task_1"
    assert plan.request_ids == {"task_1": "Request-1"}
    assert plan.assessment.answer is not None
    assert plan.assessment.answer.units[0].id == "Request-1"


def test_explain_distinguishes_callable_flows_without_disclosing_items() -> None:
    from examples.multi_request_processing.policy import HANDLERS
    from examples.multi_request_processing.run import CONFIG_PATH

    from foliqant import explain, prepare_application

    prepared = prepare_application(CONFIG_PATH, handlers=HANDLERS)
    report = explain(prepared, "intake").to_json()
    flows = {flow["id"]: flow for flow in report["flows"]}  # type: ignore[index, union-attr]
    assert flows["lookup_status"]["callable"] is True
    assert "transition" not in flows["lookup_status"]
    assert flows["process"]["steps"] == [
        {
            "id": "requests",
            "type": "flow_collection",
            "items": {"pointer": "/payload/items"},
            "flows": ["lookup_status", "prepare_guidance"],
            "max_items": 8,
        }
    ]
    assert "FOI-2026" not in json.dumps(report)

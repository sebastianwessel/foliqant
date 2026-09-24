"""The conditional intake example: conditions, repeat and declared handlers offline."""

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from examples.conditional_intake.evaluate import run_evaluations
from examples.conditional_intake.run import CONFIG_PATH, run_example

from foliqant import explain, prepare_application
from foliqant.core.plan import ConditionalRoutingPlan, MatchRoutingPlan

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_uses_every_conditional_feature_without_diagnostics() -> None:
    prepared = prepare_application(CONFIG_PATH)
    assert prepared.diagnostics == ()
    assert set(prepared.handler_contracts) == {"check_reference", "open_review"}
    plan = prepared.plans["account_intake"]
    assert isinstance(plan.start, ConditionalRoutingPlan)
    assert isinstance(plan.flow("classify").transition, MatchRoutingPlan)
    assert isinstance(plan.flow("lookup").transition, ConditionalRoutingPlan)
    repeat = plan.flow("lookup").repeat
    assert repeat is not None and repeat.retry_flow == "correct"
    extract = plan.flow("extract")
    assert [step.name for step in extract.steps if step.when is not None] == [
        "extract",
        "repair",
        "recheck",
    ]
    assert all(flow.on_unresolved_inherited for flow in plan.flows if flow.name == "lookup")
    graph = explain(prepared).to_json()
    assert {edge["kind"] for edge in graph["edges"]} >= {"start", "transition", "review", "retry"}


def test_validate_and_explain_need_no_handler_registration() -> None:
    # Regression: `evaluate --check` rejected gold paths into retry flows.
    for arguments in (
        ["validate", "--strict"],
        ["explain", "--format", "dot"],
        ["evaluate", "--check"],
    ):
        completed = subprocess.run(
            [sys.executable, "-m", "foliqant", *arguments, "--config", str(CONFIG_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


async def test_unknown_account_is_retried_with_the_corrected_reference() -> None:
    result = (
        await run_example(
            {
                "message": (
                    "Please review invoice INV-9 for account A-120; my previous account was A-100."
                )
            }
        )
    ).model_dump(mode="json")
    assert result["execution"]["status"] == "completed"
    lookup = result["flows"]["lookup"]
    assert lookup["attempt_count"] == 2 and lookup["repeat"] == {"stopped_by": "until"}
    assert [item["result"]["plan"] for item in lookup["attempts"]] == ["Unknown", "Basic"]
    assert result["flows"]["correct"]["result"] == {
        "status": "corrected",
        "account_reference": "A-100",
    }
    assert result["payload"] == {
        "queue": "billing",
        "account": {"account_reference": "A-100", "plan": "Basic", "renewal_date": "2026-12-01"},
        "review": None,
    }
    assert result["execution"]["usage"]["tool_calls"] == 2
    assert [transition["route"] for transition in result["transitions"]] == [
        {"kind": "cases", "case": "billing"},
        {"kind": "route", "index": 0},
        {"kind": "route", "index": 0},
    ]


async def test_form_request_skips_classification_and_extraction() -> None:
    result = await run_example(
        {
            "message": "Invoice INV-7 was charged twice.",
            "form": {"request_type": "billing", "account_reference": "A-100"},
        }
    )
    assert result.execution.status == "completed"
    assert result.flows["classify"].status == "skipped"
    assert result.flows["extract"].steps["extract"].status == "skipped"
    assert result.flows["extract"].steps["repair"].status == "skipped"
    assert result.execution.usage.model_requests == 0


@pytest.mark.parametrize(
    ("message", "reason", "stopped_by"),
    [
        ("Please cancel renewal for account A-999.", "account_unknown", "continue_when"),
        ("Please help with my account.", "unclassified", None),
    ],
)
async def test_reviews_follow_the_workflow_default(
    message: str, reason: str, stopped_by: str | None
) -> None:
    result = (await run_example({"message": message})).model_dump(mode="json")
    assert result["execution"]["status"] == "needs_review"
    assert result["payload"]["review"] == {"reason": reason}
    assert result["flows"]["lookup"].get("repeat", {}).get("stopped_by") == stopped_by


async def test_synthetic_gold_passes(tmp_path: Path) -> None:
    summary = await run_evaluations(output=tmp_path / "report.json")
    assert summary["ok"] is True
    assert summary["cases"] == 7
    reports = json.loads(Path(cast(str, summary["report"])).read_text())["reports"]
    assert [report["target_flow"] for report in reports] == [None, "extract"]

"""Exercise the authored support tutorial without a model endpoint."""

import re
from pathlib import Path
from typing import Any, cast

import pytest
from examples.support_email_tutorial.evaluate import run_evaluations
from examples.support_email_tutorial.run import run_example, run_multi_example

from foliqant import prepare_application

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("message", "queue", "reference", "plan", "skipped"),
    [
        (
            "Please review the duplicate charge on invoice INV-7 for account A-100.",
            "billing",
            "A-100",
            "Basic",
            "cancellation",
        ),
        ("Please cancel renewal for account A-200.", "cancellation", "A-200", "Plus", "billing"),
    ],
)
async def test_completed_support_email_projects_host_payload(
    message: str, queue: str, reference: str, plan: str, skipped: str
) -> None:
    result = (await run_example({"message": message})).model_dump(mode="json")
    assert result["execution"]["status"] == "completed"
    assert result["payload"]["queue"] == queue
    assert result["payload"]["account_reference"] == reference
    assert result["payload"]["reply"]
    assert result["flows"][queue]["steps"]["lookup"]["result"]["plan"] == plan
    assert result["flows"][skipped]["status"] == "skipped"
    assert result["execution"]["usage"]["tool_calls"] == 1


@pytest.mark.parametrize(
    ("message", "issue"),
    [
        ("Please help with my account.", "no_supported_answer"),
        (
            "Please review the duplicate charge on invoice INV-7 for account A-100. "
            "Please cancel renewal for account A-200.",
            "multiple_valid_options",
        ),
    ],
)
async def test_unsupported_or_ambiguous_email_needs_review_without_tool(
    message: str, issue: str
) -> None:
    result = (await run_example({"message": message})).model_dump(mode="json")
    assert result["execution"]["status"] == "needs_review"
    assert result["payload"] == {"disposition": "needs_review"}
    assert result["flows"]["classify"]["steps"]["classify"]["result"]["answerability"][
        "issues"
    ] == [issue]
    assert result["execution"]["usage"]["tool_calls"] == 0
    assert result["flows"]["billing"]["status"] == "skipped"
    assert result["flows"]["cancellation"]["status"] == "skipped"


async def test_missing_account_reference_stops_before_lookup() -> None:
    result = (await run_example({"message": "Please review invoice INV-7."})).model_dump(
        mode="json"
    )
    assert result["execution"]["status"] == "needs_review"
    assert result["payload"] == {"disposition": "needs_review"}
    assert result["flows"]["billing"]["steps"]["extract"]["result"]["account_reference"] is None
    assert result["flows"]["billing"]["steps"]["require_reference"]["status"] == "needs_review"
    assert result["flows"]["billing"]["steps"]["lookup"]["status"] == "skipped"
    assert result["execution"]["usage"]["tool_calls"] == 0


async def test_agent_loop_uses_one_read_only_tool() -> None:
    from examples.support_email_tutorial.run import run_demo

    result = cast(dict[str, Any], await run_demo(agent=True))
    assert result["execution"]["status"] == "completed"
    assert result["execution"]["usage"]["model_requests"] == 2
    assert result["execution"]["usage"]["tool_calls"] == 1
    assert result["payload"]["reply"]


async def test_synthetic_evaluation_checks_eight_cases(tmp_path: Path) -> None:
    summary = await run_evaluations(output=tmp_path / "report.json")
    assert summary["ok"] is True
    assert summary["cases"] == 8
    assert summary["passed_checks"] == summary["total_checks"] == 34
    assert (tmp_path / "report.json").is_file()


async def test_multi_request_prepares_two_ordered_read_only_tasks() -> None:
    result = (
        await run_multi_example(
            {
                "message": (
                    "Review invoice INV-7 for account A-100 and cancel renewal for account A-200."
                )
            }
        )
    ).model_dump(mode="json")
    assert result["execution"]["status"] == "completed"
    assert result["payload"]["disposition"] == "ready"
    assert result["payload"]["prepared"] == ["billing_request", "cancellation_request"]
    items = result["flows"]["process"]["result"]["items"]
    assert [item["flow"] for item in items] == ["billing_task", "cancellation_task"]
    assert [item["result"]["next_step"] for item in items] == [
        "review_invoice",
        "review_cancellation",
    ]
    assert result["execution"]["usage"]["tool_calls"] == 2


async def test_multi_request_holds_missing_account_for_partial_review() -> None:
    result = (
        await run_multi_example(
            {
                "message": (
                    "Review invoice INV-7 for account A-100 and cancel renewal "
                    "for my other account."
                )
            }
        )
    ).model_dump(mode="json")
    assert result["execution"]["status"] == "needs_review"
    assert result["payload"]["disposition"] == "partial_review"
    assert result["payload"]["prepared"] == ["billing_request"]
    assert result["payload"]["review"] == ["cancellation_request"]
    assert result["flows"]["plan"]["result"]["held"][0]["reason"] == ("missing_account_reference")
    assert result["execution"]["usage"]["tool_calls"] == 1


async def test_distinct_invoices_on_one_account_remain_separate_tasks() -> None:
    result = (
        await run_multi_example(
            {"message": "Review invoice INV-7 and invoice INV-8 for account A-100."}
        )
    ).model_dump(mode="json")
    assert result["execution"]["status"] == "completed"
    assert result["payload"]["prepared"] == ["billing_request", "second_billing_request"]
    items = result["flows"]["plan"]["result"]["items"]
    assert [item["flow"] for item in items] == ["billing_task", "billing_task"]
    assert [item["input"]["account_reference"] for item in items] == ["A-100", "A-100"]
    assert result["execution"]["usage"]["tool_calls"] == 2


def test_first_chapter_snippets_compile_offline(tmp_path: Path) -> None:
    """Keep the setup and first decision chapter buildable from their code blocks."""
    setup = (ROOT / "docs/tutorials/setup.md").read_text()
    chapter = (ROOT / "docs/tutorials/decision-basics.md").read_text()
    settings = re.findall(r"```yaml\n(.*?)\n```", setup, re.DOTALL)[0]
    input_schema = re.findall(r"```json\n(.*?)\n```", chapter, re.DOTALL)[0]
    workflow, flow = re.findall(r"```yaml\n(.*?)\n```", chapter, re.DOTALL)[:2]
    decision = re.findall(r"```markdown\n(.*?)\n```", chapter, re.DOTALL)[0]
    config = tmp_path / "config"
    classify = config / "support_email" / "classify"
    classify.mkdir(parents=True)
    (config / "settings.yaml").write_text(settings)
    (config / "support_email" / "input.schema.json").write_text(input_schema)
    (config / "support_email" / "workflow.yaml").write_text(workflow)
    (classify / "flow.yaml").write_text(flow)
    (classify / "classify.step.md").write_text(decision)
    prepared = prepare_application(config / "settings.yaml")
    assert "support_email" in prepared.plans

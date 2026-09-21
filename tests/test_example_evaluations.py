"""Every runnable example has measured gold checks, not just a demo response."""

import json

import pytest
from examples.http_workflow.evaluate import run_evaluations as http_evaluations
from examples.public_request_mcp.evaluate import run_evaluations as mcp_evaluations
from examples.support_triage import evaluate as support_evaluation

from foliqant.evaluation import EvaluationCase, EvaluationSuite, Expectation


async def test_mcp_pipeline_and_isolated_step_evaluations() -> None:
    result = await mcp_evaluations()
    assert result["ok"] is True
    assert result["mode"] == "local_stdio"
    assert isinstance(result["reports"], list)
    assert len(result["reports"]) == 2


async def test_http_boundary_reuses_gold_suite_and_real_workflow() -> None:
    result = await http_evaluations()
    assert result["ok"] is True
    assert result["mode"] == "offline_asgi"
    assert isinstance(result["reports"], list)
    report = result["reports"][0]
    assert isinstance(report, dict)
    assert report["case_count"] == 3
    assert report["review_rate"] == pytest.approx(1 / 3)


def test_example_evaluation_fails_when_gold_disagrees(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = support_evaluation.suite()
    case = original.cases[0]
    # Alter independently authored expectations, never the scripted response.
    wrong = EvaluationCase(
        case.id,
        case.envelope(),
        case.expectations
        + (Expectation("deliberate_mismatch", "/payload/account_reference", "wrong-account"),),
    )
    suite = EvaluationSuite(original.name, "negative-control", (wrong, *original.cases[1:]))
    monkeypatch.setattr(support_evaluation, "suite", lambda: suite)
    monkeypatch.setattr("sys.argv", ["evaluate"])
    assert support_evaluation.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["reports"][0]["checks"]["failed"] == 1

"""Every runnable example has measured gold checks, not just a demo response."""

import json
from pathlib import Path

import pytest
from examples.http_workflow.evaluate import run_evaluations as http_evaluations
from examples.public_request_mcp.evaluate import run_evaluations as mcp_evaluations
from examples.support_triage import evaluate as support_evaluation

from foliqant.evaluation import EvaluationCase, EvaluationSuite, Expectation


async def test_mcp_pipeline_and_isolated_step_evaluations(tmp_path) -> None:
    result = await mcp_evaluations(output=tmp_path / "mcp.json")
    assert result["ok"] is True
    assert result["mode"] == "local_stdio"
    assert result["suites"] == 2
    assert len(json.loads(Path(result["report"]).read_text())["reports"]) == 2


async def test_http_boundary_reuses_gold_suite_and_real_workflow(tmp_path) -> None:
    result = await http_evaluations(output=tmp_path / "http.json")
    assert result["ok"] is True
    assert result["mode"] == "offline_asgi"
    report = json.loads(Path(result["report"]).read_text())["reports"][0]
    assert isinstance(report, dict)
    assert report["case_count"] == 9
    assert report["review_rate"] == pytest.approx(1 / 3)


def test_example_evaluation_fails_when_gold_disagrees(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
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
    artifact = tmp_path / "negative.json"
    monkeypatch.setattr("sys.argv", ["evaluate", "--output", str(artifact)])
    assert support_evaluation.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["passed_checks"] == output["total_checks"] - 1
    assert output["report"] == str(artifact)
    saved = json.loads(artifact.read_text())["reports"][0]["cases"][0]["checks"][-1]
    assert saved["reason_code"] == "mismatch"
    assert saved["details"]["actual"] == "C-1049"
    assert saved["details"]["expected"] == "wrong-account"
    assert "wrong-account" not in json.dumps(output)


async def test_support_reports_include_matrices_and_isolated_step_details(tmp_path) -> None:
    artifact = tmp_path / "support.json"
    result = await support_evaluation.run_evaluations(output=artifact)
    assert result["ok"] is True
    assert "details" not in json.dumps(result)
    reports = json.loads(artifact.read_text())["reports"]
    pipeline, classification, extraction = reports
    queue, status = pipeline["metrics"]
    assert queue["support"] == queue["observed"] == 6
    assert queue["excluded"] == 3  # Review gold does not invent a queue label.
    assert queue["confusion_matrix"] == [[2, 0, 0], [0, 2, 0], [0, 0, 2]]
    assert queue["accuracy"] == queue["coverage"] == 1
    assert status["support"] == 9
    for report, step in ((classification, "classify"), (extraction, "extract")):
        assert report["target_step"] == step
        assert [entry["name"] for entry in report["steps"]] == [step]
        for case in report["cases"]:
            assert case["details"]["input"]["payload"]["message"]
            assert list(case["details"]["result"]["decisions"]) == [step]
            assert all(check["step"] == step for check in case["checks"])
            assert all(check["details"]["actual_present"] for check in case["checks"])

    for case in pipeline["cases"]:
        details = case["details"]
        message = details["input"]["payload"]["message"]
        assert details["result"]["metadata"] == details["input"]["metadata"]
        decision = details["result"]["decisions"]["classify"]["result"]
        citations = (
            decision["explanation"]["evidence"] + decision["explanation"]["contraryEvidence"]
        )
        assert all(item["sourceId"] == "message" and item["quote"] in message for item in citations)
        if case["status"] == "completed":
            action = details["result"]["payload"]["requested_action"]
            assert action in message  # The extraction contract is source-language extractive.


async def test_http_metrics_reuse_shared_golden_catalog(tmp_path) -> None:
    result = await http_evaluations(output=tmp_path / "http.json")
    metric = json.loads(Path(result["report"]).read_text())["reports"][0]["metrics"][0]
    assert metric["labels"] == ["cancellation", "billing_dispute", "service_change"]
    assert metric["confusion_matrix"] == [[2, 0, 0], [0, 2, 0], [0, 0, 2]]
    assert metric["support"] == 6 and metric["excluded"] == 3


def test_synthetic_gold_covers_catalog_languages_and_independent_step_inputs(monkeypatch) -> None:
    from collections import Counter

    def forbidden(*args, **kwargs):
        pytest.fail("gold must never be derived from a scripted response")

    monkeypatch.setattr(support_evaluation.offline, "scripted_response", forbidden)
    dataset = support_evaluation.dataset()
    pipeline, classification, extraction = dataset.suites
    assert [len(spec.gold_cases) for spec in dataset.suites] == [9, 9, 6]
    assert Counter(
        case.input.metadata.model_dump()["language"] for case in pipeline.gold_cases
    ) == {"en": 7, "de": 2}
    category_path = "/decisions/classify/result/answer/optionId"
    expected_categories = {
        check.expected
        for case in pipeline.gold_cases
        for check in case.expectations
        if check.path == category_path
    }
    assert expected_categories == set(pipeline.metrics[0].labels)
    review_ids = {
        "insufficient_information",
        "multiple_active_intents",
        "unresolved_contradiction",
    }
    assert {case.id for case in extraction.gold_cases} == {
        case.id for case in pipeline.gold_cases if case.id not in review_ids
    }
    issue_path = "/decisions/classify/result/answerability/issues"
    authored_issues = {
        case.id: next(check.expected for check in case.expectations if check.path == issue_path)
        for case in pipeline.gold_cases
        if case.id in review_ids
    }
    assert authored_issues == {
        "insufficient_information": ["missing_information"],
        "multiple_active_intents": ["multiple_valid_options"],
        "unresolved_contradiction": ["conflicting_information"],
    }
    for step in (classification, extraction):
        for isolated in step.gold_cases:
            original = next(case for case in pipeline.gold_cases if case.id == isolated.id)
            assert isolated.input.payload == {"message": original.input.payload["message"]}
            assert isolated.input.metadata == original.input.metadata


@pytest.mark.parametrize("example", ["support_triage", "http_workflow", "public_request_mcp"])
def test_export_dataset_is_explicit_valid_private_and_never_runs(
    example,
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    import importlib
    import stat

    import jsonschema
    from examples.common import ROOT

    from foliqant.evaluation.dataset import EvaluationDataset

    module = importlib.import_module(f"examples.{example}.evaluate")
    path = tmp_path / "private" / f"{example}.json"

    async def unexpected(**kwargs):
        pytest.fail("dataset export must not execute a workflow")

    monkeypatch.setattr(module, "run_evaluations", unexpected)
    monkeypatch.setattr("sys.argv", ["evaluate", "--write-dataset", str(path)])
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    exported = json.loads(path.read_text())
    loaded = EvaluationDataset.model_validate(exported, strict=True)
    assert loaded == module.dataset()
    schema = json.loads(
        (ROOT / "schemas/foliqant/runtime/evaluation-dataset.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(exported)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    assert module.main() == 1
    assert path.read_bytes() == before
    capsys.readouterr()


@pytest.mark.parametrize(
    "run", [support_evaluation.run_evaluations, http_evaluations, mcp_evaluations]
)
async def test_examples_write_shared_full_report_artifact(run, tmp_path) -> None:
    import stat

    path = tmp_path / "report.json"
    result = await run(output=path)
    saved = json.loads(path.read_text())
    assert saved["version"] == 1
    assert saved["mode"] == result["mode"]
    assert result["report"] == str(path)
    assert "details" not in json.dumps(result)
    assert result["suites"] == len(saved["reports"])
    assert result["attempts"] == sum(report["attempt_count"] for report in saved["reports"])
    assert result["total_checks"] == sum(report["checks"]["total"] for report in saved["reports"])
    assert saved["dataset"]["name"]
    assert saved["dataset"]["revision"]
    assert saved["reports"][0]["cases"][0]["details"]["result"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_exported_dataset_and_report_replay_through_shared_cli(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    import asyncio
    import shutil

    import yaml
    from examples.common import write_example_dataset

    from foliqant.cli import main

    project = tmp_path / "support"
    shutil.copytree(
        support_evaluation.CONFIG_PATH.parent, project, ignore=shutil.ignore_patterns("__pycache__")
    )
    config_path = project / "foliqant.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["evaluation"]["dataset"] = "private/gold.json"
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(support_evaluation, "CONFIG_PATH", config_path)
    await write_example_dataset(support_evaluation.dataset(), project / "private/gold.json")
    saved = tmp_path / "observed.json"
    await support_evaluation.run_evaluations(output=saved)

    async def forbidden(*args, **kwargs):
        pytest.fail("replay must never open model clients")

    monkeypatch.setattr("foliqant.bootstrap.open_application", forbidden)
    assert await asyncio.to_thread(main, ["evaluate", "--config", str(config_path), "--check"]) == 0
    capsys.readouterr()
    rescored = tmp_path / "rescored.json"
    assert (
        await asyncio.to_thread(
            main,
            [
                "evaluate",
                "--config",
                str(config_path),
                "--replay",
                str(saved),
                "--output",
                str(rescored),
            ],
        )
        == 0
    )
    assert json.loads(rescored.read_text())["reports"][0]["check_pass_rate"] == 1
    capsys.readouterr()

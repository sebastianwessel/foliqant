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
    assert report["case_count"] == 16
    assert report["review_rate"] == pytest.approx(10 / 16)


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
    queue, status, effective, origin, issues, evidence = pipeline["metrics"]
    assert queue["support"] == queue["observed"] == 6
    assert queue["excluded"] == 10  # Review gold does not invent a queue label.
    assert queue["confusion_matrix"] == [[2, 0, 0], [0, 2, 0], [0, 0, 2]]
    assert queue["accuracy"] == queue["coverage"] == 1
    assert status["support"] == 16
    assert effective["support"] == effective["observed"] == 14
    assert effective["excluded"] == 2  # Conflicting/multiple intents are not coerced to misc.
    assert effective["confusion_matrix"][-1] == [0, 0, 0, 8]
    assert origin["confusion_matrix"] == [[6, 0], [0, 8]]
    assert issues["support"] == 16
    assert issues["labels"] == [
        "no_supported_answer",
        "conflicting_information",
        "multiple_valid_options",
    ]
    assert issues["accuracy"] == 1
    assert evidence["labels"] == ["limited", "strong", None]
    assert evidence["support"] == evidence["observed"] == 16
    assert evidence["confusion_matrix"] == [[0, 0, 0], [0, 6, 0], [0, 0, 10]]
    classify_summary = next(step for step in pipeline["steps"] if step["name"] == "classify")
    assert classify_summary["model_selected_cases"] == 6
    assert classify_summary["fallback_selected_cases"] == 8
    assert classify_summary["fallback_rate"] == pytest.approx(8 / 16)
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
        assert decision["reason"].strip() and len(decision["reason"]) <= 400
        assert "explanation" not in decision
        assert decision["evidence_strength"] == (
            "strong" if case["status"] == "completed" else None
        )
        step_result = details["result"]["decisions"]["classify"]
        if case["id"] in {"multiple_active_intents", "unresolved_contradiction"}:
            assert "selection" not in step_result
        elif case["status"] == "needs_review":
            assert step_result["selection"]["origin"] == "fallback"
            assert step_result["selection"]["category"]["id"] == "misc"
            assert decision["answer"] is None
            assert decision["answerability"]["status"] == "not_answerable"
        if case["status"] == "completed":
            action = details["result"]["payload"]["requested_action"]
            assert action in message  # The extraction contract is source-language extractive.


async def test_http_metrics_reuse_shared_golden_catalog(tmp_path) -> None:
    result = await http_evaluations(output=tmp_path / "http.json")
    metric = json.loads(Path(result["report"]).read_text())["reports"][0]["metrics"][0]
    assert metric["labels"] == ["cancellation", "billing_dispute", "service_change"]
    assert metric["confusion_matrix"] == [[2, 0, 0], [0, 2, 0], [0, 0, 2]]
    assert metric["support"] == 6 and metric["excluded"] == 10


def test_synthetic_gold_covers_catalog_languages_and_independent_step_inputs(monkeypatch) -> None:
    from collections import Counter

    def forbidden(*args, **kwargs):
        pytest.fail("gold must never be derived from a scripted response")

    monkeypatch.setattr(support_evaluation.offline, "scripted_response", forbidden)
    dataset = support_evaluation.dataset()
    pipeline, classification, extraction = dataset.suites
    assert [len(spec.gold_cases) for spec in dataset.suites] == [16, 16, 6]
    assert Counter(
        case.input.metadata.model_dump()["language"] for case in pipeline.gold_cases
    ) == {"en": 11, "de": 5}
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
        "out_of_catalog",
        "german_out_of_catalog",
        "german_insufficient_information",
        "category_words_without_request",
        "german_category_words_without_request",
        "withdrawn_request",
        "missing_referent",
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
        "insufficient_information": ["no_supported_answer"],
        "multiple_active_intents": ["multiple_valid_options"],
        "unresolved_contradiction": ["conflicting_information"],
        "out_of_catalog": ["no_supported_answer"],
        "german_out_of_catalog": ["no_supported_answer"],
        "german_insufficient_information": ["no_supported_answer"],
        "category_words_without_request": ["no_supported_answer"],
        "german_category_words_without_request": ["no_supported_answer"],
        "withdrawn_request": ["no_supported_answer"],
        "missing_referent": ["no_supported_answer"],
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
    await support_evaluation.run_evaluations(output=saved, repeat=2)

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
    original_reports = json.loads(saved.read_text())["reports"]
    replay_reports = json.loads(rescored.read_text())["reports"]
    assert replay_reports[0]["check_pass_rate"] == 1
    for original, replay in zip(original_reports, replay_reports, strict=True):
        assert replay["repeat"] == 2
        assert replay["steps"] == original["steps"]
        assert replay["metrics"] == original["metrics"]
        for before, after in zip(original["cases"], replay["cases"], strict=True):
            assert after["steps"] == before["steps"]
            assert (
                after["details"]["result"]["decisions"] == before["details"]["result"]["decisions"]
            )
    classify = next(step for step in replay_reports[0]["steps"] if step["name"] == "classify")
    assert classify["model_selected_cases"] == 12
    assert classify["fallback_selected_cases"] == 16
    assert classify["observed_cases"] == 32
    assert classify["fallback_rate"] == pytest.approx(16 / 32)
    capsys.readouterr()

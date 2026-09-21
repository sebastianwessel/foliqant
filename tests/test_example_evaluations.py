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
    assert output["reports"][0]["checks"]["failed"] == 1
    mismatch = output["reports"][0]["cases"][0]["checks"][-1]
    assert mismatch["reason_code"] == "mismatch"
    assert "details" not in mismatch
    saved = json.loads(artifact.read_text())["reports"][0]["cases"][0]["checks"][-1]
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
    assert queue["support"] == queue["observed"] == 2
    assert queue["excluded"] == 1  # Review gold does not invent a queue label.
    assert queue["confusion_matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 0]]
    assert queue["accuracy"] == queue["coverage"] == 1
    assert status["support"] == 3
    for report, step in ((classification, "classify"), (extraction, "extract")):
        assert report["target_step"] == step
        assert [entry["name"] for entry in report["steps"]] == [step]
        for case in report["cases"]:
            assert case["details"]["input"]["payload"]["message"]
            assert list(case["details"]["result"]["decisions"]) == [step]
            assert all(check["step"] == step for check in case["checks"])
            assert all(check["details"]["actual_present"] for check in case["checks"])


async def test_http_metrics_reuse_shared_golden_catalog() -> None:
    result = await http_evaluations()
    metric = result["reports"][0]["metrics"][0]
    assert metric["labels"] == ["cancellation", "billing_dispute", "service_change"]
    assert metric["confusion_matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 0]]
    assert metric["support"] == 2 and metric["excluded"] == 1


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
    assert "details" not in json.dumps(result)
    for detailed, summary in zip(saved["reports"], result["reports"], strict=True):
        assert detailed["metrics"] == summary["metrics"]
        assert detailed["checks"] == summary["checks"]
        assert detailed["suite_fingerprint"] == summary["suite_fingerprint"]
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

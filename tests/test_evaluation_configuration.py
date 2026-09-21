"""Private ground truth is an evaluation input, never a runtime prerequisite."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from foliqant import Envelope, open_application, prepare_application
from foliqant.cli import main
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.evaluation.command import evaluate_configuration
from foliqant.evaluation.dataset import EvaluationDataset, load_dataset, read_json


def _project(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    workflow = tmp_path / "demo"
    workflow.mkdir()
    (workflow / "workflow.yaml").write_text(
        "version: 1\nname: demo\nstart: done\nsteps:\n"
        "  done:\n    type: finish\n    outcome: completed\n",
        encoding="utf-8",
    )
    config = tmp_path / "foliqant.yaml"
    config.write_text(
        "version: 1\nworkflows:\n  demo: demo\nevaluation:\n  dataset: .foliqant/gold.json\n",
        encoding="utf-8",
    )
    gold = tmp_path / ".foliqant/gold.json"
    gold.parent.mkdir()
    data: dict[str, Any] = {
        "version": 1,
        "name": "private-demo",
        "revision": "gold-1",
        "suites": [
            {
                "name": "pipeline",
                "workflow": "demo",
                "metrics": [
                    {
                        "name": "category",
                        "path": "/payload/category",
                        "kind": "classification",
                        "labels": ["billing", "cancellation"],
                    }
                ],
                "cases": [
                    {
                        "id": "case-1",
                        "input": {"payload": {"category": "billing", "private": "private-email"}},
                        "expectations": [
                            {"name": "category", "path": "/payload/category", "expected": "billing"}
                        ],
                    },
                    {
                        "id": "case-2",
                        "input": {"payload": {"category": "cancellation"}},
                        "expectations": [
                            {"name": "category", "path": "/payload/category", "expected": "billing"}
                        ],
                    },
                ],
            }
        ],
    }
    gold.write_text(json.dumps(data), encoding="utf-8")
    return config, gold, data


async def test_startup_does_not_require_gold_and_reference_does_not_revise_runtime(
    tmp_path: Path,
) -> None:
    config, gold, _ = _project(tmp_path)
    configured = prepare_application(config)
    gold.unlink()
    async with open_application(configured, environment={}) as app:
        result = await app.run("demo", Envelope(payload={"value": "works"}))
    assert result.execution.status == "completed"
    document = yaml.safe_load(config.read_text())
    document["evaluation"]["dataset"] = "/not/deployed/gold.json"
    config.write_text(yaml.safe_dump(document))
    relocated = prepare_application(config)
    del document["evaluation"]
    config.write_text(yaml.safe_dump(document))
    absent = prepare_application(config)
    assert (
        configured.configuration_digest
        == relocated.configuration_digest
        == absent.configuration_digest
    )
    assert configured.plans["demo"].revision == absent.plans["demo"].revision


async def test_check_never_opens_application_and_rejects_missing_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, gold, _ = _project(tmp_path)

    def no_clients(*args: object, **kwargs: object) -> None:
        pytest.fail("evaluation check must not open application clients")

    monkeypatch.setattr("foliqant.bootstrap.open_application", no_clients)
    prepared = prepare_application(config)
    summary, code = await evaluate_configuration(prepared, check=True)
    assert code == 0 and summary["cases"] == 2
    assert not (tmp_path / ".foliqant/evaluations").exists()
    gold.unlink()
    with pytest.raises(ServiceError) as failure:
        await evaluate_configuration(prepared, check=True)
    assert failure.value.code is ErrorCode.INVALID_INPUT


async def test_report_records_confusion_full_values_and_replays_without_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, gold, data = _project(tmp_path)
    prepared = prepare_application(config)
    output = tmp_path / ".foliqant/report.json"
    summary, code = await evaluate_configuration(prepared, output=output)
    assert code == 1 and summary["status"] == "failed"
    assert "private-email" not in json.dumps(summary)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    artifact = json.loads(output.read_text())
    report = artifact["reports"][0]
    assert report["metrics"][0]["confusion_matrix"] == [[1, 1], [0, 0]]
    assert report["cases"][0]["details"]["result"]["payload"]["private"] == "private-email"

    def no_clients(*args: object, **kwargs: object) -> None:
        pytest.fail("replay must not open application clients")

    monkeypatch.setattr("foliqant.bootstrap.open_application", no_clients)
    # Correct gold only; original saved response and source report stay unchanged.
    original = output.read_bytes()
    data["revision"] = "gold-2"
    data["suites"][0]["cases"][1]["expectations"][0]["expected"] = "cancellation"
    gold.write_text(json.dumps(data))
    replay_output = tmp_path / ".foliqant/replayed.json"
    replay_summary, replay_code = await evaluate_configuration(
        prepared, replay=output, output=replay_output, max_concurrency=2
    )
    assert replay_code == 0 and replay_summary["mode"] == "replay"
    assert output.read_bytes() == original
    assert json.loads(replay_output.read_text())["dataset"]["revision"] == "gold-2"


@pytest.mark.parametrize(
    "change", ["input", "configuration", "target", "order", "missing_result", "identity_type"]
)
async def test_replay_rejects_misaligned_artifacts(tmp_path: Path, change: str) -> None:
    config, gold, data = _project(tmp_path)
    prepared = prepare_application(config)
    output = tmp_path / "saved.json"
    await evaluate_configuration(prepared, output=output)
    artifact = json.loads(output.read_text())
    report = artifact["reports"][0]
    if change == "input":
        data["suites"][0]["cases"][0]["input"]["payload"]["private"] = "different"
        gold.write_text(json.dumps(data))
    elif change == "configuration":
        report["configuration_revision"] = "different"
    elif change == "target":
        report["target_step"] = "done"
    elif change == "order":
        report["cases"].reverse()
    elif change == "identity_type":
        artifact["version"] = True
    else:
        report["cases"][0]["details"]["result"] = None
    output.write_text(json.dumps(artifact))
    with pytest.raises(ServiceError) as failure:
        await evaluate_configuration(prepared, replay=output)
    assert failure.value.code is ErrorCode.INVALID_INPUT


async def test_existing_output_is_never_overwritten_or_runs_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, _ = _project(tmp_path)
    output = tmp_path / "existing.json"
    output.write_text("keep")

    def no_clients(*args: object, **kwargs: object) -> None:
        pytest.fail("existing output must fail before execution")

    monkeypatch.setattr("foliqant.bootstrap.open_application", no_clients)
    with pytest.raises(ServiceError) as failure:
        await evaluate_configuration(prepare_application(config), output=output)
    assert failure.value.code is ErrorCode.CONFLICT
    assert output.read_text() == "keep"


@pytest.mark.parametrize(
    "change",
    [
        "workflow",
        "step",
        "pointer",
        "decision",
        "execution",
        "gold",
        "duplicate_case",
        "duplicate_suite",
        "metric",
    ],
)
def test_invalid_gold_or_targets_rejected_offline(tmp_path: Path, change: str) -> None:
    config, gold, data = _project(tmp_path)
    suite = data["suites"][0]
    expectation = suite["cases"][0]["expectations"][0]
    if change in {"workflow", "step"}:
        suite[change] = "missing"
    elif change == "pointer":
        expectation["path"] = "/output/category"
    elif change == "decision":
        expectation["path"] = "/decisions/missing/result"
    elif change == "execution":
        expectation["path"] = "/execution/nonsense"
    elif change == "gold":
        expectation["expected"] = "unknown_label"
    elif change == "duplicate_case":
        suite["cases"].append(suite["cases"][0])
    elif change == "duplicate_suite":
        data["suites"].append(suite)
    else:
        suite["metrics"][0]["labels"] = ["billing", "billing"]
    gold.write_text(json.dumps(data))
    with pytest.raises((ValueError, ServiceError)):
        load_dataset(prepare_application(config))


@pytest.mark.parametrize(
    "content", ['{"version":1,"version":1}', '{"value":NaN}', '{"value":Infinity}']
)
def test_json_reader_rejects_ambiguous_data(tmp_path: Path, content: str) -> None:
    path = tmp_path / "gold.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_json(path)


def test_dataset_requires_explicit_null_gold_and_closed_fields(tmp_path: Path) -> None:
    _, _, data = _project(tmp_path)
    expectation = data["suites"][0]["cases"][0]["expectations"][0]
    del expectation["expected"]
    with pytest.raises(ValueError):
        EvaluationDataset.model_validate(data)


def test_cli_evaluate_check_and_mismatch_have_safe_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _, _ = _project(tmp_path)
    assert main(["evaluate", "--config", str(config), "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    assert main(["evaluate", "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "private-email" not in captured.out
    assert json.loads(captured.out)["total_checks"] == 2
    assert main(["evaluate", "--config", str(config), "--max-concurrency", "0"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_input"


async def test_mixed_inline_and_separate_case_files_resolve_from_manifest(tmp_path: Path) -> None:
    config, gold, data = _project(tmp_path)
    case_file = gold.parent / "pipeline.json"
    case_file.write_text(json.dumps(data["suites"][0]["cases"]))
    inline = dict(data["suites"][0], name="isolated", step="done")
    data["suites"][0]["cases"] = "pipeline.json"
    data["suites"].append(inline)
    gold.write_text(json.dumps(data))
    prepared = prepare_application(config)
    loaded = load_dataset(prepared)
    assert len(loaded.suites[0].gold_cases) == 2
    assert len(loaded.suites[1].gold_cases) == 2
    summary, code = await evaluate_configuration(prepared, check=True)
    assert code == 0 and summary["cases"] == 4
    case_file.unlink()
    assert prepare_application(config).configuration_digest == prepared.configuration_digest
    with pytest.raises(ServiceError):
        await evaluate_configuration(prepared, check=True)


@pytest.mark.parametrize("nested", ["another.json", {"cases": []}, []])
def test_case_references_require_nonempty_arrays_not_nested_references(
    tmp_path: Path, nested: object
) -> None:
    config, gold, data = _project(tmp_path)
    case_file = gold.parent / "cases.json"
    case_file.write_text(json.dumps(nested))
    data["suites"][0]["cases"] = str(case_file)
    gold.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_dataset(prepare_application(config))


async def test_replay_rejects_different_workflow_even_all_invocations_failed(
    tmp_path: Path,
) -> None:
    from foliqant.evaluation import EvaluationVariant, evaluate
    from foliqant.evaluation.artifact import write_report

    config, gold, data = _project(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "workflow.yaml").write_text(
        (tmp_path / "demo/workflow.yaml").read_text().replace("name: demo", "name: other")
    )
    configuration = yaml.safe_load(config.read_text())
    configuration["workflows"]["other"] = "other"
    config.write_text(yaml.safe_dump(configuration))
    prepared = prepare_application(config)
    dataset = load_dataset(prepared)

    async def failed(envelope: Envelope) -> Any:
        raise TimeoutError

    report = await evaluate(
        dataset.to_suite(dataset.suites[0]),
        EvaluationVariant("failure", "1", failed, prepared.configuration_digest, workflow="demo"),
        include_details=True,
    )
    output = tmp_path / "failed.json"
    write_report(
        output,
        [report],
        mode="execution",
        dataset_name=dataset.name,
        dataset_revision=dataset.revision,
    )
    summary, code = await evaluate_configuration(prepared, replay=output)
    assert code == 4
    assert summary["status"] == "error"
    data["suites"][0]["workflow"] = "other"
    gold.write_text(json.dumps(data))
    with pytest.raises(ServiceError) as failure:
        await evaluate_configuration(prepared, replay=output)
    assert failure.value.code is ErrorCode.INVALID_INPUT


async def test_artifact_handles_valid_surrogate_json_and_bounds_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, gold, data = _project(tmp_path)
    data["suites"][0]["cases"][0]["input"]["payload"]["private"] = "\ud800"
    gold.write_text(json.dumps(data))
    prepared = prepare_application(config)
    output = tmp_path / "unicode.json"
    await evaluate_configuration(prepared, output=output)
    assert (
        json.loads(output.read_text())["reports"][0]["cases"][0]["details"]["input"]["payload"][
            "private"
        ]
        == "\ud800"
    )
    _, code = await evaluate_configuration(prepared, replay=output)
    assert code == 1
    monkeypatch.setattr("foliqant.evaluation.artifact.MAX_REPORT_BYTES", 8)
    rejected = tmp_path / "oversized.json"
    with pytest.raises(ValueError):
        await evaluate_configuration(prepared, output=rejected)
    assert not rejected.exists()
    assert not list(tmp_path.glob(".evaluation-*"))

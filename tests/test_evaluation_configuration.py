"""Private ground truth is an evaluation input, never a runtime prerequisite."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from handler_contracts import declare

from foliqant import Envelope, open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.cli import main
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.json import freeze_json
from foliqant.evaluation.command import evaluate_configuration
from foliqant.evaluation.dataset import EvaluationDataset, load_dataset, read_dataset, read_json


async def _passthrough(inputs, context):
    del context
    return StepOutcome(inputs["document"])


_HANDLERS = {
    "passthrough": HandlerRegistration(
        _passthrough,
        freeze_json(
            {
                "type": "object",
                "properties": {"document": {"type": "object"}},
                "required": ["document"],
                "additionalProperties": False,
            }
        ),
        freeze_json({"type": "object"}),
    )
}


def _prepare(path: Path):
    return prepare_application(declare(path, _HANDLERS), handlers=_HANDLERS)


def _project(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    workflow = tmp_path / "demo"
    workflow.mkdir()
    (workflow / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "start": "main",
                "output": {"pointer": "/flows/main/result"},
                "flows": {
                    "main": {
                        "input": {"document": {"pointer": "/payload"}},
                        "definition": {
                            "output": {"pointer": "/steps/done/result"},
                            "steps": [
                                {
                                    "id": "done",
                                    "definition": {
                                        "type": "handler",
                                        "handler": "passthrough",
                                        "input": {"document": {"pointer": "/payload/document"}},
                                    },
                                }
                            ],
                        },
                        "transition": {"outcome": "completed"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "foliqant.yaml"
    config.write_text(
        "workflows:\n  demo: demo\nevaluation:\n  dataset: .foliqant/gold.json\n",
        encoding="utf-8",
    )
    gold = tmp_path / ".foliqant/gold.json"
    gold.parent.mkdir()
    data: dict[str, Any] = {
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
    configured = _prepare(config)
    gold.unlink()
    async with open_application(configured, environment={}) as app:
        result = await app.run("demo", Envelope(payload={"value": "works"}))
    assert result.execution.status == "completed"
    document = yaml.safe_load(config.read_text())
    document["evaluation"]["dataset"] = "/not/deployed/gold.json"
    config.write_text(yaml.safe_dump(document))
    relocated = _prepare(config)
    del document["evaluation"]
    config.write_text(yaml.safe_dump(document))
    absent = _prepare(config)
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
    prepared = _prepare(config)
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
    prepared = _prepare(config)
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


async def test_repeat_report_replay_consumes_each_saved_observation_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, _ = _project(tmp_path)
    prepared = _prepare(config)
    output = tmp_path / ".foliqant/repeated.json"
    summary, _ = await evaluate_configuration(prepared, output=output, repeat=2)
    saved = json.loads(output.read_text())
    source = saved["reports"][0]
    assert summary["attempts"] == 4
    assert source["repeat"] == 2
    assert [(case["id"], case["repetition"]) for case in source["cases"]] == [
        ("case-1", 1),
        ("case-1", 2),
        ("case-2", 1),
        ("case-2", 2),
    ]

    def no_clients(*args: object, **kwargs: object) -> None:
        pytest.fail("repeat replay must not open application clients")

    monkeypatch.setattr("foliqant.bootstrap.open_application", no_clients)
    replayed = tmp_path / ".foliqant/repeated-replay.json"
    replay_summary, _ = await evaluate_configuration(prepared, replay=output, output=replayed)
    replay = json.loads(replayed.read_text())["reports"][0]
    assert replay_summary["repeat"] == 2 and replay_summary["attempts"] == 4
    assert [case["elapsed_seconds"] for case in replay["cases"]] == [
        case["elapsed_seconds"] for case in source["cases"]
    ]
    assert replay["latency"] == source["latency"]

    with pytest.raises(ServiceError) as failure:
        await evaluate_configuration(prepared, replay=output, repeat=1)
    assert failure.value.code is ErrorCode.INVALID_INPUT


async def test_source_span_report_replays_through_shared_scorer(tmp_path: Path) -> None:
    config, gold, data = _project(tmp_path)
    private = data["suites"][0]["cases"][0]["input"]["payload"]["private"]
    data["suites"][0]["cases"][0]["expectations"].append(
        {
            "name": "private_source_span",
            "path": "/payload/private",
            "expected": {
                "input_path": "/payload/private",
                "required": [0, len(private)],
                "allowed": [0, len(private)],
            },
            "comparison": "source_span",
        }
    )
    gold.write_text(json.dumps(data))
    prepared = _prepare(config)
    observed = tmp_path / ".foliqant/source-span.json"
    await evaluate_configuration(prepared, output=observed)
    source_check = json.loads(observed.read_text())["reports"][0]["cases"][0]["checks"][-1]
    assert source_check["outcome"] == "passed"

    replayed = tmp_path / ".foliqant/source-span-replay.json"
    await evaluate_configuration(prepared, replay=observed, output=replayed)
    replay_check = json.loads(replayed.read_text())["reports"][0]["cases"][0]["checks"][-1]
    assert replay_check["outcome"] == "passed"


@pytest.mark.parametrize(
    "change", ["input", "configuration", "target", "order", "missing_result", "unknown_field"]
)
async def test_replay_rejects_misaligned_artifacts(tmp_path: Path, change: str) -> None:
    config, gold, data = _project(tmp_path)
    prepared = _prepare(config)
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
    elif change == "unknown_field":
        artifact["unexpected"] = True
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
        await evaluate_configuration(_prepare(config), output=output)
    assert failure.value.code is ErrorCode.CONFLICT
    assert output.read_text() == "keep"


@pytest.mark.parametrize(
    "change",
    [
        "workflow",
        "flow",
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
    if change == "workflow":
        suite[change] = "missing"
    elif change == "flow":
        suite[change] = "missing"
    elif change == "step":
        suite["flow"] = "main"
        suite[change] = "missing"
    elif change == "pointer":
        expectation["path"] = "/output/category"
    elif change == "decision":
        expectation["path"] = "/flows/main/steps/missing/result"
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
        load_dataset(_prepare(config))


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _, _ = _project(tmp_path)
    monkeypatch.setattr("foliqant.bootstrap.prepare_application", _prepare)
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
    inline = dict(data["suites"][0], name="isolated", flow="main", step="done")
    data["suites"][0]["cases"] = "pipeline.json"
    data["suites"].append(inline)
    gold.write_text(json.dumps(data))
    prepared = _prepare(config)
    loaded = load_dataset(prepared)
    assert len(loaded.suites[0].gold_cases) == 2
    assert len(loaded.suites[1].gold_cases) == 2
    summary, code = await evaluate_configuration(prepared, check=True)
    assert code == 0 and summary["cases"] == 4
    case_file.unlink()
    assert _prepare(config).configuration_digest == prepared.configuration_digest
    with pytest.raises(ServiceError):
        await evaluate_configuration(prepared, check=True)


async def test_configuration_dispatches_flow_and_step_targets_with_scoped_reports(
    tmp_path: Path,
) -> None:
    config, gold, data = _project(tmp_path)
    pipeline = data["suites"][0]
    flow_suite = dict(pipeline, name="flow", flow="main")
    flow_suite["cases"] = [
        {
            **case,
            "input": {"payload": {"document": case["input"]["payload"]}},
        }
        for case in pipeline["cases"]
    ]
    step_suite = {
        "name": "step",
        "workflow": "demo",
        "flow": "main",
        "step": "done",
        "cases": [
            {
                "id": "isolated",
                "input": {"payload": {"document": {"category": "billing"}}},
                "expectations": [
                    {
                        "name": "category",
                        "path": "/flows/main/steps/done/result/category",
                        "expected": "billing",
                    }
                ],
            }
        ],
    }
    data["suites"] = [flow_suite, step_suite]
    gold.write_text(json.dumps(data))
    output = tmp_path / ".foliqant/scoped.json"

    summary, code = await evaluate_configuration(_prepare(config), output=output)

    assert code == 1  # The authored flow suite retains one deliberate mismatch.
    assert summary["suites"] == 2
    reports = json.loads(output.read_text())["reports"]
    assert (reports[0]["target_flow"], reports[0]["target_step"]) == ("main", None)
    assert (reports[1]["target_flow"], reports[1]["target_step"]) == ("main", "done")
    assert reports[1]["checks"]["passed"] == reports[1]["checks"]["total"] == 1
    assert reports[1]["steps"][0]["flow"] == "main"


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
        load_dataset(_prepare(config))


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
    prepared = _prepare(config)
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
    prepared = _prepare(config)
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


async def test_conventional_gold_is_only_loaded_for_explicit_evaluation(tmp_path: Path) -> None:
    """A deployable bundle runs without its conventionally located test corpus."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config, old_gold, data = _project(config_dir)
    document = yaml.safe_load(config.read_text())
    del document["evaluation"]
    settings = config_dir / "settings.yaml"
    settings.write_text(yaml.safe_dump(document))
    config.unlink()
    old_gold.unlink()
    prepared = _prepare(settings)
    async with open_application(prepared, environment={}) as app:
        result = await app.run("demo", Envelope(payload={"category": "billing"}))
    assert result.execution.status == "completed"
    with pytest.raises(FileNotFoundError):
        load_dataset(prepared)
    gold = tmp_path / "evaluation/dataset.json"
    gold.parent.mkdir()
    gold.write_text(json.dumps(data))
    assert load_dataset(prepared).name == data["name"]
    assert _prepare(settings).configuration_digest == prepared.configuration_digest


def test_shared_dataset_reader_materializes_references_without_configuration(tmp_path, monkeypatch):
    _, gold, document = _project(tmp_path)
    document["suites"].append(dict(document["suites"][0], name="same_cases"))
    before = EvaluationDataset.model_validate(document, strict=True)
    case_file = gold.parent / "shared.json"
    case_file.write_text(json.dumps(document["suites"][0]["cases"]))
    for suite in document["suites"]:
        suite["cases"] = "shared.json"
    gold.write_text(json.dumps(document))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = read_dataset(gold)
    assert loaded.model_dump(mode="json") == before.model_dump(mode="json")
    assert [loaded.to_suite(suite).fingerprint for suite in loaded.suites] == [
        before.to_suite(suite).fingerprint for suite in before.suites
    ]
    # Case-boundary instances are detached even when the file is shared.
    loaded.suites[0].gold_cases[0].id = "edited"
    assert loaded.suites[1].gold_cases[0].id != "edited"

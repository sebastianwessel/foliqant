"""Evaluation orchestration tests with the real artifact boundary and a fake worker."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from safetensors.numpy import save_file

from foliqant_model import training, transformations
from foliqant_model.artifacts import create_manifest, load_verified_artifact
from foliqant_model.contracts import (
    FileEntry,
    MergeWorkerRequest,
    QuantizeWorkerRequest,
    TrainWorkerRequest,
    VersionedComponent,
    WorkerRequest,
    WorkerResult,
    canonical_digest,
)
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError
from foliqant_model.evaluation import evaluate_model, read_verified_predictions


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _producer(command: str) -> dict[str, object]:
    return {
        "name": "foliqant-model",
        "version": "0.1.0",
        "command": command,
        "pythonVersion": "3.12.0",
        "platform": "test",
        "machine": "test",
        "codeRevision": None,
        "codeDirty": None,
        "codeIdentityUnavailableReason": "test fixture",
        "components": [],
    }


def _checkpoint(directory: Path):  # type: ignore[no-untyped-def]
    directory.mkdir()
    config_bytes = b'{"model_type":"fixture"}'
    (directory / "config.json").write_bytes(config_bytes)
    (directory / "tokenizer.json").write_bytes(b"{}")
    (directory / "tokenizer_config.json").write_bytes(b'{"chat_template":"fixture-template"}')
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        directory / "model.safetensors",
    )
    tokenizer_entries = [
        FileEntry(
            path=name,
            size=(directory / name).stat().st_size,
            sha256=_digest((directory / name).read_bytes()),
        )
        for name in ("tokenizer.json", "tokenizer_config.json")
    ]
    return create_manifest(
        directory,
        {
            "schemaVersion": 1,
            "kind": "checkpoint",
            "name": "fixture-model",
            "createdAt": "2026-09-19T00:00:00Z",
            "stage": "upstream",
            "parents": [],
            "producer": _producer("fetch"),
            "sourceRights": [],
            "details": {
                "model": {
                    "architecture": "fixture",
                    "weightFormat": "safetensors",
                    "configSha256": _digest(config_bytes),
                    "tokenizerSha256": canonical_digest(
                        [entry.model_dump(mode="json") for entry in tokenizer_entries]
                    ),
                    "chatTemplateSha256": _digest(b"fixture-template"),
                    "tokenizerFiles": [entry.path for entry in tokenizer_entries],
                    "chatTemplateSource": {
                        "kind": "tokenizer-config",
                        "value": "tokenizer_config.json#/chat_template",
                    },
                },
                "upstreamRepo": "fixture/model",
                "upstreamRevision": "a" * 40,
                "licenseRef": "fixture-license",
                "weightPrecision": "F32",
                "compatibility": [],
            },
        },
    )


def _dataset(tmp_path: Path):  # type: ignore[no-untyped-def]
    records = [
        {
            "schemaVersion": 1,
            "id": f"record-{index:02d}",
            "sourceId": "source",
            "language": "en" if index % 2 else "de",
            "groupKeys": [f"group-{index:02d}"],
            "messages": [
                {"role": "user", "content": f"question {index}"},
                {"role": "assistant", "content": f"answer {index}"},
            ],
            "tags": ["fixture"],
            "origin": "human",
            "reviewed": True,
        }
        for index in range(20)
    ]
    return _prepared_dataset(tmp_path, "fixture", records)


def _prepared_dataset(tmp_path: Path, name: str, records: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    source = tmp_path / f"{name}-source.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    config = tmp_path / f"{name}-dataset.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": name,
                "sources": [
                    {
                        "id": "source",
                        "path": source.name,
                        "license": "test-only",
                        "licenseEvidence": "local fixture",
                        "trainingAllowed": True,
                        "sharedTrainingAllowed": True,
                        "redistributionAllowed": False,
                        "privacy": "public",
                    }
                ],
                "seed": 7,
                "validationFraction": 0.1,
                "calibrationFraction": 0.1,
                "testFraction": 0.1,
                "maxRecords": 100,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / f"{name}-dataset"
    return output, prepare_dataset(config, output)


def test_evaluate_uses_prompt_only_and_publishes_verified_predictions(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    model_path = tmp_path / "model"
    model = _checkpoint(model_path)
    dataset_path, dataset = _dataset(tmp_path)
    config = tmp_path / "evaluation.json"
    config.write_text(
        json.dumps({"schemaVersion": 1, "seed": 11, "maxTokens": 8, "maxExamples": 1}),
        encoding="utf-8",
    )
    requests = []

    def fake_worker(request, *, workspace, timeout_seconds):  # type: ignore[no-untyped-def]
        requests.append(request.root)
        assert workspace.is_dir()
        assert timeout_seconds == 3600
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": request.root.requestId,
                "operation": "generate",
                "ok": True,
                "result": {
                    "generated": "model output",
                    "generatedTokens": 2,
                    "meanTokenLogprob": -0.25,
                    "finishReason": "stop",
                    "elapsedSeconds": 0.01,
                },
            },
            strict=True,
        )

    monkeypatch.setattr("foliqant_model.evaluation.run_worker", fake_worker)
    monkeypatch.setattr("foliqant_model.evaluation._runtime_components", lambda: [])
    output = tmp_path / "evaluation"
    manifest = evaluate_model(config, model_path, dataset_path, output, split="validation")

    assert manifest.root.kind == "evaluation"
    assert manifest.root.details.modelArtifactId == model.root.artifactId
    assert manifest.root.details.datasetArtifactId == dataset.root.artifactId
    assert len(requests) == 1
    assert [message.role for message in requests[0].messages] == ["user"]
    predictions = read_verified_predictions(output, load_verified_artifact(output))
    assert len(predictions) == 1
    assert predictions[0].generated == "model output"
    assert predictions[0].expected not in [message.content for message in requests[0].messages]
    assert predictions[0].representative is True
    assert (output / "predictions.jsonl").stat().st_mode & 0o777 == 0o600


def _training_record(identifier: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": identifier,
        "sourceId": "source",
        "language": "en",
        "groupKeys": [f"family-{identifier}"],
        "messages": [
            {"role": "user", "content": f"question {identifier}"},
            {"role": "assistant", "content": f"answer {identifier}"},
        ],
        "tags": [],
        "origin": "human",
        "reviewed": True,
    }


def _copy_worker_snapshot(source: Path, output: Path, *, quantized: bool) -> None:
    source_manifest = load_verified_artifact(source)
    output.mkdir()
    names = ["config.json", *source_manifest.root.details.model.tokenizerFiles]
    for name in names:
        shutil.copyfile(source / name, output / name)
    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    if quantized:
        config["quantization"] = {"bits": 4, "group_size": 64, "mode": "affine"}
        (output / "config.json").write_text(json.dumps(config), encoding="utf-8")
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        output / "model.safetensors",
    )


def _lineage_with_warm_merge_quantize_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, Path, dict[str, dict[str, object]]]:
    checkpoint = tmp_path / "lineage-checkpoint"
    _checkpoint(checkpoint)
    training_records = {
        record["id"]: record
        for record in [_training_record(f"train-{index}") for index in range(6)]
    }
    dataset_path, dataset = _prepared_dataset(
        tmp_path, "training-lineage", list(training_records.values())
    )
    exposed_records = {
        identifier: training_records[identifier]
        for identifier, assignment in dataset.root.details.assignments.items()
        if assignment.split in {"train", "validation"}
    }
    assert len(exposed_records) >= 4

    train_config = tmp_path / "lineage-train.json"
    train_config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "lineage-adapter",
                "steps": 1,
                "maxSequenceLength": 64,
                "numLayers": 1,
                "rank": 2,
                "scale": 4.0,
                "validationEvery": 1,
                "validationBatches": 1,
                "saveEvery": 1,
            }
        ),
        encoding="utf-8",
    )

    def train_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = cast(TrainWorkerRequest, request.root)
        output = Path(branch.outputPath)
        output.mkdir()
        (output / "adapter_config.json").write_text(
            branch.namespace.model_dump_json(), encoding="utf-8"
        )
        weights = {
            "layers.0.lora_a": np.ones((2, 2), dtype=np.float32),
            "layers.0.lora_b": np.ones((2, 2), dtype=np.float32),
        }
        save_file(weights, output / "adapters.safetensors")
        save_file(weights, output / "0000001_adapters.safetensors")
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": "train",
                "ok": True,
                "result": {
                    "finalLoss": 1.0,
                    "validation": [],
                    "peakMemoryBytes": 1024,
                    "elapsedSeconds": 0.1,
                    "adapterWeightsPath": str(output / "adapters.safetensors"),
                    "adapterConfigPath": str(output / "adapter_config.json"),
                    "trainableTensorNames": ["layers.0.lora_a", "layers.0.lora_b"],
                },
            },
            strict=True,
        )

    monkeypatch.setattr(training, "run_worker", train_worker)
    monkeypatch.setattr(
        training,
        "_backend_components",
        lambda: [VersionedComponent(name="foliqant-completion-loss", version="1", role="library")],
    )
    first = tmp_path / "first-adapter"
    training.train_model(train_config, checkpoint, dataset_path, first)
    warm = tmp_path / "warm-adapter"
    training.train_model(train_config, checkpoint, dataset_path, warm, warm_start=first)

    def transform_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = request.root
        if isinstance(branch, MergeWorkerRequest):
            output = Path(branch.outputPath)
            _copy_worker_snapshot(Path(branch.modelPath), output, quantized=False)
            return WorkerResult.model_validate(
                {
                    "schemaVersion": 1,
                    "requestId": branch.requestId,
                    "operation": "merge",
                    "ok": True,
                    "result": {
                        "outputPath": str(output),
                        "tensorCount": 1,
                        "dequantized": False,
                    },
                },
                strict=True,
            )
        quantize = cast(QuantizeWorkerRequest, branch)
        output = Path(quantize.outputPath)
        _copy_worker_snapshot(Path(quantize.modelPath), output, quantized=True)
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": quantize.requestId,
                "operation": "quantize",
                "ok": True,
                "result": {
                    "outputPath": str(output),
                    "tensorCount": 1,
                    "fromPrecision": "F32",
                    "toPrecision": "4-bit-affine",
                },
            },
            strict=True,
        )

    monkeypatch.setattr(transformations, "run_worker", transform_worker)
    monkeypatch.setattr(
        transformations,
        "_backend_components",
        lambda: [VersionedComponent(name="mlx-lm", version="test", role="backend")],
    )
    merged = tmp_path / "merged"
    transformations.merge_model(checkpoint, warm, merged)
    quantized = tmp_path / "quantized"
    transformations.quantize_model(merged, quantized, bits=4)
    exported = tmp_path / "exported"
    transformations.export_model(merged, exported, format="checkpoint")
    return checkpoint, warm, merged, quantized, exported, exposed_records


def _overlapping_records(
    kind: str, exposed: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, original in enumerate(list(exposed.values())[:4]):
        messages = cast(list[dict[str, str]], original["messages"])
        record_id = cast(str, original["id"]) if kind == "id" else f"eval-{kind}-{index}"
        group_keys = (
            list(cast(list[str], original["groupKeys"]))
            if kind == "group"
            else [f"eval-family-{kind}-{index}"]
        )
        if kind == "conversation":
            candidate_messages = [dict(message) for message in messages]
        elif kind == "prompt":
            candidate_messages = [
                dict(messages[0]),
                {"role": "assistant", "content": f"different answer {index}"},
            ]
        else:
            candidate_messages = [
                {"role": "user", "content": f"different question {kind} {index}"},
                {"role": "assistant", "content": f"different response {kind} {index}"},
            ]
        records.append(
            {
                "schemaVersion": 1,
                "id": record_id,
                "sourceId": "source",
                "language": "en",
                "groupKeys": group_keys,
                "messages": candidate_messages,
                "tags": [],
                "origin": "human",
                "reviewed": True,
            }
        )
    return records


def test_evaluation_rejects_cross_dataset_leakage_through_all_derived_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, warm, merged, quantized, exported, exposed = (
        _lineage_with_warm_merge_quantize_export(tmp_path, monkeypatch)
    )
    config = tmp_path / "leakage-evaluation.json"
    config.write_text(json.dumps({"schemaVersion": 1, "maxExamples": 1}), encoding="utf-8")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("generation must not start for leaked evaluation data")

    monkeypatch.setattr("foliqant_model.evaluation.run_worker", forbidden)
    targets = {
        "id": (checkpoint, warm),
        "group": (merged, None),
        "conversation": (quantized, None),
        "prompt": (exported, None),
    }
    for kind, (model_path, adapter_path) in targets.items():
        dataset_path, _ = _prepared_dataset(
            tmp_path, f"evaluation-{kind}", _overlapping_records(kind, exposed)
        )
        with pytest.raises(ModelError) as failure:
            evaluate_model(
                config,
                model_path,
                dataset_path,
                tmp_path / f"rejected-{kind}",
                split="test",
                adapter_path=adapter_path,
            )
        assert failure.value.code == "LEAKAGE_DETECTED"

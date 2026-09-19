"""Transformation orchestration tests with inspected worker outputs."""

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


def _checkpoint(directory: Path, *, architecture: str = "fixture") -> Path:
    directory.mkdir()
    (directory / "config.json").write_text(
        json.dumps({"model_type": architecture}), encoding="utf-8"
    )
    (directory / "tokenizer.json").write_text("{}", encoding="utf-8")
    (directory / "tokenizer_config.json").write_text(
        '{"chat_template":"template"}', encoding="utf-8"
    )
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        directory / "model.safetensors",
    )
    tokenizer_entries = []
    for name in ("tokenizer.json", "tokenizer_config.json"):
        path = directory / name
        tokenizer_entries.append(
            FileEntry(path=name, size=path.stat().st_size, sha256=_digest(path.read_bytes()))
        )
    create_manifest(
        directory,
        {
            "schemaVersion": 1,
            "kind": "checkpoint",
            "name": "checkpoint-fixture",
            "createdAt": "2026-09-19T00:00:00Z",
            "stage": "upstream",
            "parents": [],
            "producer": _producer("fetch"),
            "sourceRights": [],
            "details": {
                "model": {
                    "architecture": architecture,
                    "weightFormat": "safetensors",
                    "configSha256": _digest((directory / "config.json").read_bytes()),
                    "tokenizerSha256": canonical_digest(
                        [item.model_dump(mode="json") for item in tokenizer_entries]
                    ),
                    "chatTemplateSha256": _digest(b"template"),
                    "tokenizerFiles": ["tokenizer.json", "tokenizer_config.json"],
                    "chatTemplateSource": {
                        "kind": "tokenizer-config",
                        "value": "tokenizer_config.json#/chat_template",
                    },
                },
                "upstreamRepo": "owner/model",
                "upstreamRevision": "a" * 40,
                "licenseRef": "test",
                "weightPrecision": "F32",
                "compatibility": [],
            },
        },
    )
    return directory


def _copy_snapshot(source: Path, output: Path, *, quantized: bool = False) -> None:
    output.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        shutil.copyfile(source / name, output / name)
    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    if quantized:
        config["quantization"] = {"bits": 4, "group_size": 64, "mode": "affine"}
        (output / "config.json").write_text(json.dumps(config), encoding="utf-8")
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        output / "model.safetensors",
    )


def _record(identifier: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": identifier,
        "sourceId": "source",
        "language": "en",
        "groupKeys": [identifier],
        "messages": [
            {"role": "user", "content": f"question {identifier}"},
            {"role": "assistant", "content": f"answer {identifier}"},
        ],
        "tags": [],
        "origin": "human",
        "reviewed": True,
    }


def _dataset(tmp_path: Path) -> Path:
    source = tmp_path / "source.jsonl"
    source.write_text(
        "".join(json.dumps(_record(item)) + "\n" for item in "abcdef"), encoding="utf-8"
    )
    config = tmp_path / "dataset-config.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "dataset-fixture",
                "sources": [
                    {
                        "id": "source",
                        "path": source.name,
                        "license": "test",
                        "licenseEvidence": "synthetic fixture",
                        "trainingAllowed": True,
                        "sharedTrainingAllowed": True,
                        "redistributionAllowed": False,
                        "privacy": "public",
                        "commercialUse": "unknown",
                        "restrictions": ["test-only"],
                    }
                ],
                "seed": 7,
                "validationFraction": 0.1,
                "calibrationFraction": 0.1,
                "testFraction": 0.1,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "dataset"
    prepare_dataset(config, output)
    return output


def _adapter(
    tmp_path: Path,
    checkpoint: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    config = tmp_path / "train.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "adapter-fixture",
                "steps": 1,
                "validationEvery": 1,
                "validationBatches": 1,
                "saveEvery": 1,
                "maxSequenceLength": 64,
                "numLayers": 1,
                "rank": 2,
                "scale": 4.0,
            }
        ),
        encoding="utf-8",
    )
    dataset = _dataset(tmp_path)

    def train_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = cast(TrainWorkerRequest, request.root)
        output = Path(branch.outputPath)
        output.mkdir()
        namespace = branch.namespace
        (output / "adapter_config.json").write_text(namespace.model_dump_json(), encoding="utf-8")
        save_file(
            {
                "layers.0.lora_a": np.ones((2, 2), dtype=np.float32),
                "layers.0.lora_b": np.ones((2, 2), dtype=np.float32),
            },
            output / "adapters.safetensors",
        )
        shutil.copyfile(output / "adapters.safetensors", output / "0000001_adapters.safetensors")
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
            }
        )

    monkeypatch.setattr(training, "run_worker", train_worker)
    monkeypatch.setattr(
        training,
        "_backend_components",
        lambda: [VersionedComponent(name="foliqant-completion-loss", version="1", role="library")],
    )
    output = tmp_path / "adapter"
    training.train_model(config, checkpoint, dataset, output)
    return output


def _fake_components() -> list[VersionedComponent]:
    return [VersionedComponent(name="mlx-lm", version="test", role="backend")]


def test_quantize_inspects_output_and_persists_exact_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")

    def quantize_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = cast(QuantizeWorkerRequest, request.root)
        output = Path(branch.outputPath)
        _copy_snapshot(checkpoint, output, quantized=True)
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": "quantize",
                "ok": True,
                "result": {
                    "outputPath": str(output),
                    "tensorCount": 1,
                    "fromPrecision": "F32",
                    "toPrecision": "4-bit-affine",
                },
            }
        )

    monkeypatch.setattr(transformations, "run_worker", quantize_worker)
    monkeypatch.setattr(transformations, "_backend_components", _fake_components)
    output = tmp_path / "quantized"
    result = transformations.quantize_model(checkpoint, output, bits=4)
    assert result.root.kind == "quantized"
    assert result.root.parents[0].artifactId == load_verified_artifact(checkpoint).root.artifactId
    assert result.root.details.model.weightFormat == "safetensors"
    assert result.root.details.precisionHistory[-1].operation == "quantize"
    assert result.root.details.exposureLeakageIndex.entryCount == 0
    assert not (output / "inherited-leakage").exists()


def test_quantize_rejects_worker_tensor_count_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")

    def lying_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = cast(QuantizeWorkerRequest, request.root)
        output = Path(branch.outputPath)
        _copy_snapshot(checkpoint, output, quantized=True)
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": "quantize",
                "ok": True,
                "result": {
                    "outputPath": str(output),
                    "tensorCount": 2,
                    "fromPrecision": "F32",
                    "toPrecision": "4-bit-affine",
                },
            }
        )

    monkeypatch.setattr(transformations, "run_worker", lying_worker)
    with pytest.raises(ModelError) as raised:
        transformations.quantize_model(checkpoint, tmp_path / "quantized", bits=4)
    assert raised.value.code == "OUTPUT_INVALID"


def test_merge_and_checkpoint_export_preserve_model_and_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")
    adapter = _adapter(tmp_path, checkpoint, monkeypatch)

    def merge_worker(
        request: WorkerRequest, *, workspace: Path, timeout_seconds: int
    ) -> WorkerResult:
        branch = cast(MergeWorkerRequest, request.root)
        output = Path(branch.outputPath)
        _copy_snapshot(checkpoint, output)
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
            }
        )

    monkeypatch.setattr(transformations, "run_worker", merge_worker)
    monkeypatch.setattr(transformations, "_backend_components", _fake_components)
    merged_path = tmp_path / "merged"
    merged = transformations.merge_model(checkpoint, adapter, merged_path)
    assert merged.root.kind == "merged"
    assert merged.root.details.precisionHistory[-1].operation == "fuse"
    assert merged.root.details.exposureLeakageIndex.entryCount > 0

    export_path = tmp_path / "export"
    exported = transformations.export_model(merged_path, export_path, format="checkpoint")
    assert exported.root.kind == "export"
    assert exported.root.details.exportMetadata.format == "checkpoint"
    assert exported.root.details.compatibility[0].target == "transformers"
    assert exported.root.details.compatibility[0].status == "unverified"
    assert exported.root.details.exposureLeakageIndex.file.sha256 == (
        merged.root.details.exposureLeakageIndex.file.sha256
    )
    assert load_verified_artifact(export_path).root.artifactId == exported.root.artifactId
    with pytest.raises(ModelError) as unsupported:
        transformations.export_model(merged_path, tmp_path / "gguf", format="gguf")
    assert unsupported.value.code == "ARCHITECTURE_UNSUPPORTED"


def test_transformations_reject_wrong_kinds_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")
    monkeypatch.setattr(
        transformations,
        "run_worker",
        lambda *_args, **_kwargs: pytest.fail("worker must not run"),
    )
    with pytest.raises(ModelError, match="4 or 8"):
        transformations.quantize_model(checkpoint, tmp_path / "bad-bits", bits=True)
    with pytest.raises(ModelError, match="merged"):
        transformations.export_model(checkpoint, tmp_path / "bad-export", format="checkpoint")
    with pytest.raises(ModelError, match="adapter"):
        transformations.merge_model(checkpoint, checkpoint, tmp_path / "bad-merge")


@pytest.mark.parametrize("change", ["architecture", "template", "config"])
def test_conversion_rejects_semantic_drift(tmp_path: Path, change: str) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")
    parent = load_verified_artifact(checkpoint)
    assert parent.root.kind == "checkpoint"
    output = tmp_path / "converted"
    _copy_snapshot(checkpoint, output)
    if change == "template":
        (output / "tokenizer_config.json").write_text('{"chat_template":"changed template"}')
    else:
        config = json.loads((output / "config.json").read_text())
        config["model_type" if change == "architecture" else "vocab_size"] = "changed"
        (output / "config.json").write_text(json.dumps(config))
    inspected = transformations.inspect_snapshot(output)
    with pytest.raises(ModelError) as failure:
        transformations._preserve_parent_semantics(
            checkpoint, output, parent.root.details.model, inspected, quantized=False
        )
    assert failure.value.code == "OUTPUT_INVALID"


def test_conversion_restores_exact_original_tokenizer_bytes(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path / "checkpoint")
    parent = load_verified_artifact(checkpoint)
    assert parent.root.kind == "checkpoint"
    output = tmp_path / "converted"
    _copy_snapshot(checkpoint, output)
    (output / "tokenizer.json").write_text("{  }\n")
    inspected = transformations.inspect_snapshot(output)
    assert inspected.model.tokenizerSha256 != parent.root.details.model.tokenizerSha256
    preserved = transformations._preserve_parent_semantics(
        checkpoint, output, parent.root.details.model, inspected, quantized=False
    )
    assert preserved.model.tokenizerSha256 == parent.root.details.model.tokenizerSha256

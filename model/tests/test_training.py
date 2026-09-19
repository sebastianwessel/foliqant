"""Training orchestration tests with controlled worker doubles."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from safetensors.numpy import save_file

from foliqant_model import training, transformations
from foliqant_model.artifacts import create_manifest, load_verified_artifact
from foliqant_model.contracts import FileEntry, VersionedComponent, WorkerResult, canonical_digest
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError
from foliqant_model.snapshots import snapshot_file_allowed


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


def _checkpoint(directory: Path, *, revision: str = "a" * 40):  # type: ignore[no-untyped-def]
    directory.mkdir()
    (directory / "config.json").write_text('{"model_type":"fixture"}', encoding="utf-8")
    (directory / "tokenizer.json").write_text("{}", encoding="utf-8")
    (directory / "tokenizer_config.json").write_text(
        '{"chat_template":"template"}', encoding="utf-8"
    )
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        directory / "model.safetensors",
    )
    tokenizer_files = []
    for name in ("tokenizer.json", "tokenizer_config.json"):
        path = directory / name
        tokenizer_files.append(
            FileEntry(path=name, size=path.stat().st_size, sha256=_digest(path.read_bytes()))
        )
    return create_manifest(
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
                    "architecture": "fixture",
                    "weightFormat": "safetensors",
                    "configSha256": _digest((directory / "config.json").read_bytes()),
                    "tokenizerSha256": canonical_digest(
                        [item.model_dump(mode="json") for item in tokenizer_files]
                    ),
                    "chatTemplateSha256": _digest(b"template"),
                    "tokenizerFiles": ["tokenizer.json", "tokenizer_config.json"],
                    "chatTemplateSource": {
                        "kind": "tokenizer-config",
                        "value": "tokenizer_config.json#/chat_template",
                    },
                },
                "upstreamRepo": "owner/model",
                "upstreamRevision": revision,
                "licenseRef": "test",
                "weightPrecision": "F32",
                "compatibility": [],
            },
        },
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


def _dataset(tmp_path: Path, *, shared_allowed: bool = True) -> Path:
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
                        "sharedTrainingAllowed": shared_allowed,
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


def _train_config(path: Path, *, steps: int = 2, accumulation: int = 1) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "adapter-fixture",
                "steps": steps,
                "batchSize": 1,
                "gradientAccumulation": accumulation,
                "maxSequenceLength": 64,
                "numLayers": 1,
                "rank": 2,
                "scale": 4.0,
                "dropout": 0.0,
                "validationEvery": 1,
                "validationBatches": 1,
                "saveEvery": 1,
                "gradientCheckpointing": False,
            }
        ),
        encoding="utf-8",
    )


def _install_worker_double(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    requests: list[object] = []

    def fake_worker(request, *, workspace: Path, timeout_seconds: int):  # type: ignore[no-untyped-def]
        requests.append(request)
        branch = request.root
        assert {item.name for item in Path(branch.namespace.data).iterdir()} == {
            "train.jsonl",
            "validation.jsonl",
        }
        assert workspace == Path(branch.outputPath).parent
        assert timeout_seconds == 3600
        output = Path(branch.outputPath)
        output.mkdir()
        names = ["model.layers.0.proj.lora_a", "model.layers.0.proj.lora_b"]
        save_file(
            {
                names[0]: np.ones((3, 2), dtype=np.float32),
                names[1]: np.ones((2, 4), dtype=np.float32),
            },
            output / "adapters.safetensors",
        )
        for iteration in range(
            branch.namespace.save_every,
            branch.namespace.iters + 1,
            branch.namespace.save_every,
        ):
            save_file(
                {
                    names[0]: np.ones((3, 2), dtype=np.float32),
                    names[1]: np.ones((2, 4), dtype=np.float32),
                },
                output / f"{iteration:07d}_adapters.safetensors",
            )
        (output / "adapter_config.json").write_text(
            branch.namespace.model_dump_json(), encoding="utf-8"
        )
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": "train",
                "ok": True,
                "result": {
                    "finalLoss": 0.75,
                    "validation": [{"iteration": 1, "loss": 0.8, "elapsedSeconds": 0.1}],
                    "peakMemoryBytes": 1234,
                    "elapsedSeconds": 0.2,
                    "adapterWeightsPath": str(output / "adapters.safetensors"),
                    "adapterConfigPath": str(output / "adapter_config.json"),
                    "trainableTensorNames": names,
                },
            }
        )

    monkeypatch.setattr(training, "run_worker", fake_worker)
    monkeypatch.setattr(
        training,
        "_backend_components",
        lambda: [
            VersionedComponent(name="mlx-lm", version="test", role="backend"),
            VersionedComponent(name="foliqant-completion-loss", version="1", role="library"),
            VersionedComponent(name="mlx", version="test", role="library"),
        ],
    )
    return requests


def _install_merge_double(monkeypatch: pytest.MonkeyPatch) -> None:
    def merge_worker(request, **_kwargs: object):  # type: ignore[no-untyped-def]
        branch = request.root
        source = Path(branch.modelPath)
        output = Path(branch.outputPath)
        output.mkdir()
        model = load_verified_artifact(source)
        for entry in model.root.files:
            if "/" not in entry.path and snapshot_file_allowed(entry.path):
                shutil.copyfile(source / entry.path, output / entry.path)
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
    monkeypatch.setattr(
        transformations,
        "_backend_components",
        lambda: [VersionedComponent(name="mlx-lm", version="test", role="backend")],
    )


def test_shared_training_uses_private_partitions_and_publishes_verified_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)
    requests = _install_worker_double(monkeypatch)

    output = tmp_path / "adapter"
    manifest = training.train_model(config_path, model_path, dataset_path, output)

    assert len(requests) == 1
    assert load_verified_artifact(output).root.artifactId == manifest.root.artifactId
    assert manifest.root.kind == "adapter"
    assert manifest.root.stage == "shared"
    assert manifest.root.details.finalLoss == 0.75
    expected = (
        manifest.root.parents[1].details.partitions.train.records
        + manifest.root.parents[1].details.partitions.validation.records
    )
    assert manifest.root.details.usedDataLeakageIndex.entryCount == expected
    assert [item.sourceId for item in manifest.root.sourceRights] == ["source"]
    assert {item.ancestorArtifactId for item in manifest.root.details.inheritedLeakageIndexes} == {
        manifest.root.parents[1].artifactId
    }


def test_training_preflight_rejects_rights_and_step_contract_before_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path, shared_allowed=False)
    config_path = tmp_path / "train.json"
    _train_config(config_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("worker must not launch")

    monkeypatch.setattr(training, "run_worker", forbidden)
    with pytest.raises(ModelError) as captured:
        training.train_model(config_path, model_path, dataset_path, tmp_path / "denied")
    assert captured.value.code == "DATA_RIGHTS_DENIED"

    _train_config(config_path, steps=3, accumulation=2)
    with pytest.raises(ModelError) as captured:
        training.train_model(config_path, model_path, dataset_path, tmp_path / "bad-steps")
    assert captured.value.code == "CONFIG_INVALID"


def test_training_rejects_wrong_customer_parent_and_undersized_validation_before_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("worker must not launch")

    monkeypatch.setattr(training, "run_worker", forbidden)
    with pytest.raises(ModelError) as captured:
        training.train_model(
            config_path,
            model_path,
            dataset_path,
            tmp_path / "customer",
            customer="customer-a",
        )
    assert captured.value.code == "LINEAGE_MISMATCH"

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["batchSize"] = 2
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelError) as captured:
        training.train_model(config_path, model_path, dataset_path, tmp_path / "large-batch")
    assert captured.value.code == "CONFIG_INVALID"


def test_training_rejects_customer_release_for_shared_or_further_customer_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)
    _install_worker_double(monkeypatch)
    _install_merge_double(monkeypatch)

    shared_adapter = tmp_path / "shared-adapter"
    training.train_model(config_path, model_path, dataset_path, shared_adapter)
    shared_merged = tmp_path / "shared-merged"
    transformations.merge_model(model_path, shared_adapter, shared_merged)
    customer_adapter = tmp_path / "customer-adapter"
    training.train_model(
        config_path,
        shared_merged,
        dataset_path,
        customer_adapter,
        customer="customer-a",
    )
    customer_merged = tmp_path / "customer-merged"
    transformations.merge_model(shared_merged, customer_adapter, customer_merged)

    monkeypatch.setattr(
        training,
        "run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("worker launched")),
    )
    for customer, output in (
        (None, tmp_path / "shared-from-customer"),
        ("customer-a", tmp_path / "customer-from-customer"),
    ):
        with pytest.raises(ModelError) as captured:
            training.train_model(
                config_path,
                customer_merged,
                dataset_path,
                output,
                customer=customer,
            )
        assert captured.value.code == "LINEAGE_MISMATCH"


def test_worker_failure_never_publishes_an_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)

    def failed(request, **_kwargs: object):  # type: ignore[no-untyped-def]
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": request.root.requestId,
                "operation": "train",
                "ok": False,
                "error": {"code": "TRAINING_FAILED", "message": "training failed"},
            }
        )

    monkeypatch.setattr(training, "run_worker", failed)
    output = tmp_path / "failed"
    with pytest.raises(ModelError) as captured:
        training.train_model(config_path, model_path, dataset_path, output)
    assert captured.value.code == "BACKEND_FAILED"
    assert not output.exists()


@pytest.mark.parametrize("issue", ["malformed", "nonfinite"])
def test_invalid_adapter_tensors_never_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, issue: str
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)

    def invalid_worker(request, **_kwargs: object):  # type: ignore[no-untyped-def]
        branch = request.root
        output = Path(branch.outputPath)
        output.mkdir()
        names = ["model.layers.0.proj.lora_a", "model.layers.0.proj.lora_b"]
        if issue == "malformed":
            (output / "adapters.safetensors").write_bytes(b"not safetensors")
        else:
            save_file(
                {
                    names[0]: np.full((3, 2), np.nan, dtype=np.float32),
                    names[1]: np.ones((2, 4), dtype=np.float32),
                },
                output / "adapters.safetensors",
            )
        for iteration in range(
            branch.namespace.save_every,
            branch.namespace.iters + 1,
            branch.namespace.save_every,
        ):
            (output / f"{iteration:07d}_adapters.safetensors").write_bytes(
                (output / "adapters.safetensors").read_bytes()
            )
        (output / "adapter_config.json").write_text(
            branch.namespace.model_dump_json(), encoding="utf-8"
        )
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": "train",
                "ok": True,
                "result": {
                    "finalLoss": 0.75,
                    "validation": [],
                    "peakMemoryBytes": 1234,
                    "elapsedSeconds": 0.2,
                    "adapterWeightsPath": str(output / "adapters.safetensors"),
                    "adapterConfigPath": str(output / "adapter_config.json"),
                    "trainableTensorNames": names,
                },
            }
        )

    monkeypatch.setattr(training, "run_worker", invalid_worker)
    output = tmp_path / "invalid-adapter"
    with pytest.raises(ModelError) as captured:
        training.train_model(config_path, model_path, dataset_path, output)
    assert captured.value.code == "OUTPUT_INVALID"
    assert not output.exists()


def test_warm_start_rejects_incompatible_rank_before_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)
    _install_worker_double(monkeypatch)
    first = tmp_path / "first"
    training.train_model(config_path, model_path, dataset_path, first)

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["rank"] = 3
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        training,
        "run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("worker launched")),
    )
    with pytest.raises(ModelError) as captured:
        training.train_model(
            config_path,
            model_path,
            dataset_path,
            tmp_path / "second",
            warm_start=first,
        )
    assert captured.value.code == "LINEAGE_MISMATCH"


def test_warm_start_rejects_valid_adapter_from_different_exact_parent_before_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_model = tmp_path / "first-model"
    second_model = tmp_path / "second-model"
    _checkpoint(first_model)
    _checkpoint(second_model, revision="b" * 40)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)
    _install_worker_double(monkeypatch)
    adapter_path = tmp_path / "adapter"
    training.train_model(config_path, first_model, dataset_path, adapter_path)

    monkeypatch.setattr(
        training,
        "run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("worker launched")),
    )
    with pytest.raises(ModelError) as captured:
        training.train_model(
            config_path,
            second_model,
            dataset_path,
            tmp_path / "wrong-parent",
            warm_start=adapter_path,
        )
    assert captured.value.code == "LINEAGE_MISMATCH"


def test_warm_start_deduplicates_direct_and_inherited_dataset_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model"
    _checkpoint(model_path)
    dataset_path = _dataset(tmp_path)
    config_path = tmp_path / "train.json"
    _train_config(config_path)
    _install_worker_double(monkeypatch)
    first_path = tmp_path / "first"
    first = training.train_model(config_path, model_path, dataset_path, first_path)

    second = training.train_model(
        config_path,
        model_path,
        dataset_path,
        tmp_path / "second",
        warm_start=first_path,
    )

    assert second.root.details.warmStartArtifactId == first.root.artifactId
    inherited_ids = [
        item.ancestorArtifactId for item in second.root.details.inheritedLeakageIndexes
    ]
    assert inherited_ids == sorted(set(inherited_ids))
    dataset_id = load_verified_artifact(dataset_path).root.artifactId
    assert set(inherited_ids) == {dataset_id, first.root.artifactId}

"""Focused persistence tests for adapter training evidence."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from foliqant_model.contracts import AdapterDetails, TrainConfig, canonical_digest

DIGEST = "a" * 64


def _adapter_details() -> dict[str, object]:
    train = TrainConfig.model_validate({"schemaVersion": 1, "name": "fixture"})
    train_payload = train.model_dump(mode="json")
    backend = {
        "method": "lora",
        "maskPrompt": True,
        **{
            key: train_payload[key]
            for key in (
                "seed",
                "steps",
                "batchSize",
                "gradientAccumulation",
                "maxSequenceLength",
                "learningRate",
                "numLayers",
                "rank",
                "scale",
                "dropout",
                "gradientCheckpointing",
                "validationEvery",
                "validationBatches",
                "saveEvery",
            )
        },
        "modelArtifactId": DIGEST,
        "datasetArtifactId": DIGEST,
        "trainFileSha256": DIGEST,
        "validationFileSha256": DIGEST,
    }
    return {
        "modelArtifactId": DIGEST,
        "datasetArtifactId": DIGEST,
        "trainConfig": train_payload,
        "trainConfigSha256": canonical_digest(train_payload),
        "backendConfig": backend,
        "usedDataLeakageIndex": {
            "file": {
                "path": "leakage.jsonl",
                "sha256": DIGEST,
                "recordCount": 0,
                "format": "jsonl",
            },
            "splits": ["train", "validation"],
            "entryCount": 0,
        },
        "inheritedLeakageIndexes": [],
        "finalLoss": 1.25,
        "validation": [
            {"iteration": 25, "loss": 1.5, "elapsedSeconds": 0.2},
            {"iteration": 50, "loss": 1.4, "elapsedSeconds": 0.3},
        ],
        "peakMemoryBytes": {"value": 1024},
        "elapsedSeconds": 2.0,
        "trainableTensorNames": ["layers.0.lora_a", "layers.0.lora_b"],
        "adapterFormat": "mlx-lora-v1",
    }


def test_adapter_persists_validation_and_resolved_trainable_tensors() -> None:
    details = AdapterDetails.model_validate(_adapter_details())
    assert [item.iteration for item in details.validation] == [25, 50]
    assert details.trainableTensorNames == ["layers.0.lora_a", "layers.0.lora_b"]


@pytest.mark.parametrize(
    "validation",
    [
        [
            {"iteration": 50, "loss": 1.4, "elapsedSeconds": 0.3},
            {"iteration": 25, "loss": 1.5, "elapsedSeconds": 0.2},
        ],
        [
            {"iteration": 25, "loss": 1.5, "elapsedSeconds": 0.2},
            {"iteration": 25, "loss": 1.4, "elapsedSeconds": 0.3},
        ],
    ],
)
def test_adapter_validation_iterations_are_ordered_and_unique(
    validation: list[dict[str, object]],
) -> None:
    payload = _adapter_details()
    payload["validation"] = validation
    with pytest.raises(ValidationError):
        AdapterDetails.model_validate(payload)


@pytest.mark.parametrize(
    "names",
    [[], ["layers.0.lora_b", "layers.0.lora_a"], ["layers.0.lora_a"] * 2],
)
def test_adapter_trainable_tensor_names_are_nonempty_sorted_and_unique(names: list[str]) -> None:
    payload = deepcopy(_adapter_details())
    payload["trainableTensorNames"] = names
    with pytest.raises(ValidationError):
        AdapterDetails.model_validate(payload)


@pytest.mark.parametrize("missing", ["validation", "trainableTensorNames"])
def test_adapter_training_evidence_fields_are_required(missing: str) -> None:
    payload = _adapter_details()
    del payload[missing]
    with pytest.raises(ValidationError):
        AdapterDetails.model_validate(payload)

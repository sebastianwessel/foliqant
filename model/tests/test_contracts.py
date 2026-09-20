"""Executable examples and boundary tests for the canonical model contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError

from foliqant_model.contracts import (
    ArtifactManifest,
    CliSuccess,
    DatasetConfig,
    Prediction,
    SourceDeclaration,
    SourceRight,
    TrainConfig,
    WorkerRequest,
    WorkerResult,
    canonical_digest,
)

DIGEST = "a" * 64
COMMIT = "b" * 40


def _producer(command: str) -> dict[str, object]:
    return {
        "name": "foliqant-model",
        "version": "0.1.0",
        "command": command,
        "pythonVersion": "3.12.11",
        "platform": "Darwin",
        "machine": "arm64",
        "codeRevision": COMMIT,
        "codeDirty": False,
        "components": [],
    }


def _checkpoint() -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "artifactId": DIGEST,
        "kind": "checkpoint",
        "name": "base-model",
        "createdAt": "2026-09-19T12:00:00Z",
        "stage": "upstream",
        "files": [],
        "parents": [],
        "producer": _producer("fetch"),
        "sourceRights": [],
        "details": {
            "model": {
                "architecture": "fixture",
                "weightFormat": "safetensors",
                "configSha256": DIGEST,
                "tokenizerSha256": DIGEST,
                "chatTemplateSha256": DIGEST,
                "tokenizerFiles": ["tokenizer.json"],
                "chatTemplateSource": {
                    "kind": "file",
                    "value": "chat_template.jinja",
                },
            },
            "upstreamRepo": "fixture/model",
            "upstreamRevision": COMMIT,
            "licenseRef": "fixture-license",
            "weightPrecision": "bf16",
            "compatibility": [],
        },
    }
    manifest["artifactId"] = canonical_digest(
        {key: value for key, value in manifest.items() if key != "artifactId"}
    )
    return manifest


def _source(**updates: object) -> dict[str, object]:
    source: dict[str, object] = {
        "id": "source-a",
        "path": "/tmp/source.jsonl",
        "license": "research-only",
        "licenseEvidence": "LICENSE#research",
        "trainingAllowed": True,
        "redistributionAllowed": False,
        "privacy": "public",
    }
    source.update(updates)
    return source


def test_dataset_source_defaults_preserve_research_rights() -> None:
    config = DatasetConfig.model_validate(
        {"schemaVersion": 1, "name": "fixture", "sources": [_source()]}
    )
    source = config.sources[0]
    assert source.sharedTrainingAllowed is False
    assert source.commercialUse == "unknown"
    assert source.restrictions == []
    assert "authorizationRef" not in source.model_dump(mode="json")


def test_commercial_restrictions_are_unique_and_do_not_block_training() -> None:
    source = SourceDeclaration.model_validate(
        _source(
            commercialUse="restricted",
            restrictions=["non-commercial", "research-only"],
        )
    )
    assert source.trainingAllowed is True
    with pytest.raises(ValidationError):
        SourceDeclaration.model_validate(_source(restrictions=["non-commercial", "non-commercial"]))


def test_materialized_source_right_requires_explicit_commercial_assessment() -> None:
    right = {
        "sourceId": "source-a",
        "license": "research-only",
        "licenseEvidence": "LICENSE#research",
        "trainingAllowed": True,
        "sharedTrainingAllowed": False,
        "redistributionAllowed": False,
        "privacy": "public",
        "attribution": "",
        "commercialUse": "restricted",
        "restrictions": ["non-commercial"],
    }
    assert SourceRight.model_validate(right).commercialUse == "restricted"
    del right["commercialUse"]
    with pytest.raises(ValidationError):
        SourceRight.model_validate(right)


@pytest.mark.parametrize("field", ["batchSize", "steps", "seed"])
def test_integer_train_fields_reject_boolean_values(field: str) -> None:
    with pytest.raises(ValidationError):
        TrainConfig.model_validate({"schemaVersion": 1, "name": "train", field: True})


def test_all_contract_objects_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DatasetConfig.model_validate(
            {
                "schemaVersion": 1,
                "name": "fixture",
                "sources": [_source()],
                "unexpected": "closed-contract",
            }
        )


def test_worker_request_is_a_closed_operation_union() -> None:
    request = WorkerRequest.model_validate(
        {"schemaVersion": 1, "requestId": "probe", "operation": "doctor"}
    )
    assert request.root.operation == "doctor"
    with pytest.raises(ValidationError):
        WorkerRequest.model_validate(
            {
                "schemaVersion": 1,
                "requestId": "probe",
                "operation": "doctor",
                "modelPath": "/tmp/model",
            }
        )


def test_worker_success_operation_must_match_result_shape() -> None:
    with pytest.raises(ValidationError):
        WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": "request-a",
                "operation": "generate",
                "ok": True,
                "result": {
                    "mlxImportAvailable": True,
                    "metalAvailable": True,
                    "mlxVersion": "1.0",
                    "mlxLmVersion": "1.0",
                    "unavailableReason": None,
                },
            }
        )


def test_generation_score_nullability_is_exact() -> None:
    payload = {
        "schemaVersion": 1,
        "requestId": "request-a",
        "operation": "generate",
        "ok": True,
        "result": {
            "generated": "",
            "generatedTokens": 0,
            "meanTokenLogprob": None,
            "meanTokenLogprobUnavailableReason": "no-generated-tokens",
            "finishReason": "stop",
            "elapsedSeconds": 0.0,
        },
    }
    assert WorkerResult.model_validate(payload).root.operation == "generate"
    result = payload["result"]
    assert isinstance(result, dict)
    result.pop("meanTokenLogprobUnavailableReason")
    with pytest.raises(ValidationError):
        WorkerResult.model_validate(payload)


def test_cli_command_must_match_nested_result() -> None:
    with pytest.raises(ValidationError):
        CliSuccess.model_validate(
            {
                "schemaVersion": 1,
                "ok": True,
                "command": "fetch",
                "result": {
                    "command": "doctor",
                    "pythonVersion": "3.12.11",
                    "platform": "Darwin",
                    "machine": "arm64",
                    "mlxAvailable": False,
                    "mlxDeviceAvailable": False,
                    "freeDiskBytes": 0,
                    "availableCommands": [],
                    "unavailableCommands": [],
                    "components": [],
                },
            }
        )


def test_setup_is_a_closed_cli_success_branch() -> None:
    success = CliSuccess.model_validate(
        {
            "schemaVersion": 1,
            "ok": True,
            "command": "setup",
            "result": {
                "command": "setup",
                "workspacePath": "/tmp/foliqant",
                "profilePath": "/tmp/foliqant/profile.json",
                "receiptPath": "/tmp/foliqant/receipt.json",
                "profileSha256": DIGEST,
                "downloadedFiles": 14,
                "reusedFiles": 0,
                "modelPath": "/tmp/foliqant/model",
                "sharedDatasetPath": "/tmp/foliqant/shared",
                "customerDatasetPath": "/tmp/foliqant/customer",
                "trainConfigPath": "/tmp/foliqant/train.json",
                "evaluationConfigPath": "/tmp/foliqant/evaluation.json",
            },
        }
    )
    assert success.root.command == "setup"


def test_artifact_kind_details_stage_customer_and_parent_rules() -> None:
    manifest = _checkpoint()
    assert ArtifactManifest.model_validate(manifest).root.kind == "checkpoint"

    for update in (
        {"stage": "shared"},
        {"customer": None},
        {"kind": "quantized"},
    ):
        invalid = {**manifest, **update}
        invalid["artifactId"] = canonical_digest(
            {key: value for key, value in invalid.items() if key != "artifactId"}
        )
        with pytest.raises(ValidationError):
            ArtifactManifest.model_validate(invalid)

    parent = {key: value for key, value in manifest.items() if key != "schemaVersion"}
    invalid_parent = {**manifest, "parents": [parent]}
    invalid_parent["artifactId"] = canonical_digest(
        {key: value for key, value in invalid_parent.items() if key != "artifactId"}
    )
    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(invalid_parent)


def test_field_correctness_does_not_control_applicable_validity() -> None:
    prediction = Prediction.model_validate(
        {
            "id": "record-a",
            "sourceId": "source-a",
            "language": "en",
            "tags": [],
            "componentId": DIGEST,
            "groupIds": [DIGEST],
            "representative": True,
            "expected": '{"answer":"yes"}',
            "generated": '{"answer":"no"}',
            "expectedJson": {"answer": "yes"},
            "generatedJson": {"answer": "no"},
            "elapsedSeconds": 0.1,
            "generatedTokens": 4,
            "meanTokenLogprob": -0.2,
            "jsonValid": True,
            "finishReason": "stop",
            "schemaValid": True,
            "evidenceValid": None,
            "fieldPresent": {"/answer": True},
            "fieldValid": {"/answer": False},
            "applicableValid": True,
            "exactCorrect": False,
        }
    )
    assert prediction.applicableValid is True
    assert prediction.fieldValid == {"/answer": False}


def test_generated_schemas_are_valid_closed_draft_2020_12_documents() -> None:
    schema_dir = Path(__file__).parents[2] / "contracts" / "model"
    paths = sorted(schema_dir.glob("*.schema.json"))
    assert len(paths) == 25
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)

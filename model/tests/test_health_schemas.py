import json
from pathlib import Path

import pytest

from foliqant_model import doctor
from foliqant_model.cli import main
from foliqant_model.contracts import Prediction, WorkerRequest, WorkerResult
from foliqant_model.errors import ModelError
from foliqant_model.schemas import SCHEMAS, export_schemas

CURATION_SCHEMAS = {
    "candidate-job.schema.json",
    "candidate-outcome.schema.json",
    "category-catalog.schema.json",
    "curation-config.schema.json",
    "curation-plan.schema.json",
    "frozen-family-assignment.schema.json",
    "generation-provenance.schema.json",
}


def test_public_schema_roundtrip_and_readonly_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "schemas"
    assert main(["schema", "--output", str(output)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["result"]["schemaCount"] == len(SCHEMAS)
    export_schemas(output, check=True)
    target = output / "prediction.schema.json"
    target.write_text("{}")
    assert main(["schema", "--check", str(output)]) == 2
    response = capsys.readouterr()
    assert not response.out
    assert json.loads(response.err)["error"]["code"] == "SCHEMA_DRIFT"
    assert target.read_text() == "{}"
    with pytest.raises(ModelError, match="new or empty"):
        export_schemas(output)
    assert target.read_text() == "{}"


def test_curation_contracts_are_in_the_public_schema_registry() -> None:
    assert CURATION_SCHEMAS.issubset(SCHEMAS)
    for filename in CURATION_SCHEMAS:
        schema = SCHEMAS[filename].model_json_schema(mode="validation")
        assert schema["type"] == "object"


def test_schema_check_rejects_special_file_without_opening(tmp_path: Path) -> None:
    export_schemas(tmp_path)
    target = tmp_path / "prediction.schema.json"
    target.unlink()
    target.symlink_to(tmp_path / "missing")
    with pytest.raises(ModelError) as failure:
        export_schemas(tmp_path, check=True)
    assert failure.value.code == "SCHEMA_DRIFT"


@pytest.mark.parametrize("available", [False, True])
def test_doctor_maps_typed_probe_without_advertising_unimplemented_commands(
    available: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def probe(request: WorkerRequest, *, workspace: Path, timeout_seconds: int) -> WorkerResult:
        assert request.root.operation == "doctor"
        assert workspace.is_dir()
        assert timeout_seconds == 60
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": request.root.requestId,
                "operation": "doctor",
                "ok": True,
                "result": {
                    "mlxImportAvailable": available,
                    "metalAvailable": available,
                    "mlxVersion": "test-version" if available else None,
                    "mlxLmVersion": "test-version" if available else None,
                    "unavailableReason": None if available else "Unavailable in test environment",
                },
            }
        )

    monkeypatch.setattr(doctor, "run_worker", probe)
    result = doctor.diagnose(["doctor", "schema", "train", "export"])
    assert result.mlxAvailable == available
    assert result.mlxDeviceAvailable == available
    assert ("train" in result.availableCommands) == available
    assert "export" in result.availableCommands
    assert "curate" in doctor.diagnose(["curate"]).availableCommands
    assert {"doctor", "schema"}.issubset(result.availableCommands)


def test_valid_json_null_is_not_confused_with_invalid_json() -> None:
    row = Prediction.model_validate(
        {
            "id": "null-result",
            "sourceId": "test",
            "language": "en",
            "tags": [],
            "componentId": "a" * 64,
            "groupIds": ["a" * 64],
            "representative": True,
            "expected": "null",
            "generated": "null",
            "expectedJson": None,
            "generatedJson": None,
            "elapsedSeconds": 0.1,
            "generatedTokens": 1,
            "meanTokenLogprob": -0.1,
            "jsonValid": True,
            "finishReason": "stop",
            "schemaValid": True,
            "evidenceValid": None,
            "fieldPresent": {},
            "fieldValid": {},
            "applicableValid": True,
            "exactCorrect": True,
        }
    )
    assert row.jsonValid and row.generatedJson is None


def test_schema_generation_does_not_replace_a_concurrent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from foliqant_model import schemas

    original = os.open
    target = tmp_path / next(iter(SCHEMAS))

    def racing_open(path: object, flags: int, mode: int = 0o777) -> int:
        if Path(path) == target:
            target.write_bytes(b"created by another process")
        return original(path, flags, mode)

    monkeypatch.setattr(schemas.os, "open", racing_open)
    with pytest.raises(ModelError) as failure:
        export_schemas(tmp_path)
    assert failure.value.code == "OUTPUT_EXISTS"
    assert target.read_bytes() == b"created by another process"

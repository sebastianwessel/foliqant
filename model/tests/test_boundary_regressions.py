"""Independent negative cases derived from the model contract specification."""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import TypeAdapter, ValidationError

from foliqant_model.contracts.base import (
    CountRate,
    JsonPointer,
    MeasuredBytes,
    ProducerIdentity,
    SafePath,
    SourceRight,
)


@pytest.mark.parametrize(
    "value",
    ["a//b", "a/./b", "a/b/", "../data", "a/../data", "/data", "C:data", "https:data", "a\\b"],
)
def test_inventory_paths_reject_original_unsafe_segments(value: str) -> None:
    """Path normalization must not hide a forbidden original segment."""
    with pytest.raises(ValidationError):
        TypeAdapter(SafePath).validate_python(value)


@pytest.mark.parametrize("value", ["model.safetensors", "chats/train.jsonl", ".hidden/data.jsonl"])
def test_inventory_paths_keep_safe_relative_names(value: str) -> None:
    assert TypeAdapter(SafePath).validate_python(value) == value


def test_permission_is_a_boolean_not_the_number_one() -> None:
    with pytest.raises(ValidationError):
        SourceRight.model_validate(
            {
                "sourceId": "fixture",
                "license": "local-development",
                "licenseEvidence": "fixture declaration",
                "trainingAllowed": 1,
                "sharedTrainingAllowed": False,
                "redistributionAllowed": False,
                "privacy": "public",
                "attribution": "",
                "commercialUse": "unknown",
                "restrictions": [],
            }
        )


def test_absent_reason_is_omitted_but_required_null_measurement_is_retained() -> None:
    measured = MeasuredBytes.model_validate({"value": 7})
    assert measured.model_dump(mode="json") == {"value": 7}
    unavailable = MeasuredBytes.model_validate(
        {"value": None, "unavailableReason": "backend did not report memory"}
    )
    assert unavailable.model_dump(mode="json") == {
        "value": None,
        "unavailableReason": "backend did not report memory",
    }


def test_optional_reason_cannot_be_explicit_null_in_model_or_generated_schema() -> None:
    value = {"value": 7, "unavailableReason": None}
    with pytest.raises(ValidationError):
        MeasuredBytes.model_validate(value)
    with pytest.raises(SchemaValidationError):
        Draft202012Validator(MeasuredBytes.model_json_schema()).validate(value)


def test_available_code_identity_rejects_explicit_null_optional_reason() -> None:
    with pytest.raises(ValidationError):
        ProducerIdentity.model_validate(
            {
                "name": "foliqant-model",
                "version": "0.1.0",
                "command": "doctor",
                "pythonVersion": "3.12.11",
                "platform": "Darwin",
                "machine": "arm64",
                "codeRevision": "a" * 40,
                "codeDirty": False,
                "codeIdentityUnavailableReason": None,
                "components": [],
            }
        )


@pytest.mark.parametrize("value", ["not/a/pointer", "/bad~2escape", "/unfinished~"])
def test_invalid_pointer_escapes_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(JsonPointer).validate_python(value)


def test_empty_pointer_names_the_entire_document() -> None:
    assert TypeAdapter(JsonPointer).validate_python("") == ""


def test_counts_reject_booleans_and_inconsistent_rates() -> None:
    with pytest.raises(ValidationError):
        CountRate.model_validate({"eligibleCount": True, "passedCount": 1, "rate": 1.0})
    with pytest.raises(ValidationError):
        CountRate.model_validate({"eligibleCount": 3, "passedCount": 1, "rate": 0.5})
    assert (
        CountRate.model_validate({"eligibleCount": 3, "passedCount": 1, "rate": 1 / 3}).rate
        == 1 / 3
    )


def test_artifact_hash_rejects_file_replaced_by_fifo_without_blocking(tmp_path: Path) -> None:
    import subprocess
    import sys

    target = tmp_path / "racing-input"
    target.write_bytes(b"expected regular file")
    script = """
import os
import sys
from pathlib import Path
from foliqant_model import artifacts
from foliqant_model.errors import ModelError
target = Path(sys.argv[1])
original = os.open
def racing_open(path, flags, *args, **kwargs):
    if Path(path) == target:
        target.unlink()
        os.mkfifo(target)
    return original(path, flags, *args, **kwargs)
artifacts.os.open = racing_open
try:
    artifacts.sha256_file(target)
except ModelError as error:
    assert error.code == "INTEGRITY_FAILED"
else:
    raise AssertionError("special-file replacement was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(target)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr

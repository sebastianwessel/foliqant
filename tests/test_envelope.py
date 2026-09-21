"""Protected metadata and immutable engine boundary acceptance tests."""

import math

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.json import freeze_json, thaw_json


@pytest.mark.parametrize(
    "identity, expected",
    [
        (Identity(), {}),
        (Identity(tenant_id="org"), {"tenant_id": "org"}),
        (Identity(principal_id="user"), {"principal_id": "user"}),
        (
            Identity(tenant_id="org", principal_id="user"),
            {"tenant_id": "org", "principal_id": "user"},
        ),
    ],
)
def test_identity_fields_are_independent(identity: Identity, expected: dict[str, str]) -> None:
    accepted = accept_envelope(Envelope(payload={"message": "test"}), identity)
    assert thaw_json(accepted.metadata) == expected


@pytest.mark.parametrize("key", ["tenant_id", "principal_id"])
def test_unverified_body_identity_cannot_grant_authority(key: str) -> None:
    envelope = Envelope.model_validate({"payload": {}, "metadata": {key: "forged"}})
    with pytest.raises(ServiceError) as error:
        accept_envelope(envelope, Identity())
    assert error.value.code == ErrorCode.FORBIDDEN
    assert "forged" not in str(error.value)


@pytest.mark.parametrize("value", ["", "  ", "bad\nidentity", "x" * 257])
def test_invalid_trusted_identity_fails_with_a_safe_error(value: str) -> None:
    with pytest.raises(ServiceError) as error:
        accept_envelope(Envelope(payload=None), Identity(principal_id=value))
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_identity_mismatch_is_not_silently_overwritten() -> None:
    envelope = Envelope.model_validate({"payload": {}, "metadata": {"tenant_id": "other"}})
    with pytest.raises(ServiceError):
        accept_envelope(envelope, Identity(tenant_id="trusted"))


@pytest.mark.parametrize("value", [None, "", "   ", "a\nb", "a\x85b", "x" * 257, 42, True])
def test_invalid_protected_identity_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate({"payload": None, "metadata": {"tenant_id": value}})


def test_protected_field_schema_matches_omission_and_rejection_rules() -> None:
    schema = Envelope.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"payload": None, "metadata": {"custom": None}})
    assert validator.is_valid({"payload": None, "metadata": {"principal_id": "user"}})
    for bad in (None, "", "  ", "a\x85b"):
        assert not validator.is_valid({"payload": None, "metadata": {"tenant_id": bad}})
    for name in ("tenant_id", "principal_id", "telemetry"):
        assert "default" not in schema["$defs"]["Metadata"]["properties"][name]


def test_business_metadata_and_carrier_preserved_without_aliasing() -> None:
    value = {
        "payload": {"message": ["hello"]},
        "metadata": {
            "tenant_id": "org",
            "telemetry": {"traceparent": "invalid-carrier-is-handled-by-propagator"},
            "custom": {"nullable": None, "items": [1, True]},
        },
    }
    envelope = Envelope.model_validate(value)
    accepted = accept_envelope(envelope, Identity(tenant_id="org"))
    assert thaw_json(accepted.payload) == value["payload"]
    assert thaw_json(accepted.metadata) == value["metadata"]
    envelope.metadata.__pydantic_extra__["custom"] = "changed"
    assert thaw_json(accepted.metadata) == value["metadata"]
    with pytest.raises(TypeError):
        accepted.metadata["tenant_id"] = "changed"  # type: ignore[index]


def test_unknown_telemetry_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate({"payload": None, "metadata": {"telemetry": {"debug": True}}})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_json_rejected(value: float) -> None:
    with pytest.raises(ServiceError):
        freeze_json({"value": value})


def test_json_depth_is_bounded() -> None:
    nested: object = None
    for _ in range(66):
        nested = [nested]
    with pytest.raises(ServiceError):
        freeze_json(nested)


def test_closed_envelope_rejects_control_overrides() -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate({"payload": None, "debug": True})

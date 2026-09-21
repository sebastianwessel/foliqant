"""Untrusted JSON fails safely before it reaches typed workflow values."""

import pytest

from foliqant.contracts.decoding import decode_envelope
from foliqant.core.errors import ErrorCode, ServiceError


@pytest.mark.parametrize(
    "raw",
    [
        b'{"payload": 1, "payload": 2}',
        b'{"payload": {"secret": 1, "secret": 2}}',
        b'{"payload": null, "metadata": {"tenant_id": "one", "tenant_id": "two"}}',
        b'{"payload": NaN}',
        b'{"payload": Infinity}',
        b'{"payload": "PRIVATE_SENTINEL", "metadata": {"tenant_id": 42}}',
        b'{"payload": "PRIVATE_SENTINEL", "unexpected": true}',
        b"\xff",
        b'{"payload":',
    ],
)
def test_rejects_invalid_input_without_disclosing_contents(raw: bytes) -> None:
    with pytest.raises(ServiceError) as error:
        decode_envelope(raw)
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert error.value.__suppress_context__


def test_input_size_limit_applies_before_parsing() -> None:
    with pytest.raises(ServiceError) as error:
        decode_envelope(b'{"payload": "long"}', max_bytes=4)
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_exact_utf8_byte_limit_and_next_byte() -> None:
    raw = '{"payload":"Ä"}'.encode()
    assert decode_envelope(raw, max_bytes=len(raw)).payload == "Ä"
    with pytest.raises(ServiceError):
        decode_envelope(raw, max_bytes=len(raw) - 1)


def test_exact_depth_boundary_counts_the_envelope() -> None:
    decode_envelope(b'{"payload":' + b"[" * 63 + b"0" + b"]" * 63 + b"}")
    with pytest.raises(ServiceError):
        decode_envelope(b'{"payload":' + b"[" * 64 + b"0" + b"]" * 64 + b"}")


def test_deep_input_rejected_before_pydantic() -> None:
    with pytest.raises(ServiceError):
        decode_envelope(b'{"payload":' + b"[" * 70 + b"0" + b"]" * 70 + b"}")


def test_valid_unicode_and_explicit_null_business_values_preserved() -> None:
    value = decode_envelope('{"payload":"Überweisung 🏦","metadata":{"custom":null}}'.encode())
    assert value.model_dump(mode="json") == {
        "payload": "Überweisung 🏦",
        "metadata": {"custom": None},
    }

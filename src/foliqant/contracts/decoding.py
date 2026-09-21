"""Safe raw-JSON ingress; do not expose Pydantic exception contents to clients."""

import json

from pydantic import ValidationError

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import freeze_json

from .envelope import Envelope

MAX_ENVELOPE_BYTES = 1024 * 1024


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("nonfinite JSON number")


def decode_envelope(data: bytes, *, max_bytes: int = MAX_ENVELOPE_BYTES) -> Envelope:
    """Decode a bounded UTF-8 envelope with fixed safe errors.

    Transport adapters must also bound reads before buffering the complete body.
    Direct Pydantic validation is for trusted construction and may include input
    values in exceptions; it is not a replacement for this ingress boundary.
    """
    if max_bytes < 1 or len(data) > max_bytes:
        raise ServiceError(ErrorCode.INVALID_INPUT) from None
    try:
        decoded: object = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        freeze_json(decoded)
        return Envelope.model_validate(decoded, strict=True)
    except (UnicodeError, ValueError, RecursionError, ValidationError, ServiceError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None

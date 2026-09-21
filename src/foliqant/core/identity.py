"""Trusted identity supplied by ingress authentication, never by model output."""

import re
from dataclasses import dataclass

from .errors import ErrorCode, ServiceError

IDENTITY_MAX_LENGTH = 256
IDENTITY_PATTERN = r"^[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f][^\x00-\x1f\x7f-\x9f]*$"
_VALID_ID = re.compile(IDENTITY_PATTERN)


def validate_identity_id(value: object) -> str:
    """Check the shared ID constraints without granting authentication or access."""
    if (
        not isinstance(value, str)
        or len(value) > IDENTITY_MAX_LENGTH
        or not _VALID_ID.fullmatch(value)
    ):
        raise ServiceError(ErrorCode.INVALID_INPUT)
    return value


@dataclass(frozen=True, slots=True)
class Identity:
    """Independently optional organization and user identifiers.

    Construction is a trusted host operation, not token verification. A principal
    may be known without a tenant; neither identifier grants permission by itself.
    """

    tenant_id: str | None = None
    principal_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.principal_id):
            if value is not None:
                validate_identity_id(value)

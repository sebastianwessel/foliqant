"""Trusted identity supplied by ingress authentication, never by model output."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    """Independently optional organization and user identifiers.

    Construction is a trusted host operation, not token verification. A principal
    may be known without a tenant; neither identifier grants permission by itself.
    """

    tenant_id: str | None = None
    principal_id: str | None = None

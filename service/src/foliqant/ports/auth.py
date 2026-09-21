"""Replaceable async authentication boundary for ingress transports."""

from dataclasses import dataclass
from typing import Protocol

from foliqant.core.identity import Identity


@dataclass(frozen=True, slots=True)
class AuthenticatedCaller:
    """Trusted caller identity and operator-configured workflow grants."""

    identity: Identity
    workflows: frozenset[str]


class Authenticator(Protocol):
    """Authenticate one request without retaining request-scoped mutable state."""

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        """Return trusted caller data or raise a content-free service error."""
        ...


__all__ = ["AuthenticatedCaller", "Authenticator"]

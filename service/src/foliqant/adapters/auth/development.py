"""Explicit identity-free authenticator for loopback development transports."""

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.ports.auth import AuthenticatedCaller


class DevelopmentAuthenticator:
    """Return an empty trusted identity only when no credential was supplied.

    The HTTP composition layer is responsible for permitting this adapter only
    in development mode on a loopback listener.
    """

    def __init__(self, workflows: frozenset[str]) -> None:
        self._caller = AuthenticatedCaller(identity=Identity(), workflows=workflows)

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        """Authenticate the explicitly anonymous development caller."""
        if authorization is not None:
            raise ServiceError(ErrorCode.UNAUTHENTICATED)
        return self._caller


__all__ = ["DevelopmentAuthenticator"]

"""Constant-time static bearer authentication from a startup environment snapshot."""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from foliqant.contracts.auth import BearerAuthConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.ports.auth import AuthenticatedCaller

_MAX_TOKEN_BYTES = 8192


def _invalid_configuration() -> ServiceError:
    return ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _unauthenticated() -> ServiceError:
    return ServiceError(ErrorCode.UNAUTHENTICATED)


def _token_bytes(value: object, *, configuration: bool) -> bytes:
    error = _invalid_configuration if configuration else _unauthenticated
    if not isinstance(value, str) or not value or value != value.strip():
        raise error()
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise error() from None
    if len(encoded) > _MAX_TOKEN_BYTES or any(byte <= 32 or byte == 127 for byte in encoded):
        raise error()
    return encoded


def _authorization_token(authorization: str | None) -> bytes:
    if authorization is None:
        raise _unauthenticated()
    if authorization[:7].lower() != "bearer " or authorization.count(" ") != 1:
        raise _unauthenticated()
    return _token_bytes(authorization[7:], configuration=False)


@dataclass(frozen=True, slots=True, repr=False)
class _HashedBinding:
    digest: bytes
    caller: AuthenticatedCaller


class BearerAuthenticator:
    """Authenticate against secret hashes captured exactly once during startup."""

    def __init__(self, config: BearerAuthConfig, *, environment: Mapping[str, str]) -> None:
        try:
            validated = BearerAuthConfig.model_validate(
                config.model_dump(mode="python", exclude_unset=True), strict=True
            )
        except Exception:
            raise _invalid_configuration() from None

        bindings: list[_HashedBinding] = []
        seen: set[bytes] = set()
        for binding in validated.bindings.values():
            token = _token_bytes(environment.get(binding.token_env), configuration=True)
            digest = hashlib.sha256(token).digest()
            if digest in seen:
                raise _invalid_configuration()
            seen.add(digest)
            bindings.append(
                _HashedBinding(
                    digest=digest,
                    caller=AuthenticatedCaller(
                        identity=Identity(
                            tenant_id=binding.tenant_id,
                            principal_id=binding.principal_id,
                        ),
                        workflows=frozenset(binding.workflows),
                    ),
                )
            )
        self._bindings = tuple(bindings)

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        """Match one RFC bearer authorization value without exposing credentials."""
        digest = hashlib.sha256(_authorization_token(authorization)).digest()
        matched: AuthenticatedCaller | None = None
        # Compare every configured digest so the binding position does not affect
        # the number of constant-time comparisons.
        for binding in self._bindings:
            if hmac.compare_digest(digest, binding.digest):
                matched = binding.caller
        if matched is None:
            raise _unauthenticated()
        return matched


__all__ = ["BearerAuthenticator"]

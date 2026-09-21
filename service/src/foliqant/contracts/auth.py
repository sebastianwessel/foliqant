"""Strict deployment authentication policies containing secret references only."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Discriminator, Field, Tag, model_validator

from foliqant.core.identity import Identity

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .envelope import IdentityId
from .models import Duration, EnvironmentName
from .workflow import Id, NonBlank


class JwtAlgorithm(StrEnum):
    """Asymmetric JWT signature algorithms supported by the native adapter."""

    RS256 = "RS256"
    ES256 = "ES256"
    EDDSA = "EdDSA"


def _parse_jwt_algorithm(value: object) -> JwtAlgorithm:
    if isinstance(value, JwtAlgorithm):
        return value
    if isinstance(value, str):
        try:
            return JwtAlgorithm(value)
        except ValueError:
            pass
    raise ValueError("unsupported JWT algorithm")


JwtAlgorithmValue = Annotated[JwtAlgorithm, BeforeValidator(_parse_jwt_algorithm)]


class BearerBinding(BoundaryModel):
    """Environment credential reference and trusted grants for one bearer token."""

    token_env: EnvironmentName
    tenant_id: IdentityId | None = None
    principal_id: IdentityId | None = None
    workflows: Annotated[list[Id], Field(min_length=1, max_length=1024)]

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        for field in ("tenant_id", "principal_id"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("identity fields cannot be null")
        if len(set(self.workflows)) != len(self.workflows):
            raise ValueError("workflow grants must be unique")
        # Apply the same runtime identity validation used after JWT verification.
        Identity(tenant_id=self.tenant_id, principal_id=self.principal_id)
        return self


class BearerAuthConfig(BoundaryModel):
    """Static bearer bindings resolved once from the startup environment."""

    type: Literal["bearer"]
    bindings: Annotated[dict[Id, BearerBinding], Field(min_length=1, max_length=1024)]


class JwtAuthConfig(BoundaryModel):
    """Fixed issuer policy and bounded JWKS retrieval settings for JWT verification."""

    type: Literal["jwt"]
    issuer: Annotated[str, Field(max_length=2048)]
    audience: Annotated[NonBlank, Field(max_length=512)]
    algorithms: Annotated[list[JwtAlgorithmValue], Field(min_length=1, max_length=3)]
    jwks_url: Annotated[str, Field(max_length=2048)]
    tenant_claim: Literal["tenant_id"] = "tenant_id"
    principal_claim: Literal["sub"] = "sub"
    workflows: Annotated[list[Id], Field(min_length=1, max_length=1024)]
    jwks_cache_ttl: Duration = 300.0
    jwks_refresh_cooldown: Annotated[float, Field(strict=True, gt=0, le=300)] = 5.0
    request_timeout: Annotated[float, Field(strict=True, gt=0, le=30)] = 5.0
    max_jwks_bytes: Annotated[int, Field(strict=True, ge=256, le=4_194_304)] = 1_048_576
    max_cached_keys: Annotated[int, Field(strict=True, ge=1, le=1024)] = 128

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        validate_http_endpoint(self.issuer, allow_insecure_http=False)
        validate_http_endpoint(self.jwks_url, allow_insecure_http=False)
        if len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("JWT algorithms must be unique")
        if len(set(self.workflows)) != len(self.workflows):
            raise ValueError("workflow grants must be unique")
        return self


class DevelopmentAuthConfig(BoundaryModel):
    """Explicit unauthenticated mode; HTTP additionally restricts it to loopback."""

    type: Literal["development"]


def _auth_kind(value: object) -> str | None:
    if isinstance(value, Mapping):
        kind = value.get("type")
        return kind if isinstance(kind, str) else None
    kind = getattr(value, "type", None)
    return kind if isinstance(kind, str) else None


AuthConfig = Annotated[
    Annotated[BearerAuthConfig, Tag("bearer")]
    | Annotated[JwtAuthConfig, Tag("jwt")]
    | Annotated[DevelopmentAuthConfig, Tag("development")],
    Discriminator(_auth_kind),
]


__all__ = [
    "AuthConfig",
    "BearerAuthConfig",
    "BearerBinding",
    "DevelopmentAuthConfig",
    "JwtAlgorithm",
    "JwtAuthConfig",
]

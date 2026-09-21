"""Closed configuration for the synchronous HTTP workflow boundary."""

from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .auth import AuthConfig, DevelopmentAuthConfig
from .base import BoundaryModel

type DeploymentMode = Literal["production", "development"]

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024


class HttpConfig(BoundaryModel):
    """Bounded listener and authentication settings for the HTTP adapter."""

    model_config = ConfigDict(frozen=True)

    auth: AuthConfig
    host: Annotated[str, Field(min_length=1, max_length=255)] = "127.0.0.1"
    port: Annotated[int, Field(strict=True, ge=1, le=65_535)] = 8000
    max_body_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_BODY_BYTES)] = (
        DEFAULT_MAX_BODY_BYTES
    )
    body_timeout: Annotated[float, Field(gt=0, le=60)] = 10.0


def validate_http_mode(config: HttpConfig, mode: DeploymentMode) -> None:
    """Reject development authentication outside an explicit loopback deployment."""

    if mode not in {"production", "development"}:
        raise ValueError("unknown deployment mode")
    if not isinstance(config.auth, DevelopmentAuthConfig):
        return
    if mode != "development":
        raise ValueError("development HTTP authentication requires development mode")
    try:
        address = ip_address(config.host)
    except ValueError:
        raise ValueError(
            "development HTTP authentication requires a literal loopback address"
        ) from None
    if not address.is_loopback:
        raise ValueError("development HTTP authentication requires a loopback address")

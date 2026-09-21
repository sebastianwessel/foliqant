"""Strict MCP deployment profiles with reference-only authentication."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .models import Duration, EnvironmentName
from .workflow import DeclaredToolCatalog, Id, NonBlank

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024

ConfigText = Annotated[str, Field(max_length=8192, pattern=r"^[^\x00-\x1f\x7f]*$")]
IdentityMetaKey = Annotated[
    str,
    Field(
        min_length=5,
        max_length=256,
        pattern=(
            r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z]{2,63}/[A-Za-z0-9](?:[A-Za-z0-9._~/-]*[A-Za-z0-9._~-])?$"
        ),
    ),
]


class McpHttpTransport(BoundaryModel):
    """Current Streamable HTTP endpoint; HTTPS is the deployment default."""

    model_config = ConfigDict(frozen=True)

    type: Literal["streamable_http"]
    endpoint: NonBlank
    allow_insecure_http: bool = False

    @model_validator(mode="after")
    def valid_endpoint(self) -> Self:
        validate_http_endpoint(self.endpoint, allow_insecure_http=self.allow_insecure_http)
        return self


class McpStdioTransport(BoundaryModel):
    """Trusted child settings plus an overlay on the SDK's fixed safe environment."""

    model_config = ConfigDict(frozen=True)

    type: Literal["stdio"]
    command: Annotated[NonBlank, Field(max_length=4096)]
    args: Annotated[list[ConfigText], Field(max_length=256)] = Field(default_factory=list)
    cwd: Annotated[NonBlank, Field(max_length=4096)] | None = None
    env: Annotated[dict[EnvironmentName, ConfigText], Field(max_length=128)] = Field(
        default_factory=dict
    )

    @field_validator("command", "cwd")
    @classmethod
    def safe_process_text(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("stdio process fields cannot contain control characters")
        return value

    @field_validator("cwd")
    @classmethod
    def absolute_working_directory(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("stdio working directory must be absolute")
        return value


McpTransport = Annotated[
    McpHttpTransport | McpStdioTransport,
    Field(discriminator="type"),
]


class McpServerProfile(BoundaryModel):
    """One trusted server binding and its operator-reviewed host policy."""

    model_config = ConfigDict(frozen=True)

    transport: McpTransport
    auth: Id | None = None
    identity_meta_key: IdentityMetaKey | None = None
    catalog: DeclaredToolCatalog
    concurrency: Annotated[int, Field(strict=True, ge=1, le=1024)] = 4
    queue_limit: Annotated[int, Field(strict=True, ge=0, le=10_000)] = 16
    request_timeout: Duration = 30.0
    output_limit_bytes: Annotated[int, Field(strict=True, ge=1, le=_MAX_OUTPUT_BYTES)] = 1_048_576

    @model_validator(mode="after")
    def valid_server_policy(self) -> Self:
        if not self.catalog.tools:
            raise ValueError("MCP server catalog must declare at least one tool")
        if isinstance(self.transport, McpStdioTransport) and self.auth is not None:
            raise ValueError("stdio MCP servers cannot name an HTTP credential hook")
        return self


class McpProfiles(BoundaryModel):
    """Named MCP servers have no reserved alias or implicit endpoint discovery."""

    servers: Annotated[dict[Id, McpServerProfile], Field(min_length=1, max_length=128)]

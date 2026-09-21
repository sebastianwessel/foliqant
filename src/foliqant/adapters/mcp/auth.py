"""Identity-scoped HTTP authentication for MCP sessions.

The adapter deliberately exposes no access-token or browser-launch shortcut.
OAuth discovery, PKCE, state, issuer and resource processing remain owned by the
installed MCP SDK.  Host code supplies durable scoped storage and, when an
operator explicitly starts an interactive authorization, the two interaction
callbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata

from foliqant.core.identity import Identity
from foliqant.ports.execution import StepContext


@dataclass(frozen=True, slots=True)
class McpCredentialScope:
    """Stable token-partition inputs for one authenticated MCP identity.

    A storage implementation must partition records by every field.  In
    particular, an absent tenant is distinct from a present tenant and a token
    for one principal must never be returned for another principal.
    """

    server_alias: str
    endpoint: str
    auth_reference: str
    identity: Identity


@dataclass(frozen=True, slots=True)
class McpHttpAuthorization:
    """Per-session HTTP authentication and its complete network origin allowlist."""

    auth: httpx2.Auth
    allowed_origins: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_origins",
            frozenset(_canonical_https_origin(origin) for origin in self.allowed_origins),
        )


class McpCredentialProvider(Protocol):
    """Create fresh HTTP authorization for the current caller and run."""

    async def create_auth(
        self, *, scope: McpCredentialScope, context: StepContext
    ) -> McpHttpAuthorization: ...


class OAuthTokenStorageFactory(Protocol):
    """Resolve SDK token storage already partitioned for the supplied scope."""

    async def open(self, scope: McpCredentialScope) -> TokenStorage: ...


class OAuthOperatorInteraction(Protocol):
    """Explicit host UI boundary for an operator-initiated authorization flow."""

    async def present_authorization_url(
        self, url: str, scope: McpCredentialScope, context: StepContext
    ) -> None: ...

    async def wait_for_callback(
        self, scope: McpCredentialScope, context: StepContext
    ) -> AuthorizationCodeResult: ...


def _canonical_https_origin(value: str) -> str:
    """Return a normalized HTTPS origin, rejecting paths and credential material."""

    try:
        url = httpx2.URL(value)
    except Exception as error:
        raise ValueError("authorization server origin is invalid") from error
    if (
        url.scheme != "https"
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("authorization server origin must be an HTTPS origin")
    return str(url.copy_with(raw_path=b"/", query=None, fragment=None)).rstrip("/")


class SdkOAuthCredentialProvider:
    """Build one SDK OAuth provider per identity-scoped MCP session.

    ``allowed_authorization_server_origins`` is also enforced by the HTTP
    transport before every request.  This closes the SDK's intentionally open
    protected-resource discovery path without reimplementing that discovery.
    The configured endpoint origin is added separately by the transport.
    """

    def __init__(
        self,
        *,
        client_metadata: OAuthClientMetadata,
        storage_factory: OAuthTokenStorageFactory,
        allowed_authorization_server_origins: frozenset[str],
        operator: OAuthOperatorInteraction | None = None,
        client_metadata_url: str | None = None,
    ) -> None:
        if not allowed_authorization_server_origins:
            raise ValueError("at least one authorization server origin is required")
        self._client_metadata = client_metadata.model_copy(deep=True)
        self._storage_factory = storage_factory
        self._allowed_origins = frozenset(
            _canonical_https_origin(origin) for origin in allowed_authorization_server_origins
        )
        self._operator = operator
        self._client_metadata_url = client_metadata_url

    async def create_auth(
        self, *, scope: McpCredentialScope, context: StepContext
    ) -> McpHttpAuthorization:
        """Return a fresh SDK auth object; never retain caller or token state here."""

        storage = await self._storage_factory.open(scope)
        redirect_handler: Callable[[str], Awaitable[None]] | None = None
        callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]] | None = None
        operator = self._operator
        if operator is not None:

            async def redirect_handler(url: str) -> None:
                await operator.present_authorization_url(url, scope, context)

            async def callback_handler() -> AuthorizationCodeResult:
                return await operator.wait_for_callback(scope, context)

        auth = OAuthClientProvider(
            server_url=scope.endpoint,
            client_metadata=self._client_metadata.model_copy(deep=True),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            client_metadata_url=self._client_metadata_url,
        )
        return McpHttpAuthorization(auth=auth, allowed_origins=self._allowed_origins)


__all__ = [
    "McpCredentialProvider",
    "McpCredentialScope",
    "McpHttpAuthorization",
    "OAuthOperatorInteraction",
    "OAuthTokenStorageFactory",
    "SdkOAuthCredentialProvider",
]

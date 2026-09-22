"""Bounded, identity-scoped MCP client session ownership."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from types import MappingProxyType
from typing import Protocol

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.auth import OAuthFlowError, OAuthRegistrationError, OAuthTokenError
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from opentelemetry import baggage as otel_baggage
from opentelemetry import context as otel_context

from foliqant.contracts.mcp import (
    McpHttpTransport,
    McpProfiles,
    McpServerProfile,
    McpStdioTransport,
)
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.environment import EnvironmentResolver
from foliqant.ports.execution import StepContext

from .auth import McpCredentialProvider, McpCredentialScope, McpHttpAuthorization
from .retry import record_http_response

_SESSION_CLEANUP_TIMEOUT_SECONDS = 10.0


class McpSessionFactory(Protocol):
    """Open a fresh one-use SDK client for the current caller identity."""

    def open(
        self, server_alias: str, context: StepContext
    ) -> AbstractAsyncContextManager[Client]: ...


class McpHttpClientFactory(Protocol):
    """Trusted composition hook used to construct each owned HTTP pool."""

    def __call__(
        self,
        *,
        auth: httpx2.Auth | None,
        timeout: httpx2.Timeout,
        request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    ) -> httpx2.AsyncClient: ...


class _OriginDenied(Exception):
    """Internal content-free failure for an unconfigured network origin."""


def _origin(value: str | httpx2.URL) -> str:
    url = value if isinstance(value, httpx2.URL) else httpx2.URL(value)
    return str(url.copy_with(raw_path=b"/", query=None, fragment=None)).rstrip("/")


def _invalid_configuration() -> ServiceError:
    return ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _connection_error(error: BaseException) -> ServiceError:
    if isinstance(error, (OAuthFlowError, OAuthRegistrationError, OAuthTokenError)):
        return ServiceError(ErrorCode.UNAUTHENTICATED)
    if isinstance(error, _OriginDenied):
        return ServiceError(ErrorCode.FORBIDDEN)
    return ServiceError(ErrorCode.DEPENDENCY_FAILURE)


def _default_http_client_factory(
    *,
    auth: httpx2.Auth | None,
    timeout: httpx2.Timeout,
    request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        auth=auth,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        event_hooks={"request": request_hooks},
    )


class McpClientSessionFactory:
    """Create bounded HTTP or stdio sessions from trusted deployment profiles.

    Session admission is per configured server alias and spans the complete
    connection lifetime.  HTTP pools, SDK clients, OAuth state and stdio child
    processes are never shared between callers.  The SDK stdio transport always
    inherits its documented fixed safe environment baseline and overlays the
    profile's explicit values; this adapter does not mutate the process-wide
    environment to pretend it can provide an exact empty child environment.
    """

    def __init__(
        self,
        profiles: McpProfiles,
        *,
        credential_providers: Mapping[str, McpCredentialProvider],
        environment: Mapping[str, str] | None = None,
        http_client_factory: McpHttpClientFactory = _default_http_client_factory,
    ) -> None:
        try:
            validated = (
                EnvironmentResolver(environment or {}).resolve(profiles).model_copy(deep=True)
            )
        except Exception:
            raise _invalid_configuration() from None
        providers = dict(credential_providers)
        for profile in validated.servers.values():
            if profile.auth is not None and profile.auth not in providers:
                raise _invalid_configuration()
        self._profiles = validated
        self._credential_providers = MappingProxyType(providers)
        self._http_client_factory = http_client_factory
        self._limiters = {
            alias: CapacityLimiter(concurrency=profile.concurrency, queue_limit=profile.queue_limit)
            for alias, profile in validated.servers.items()
        }

    @asynccontextmanager
    async def open(self, server_alias: str, context: StepContext) -> AsyncIterator[Client]:
        """Enter and close one SDK session within bounded connection admission."""

        profile = self._profiles.servers.get(server_alias)
        if profile is None:
            raise ServiceError(ErrorCode.MISSING_BINDING)
        connect_timeout = min(float(profile.request_timeout), context.tool_timeout)
        limiter = self._limiters[server_alias]
        async with limiter.slot(deadline=context.deadline):
            connect_deadline = min(
                context.deadline,
                asyncio.get_running_loop().time() + connect_timeout,
            )
            stack = AsyncExitStack()
            active_error: BaseException | None = None
            telemetry_context = otel_context.get_current()
            for name in otel_baggage.get_all(telemetry_context):
                telemetry_context = otel_baggage.remove_baggage(name, context=telemetry_context)
            telemetry_token = otel_context.attach(telemetry_context)
            try:
                try:
                    async with asyncio.timeout_at(connect_deadline):
                        sdk_client = await self._build_client(
                            stack,
                            server_alias=server_alias,
                            profile=profile,
                            context=context,
                            request_timeout=connect_timeout,
                        )
                        connected = await stack.enter_async_context(sdk_client)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    raise ServiceError(ErrorCode.TIMEOUT) from None
                except ServiceError:
                    raise
                except Exception as error:
                    raise _connection_error(error) from None
                yield connected
            except BaseException as error:
                active_error = error
                raise
            finally:
                try:
                    try:
                        # AnyIO cancel scopes in the SDK must be exited by the
                        # task that entered them. asyncio.timeout keeps cleanup
                        # in this task and exceeds the SDK stdio transport's
                        # bounded flush/terminate/kill/reap sequence.
                        async with asyncio.timeout(_SESSION_CLEANUP_TIMEOUT_SECONDS):
                            await stack.aclose()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if active_error is None:
                            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None
                finally:
                    otel_context.detach(telemetry_token)

    async def _build_client(
        self,
        stack: AsyncExitStack,
        *,
        server_alias: str,
        profile: McpServerProfile,
        context: StepContext,
        request_timeout: float,
    ) -> Client:
        transport = profile.transport
        if isinstance(transport, McpHttpTransport):
            authorization = await self._http_authorization(
                server_alias=server_alias,
                profile=profile,
                transport=transport,
                context=context,
            )
            allowed_origins = {_origin(transport.endpoint)}
            auth: httpx2.Auth | None = None
            if authorization is not None:
                auth = authorization.auth
                allowed_origins.update(authorization.allowed_origins)

            async def enforce_origin(request: httpx2.Request) -> None:
                if _origin(request.url) not in allowed_origins:
                    raise _OriginDenied()

            http_client = self._http_client_factory(
                auth=auth,
                timeout=httpx2.Timeout(request_timeout),
                request_hooks=[enforce_origin],
            )
            http_client.event_hooks.setdefault("response", []).append(record_http_response)
            await stack.enter_async_context(http_client)
            sdk_transport = streamable_http_client(
                transport.endpoint,
                http_client=http_client,
                terminate_on_close=True,
            )
        elif isinstance(transport, McpStdioTransport):
            parameters = StdioServerParameters(
                command=transport.command,
                args=list(transport.args),
                env={key: value.get_secret_value() for key, value in transport.env.items()},
                cwd=transport.cwd,
            )
            # A child controls stderr content and volume. Discard it here until
            # the safe observation adapter provides a bounded sanitized sink.
            errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
            sdk_transport = stdio_client(parameters, errlog=errlog)
        else:  # pragma: no cover - closed Pydantic discriminator
            raise _invalid_configuration()
        return Client(
            sdk_transport,
            cache=None,
            mode="auto",
            read_timeout_seconds=request_timeout,
            input_required_max_rounds=0,
        )

    async def _http_authorization(
        self,
        *,
        server_alias: str,
        profile: McpServerProfile,
        transport: McpHttpTransport,
        context: StepContext,
    ) -> McpHttpAuthorization | None:
        if profile.auth is None:
            return None
        provider = self._credential_providers.get(profile.auth)
        if provider is None:  # defensive if the immutable registry ever changes construction
            raise _invalid_configuration()
        authorization = await provider.create_auth(
            scope=McpCredentialScope(
                server_alias=server_alias,
                endpoint=transport.endpoint,
                auth_reference=profile.auth,
                identity=context.caller.identity,
            ),
            context=context,
        )
        if not isinstance(authorization, McpHttpAuthorization):
            raise _invalid_configuration()
        return authorization


__all__ = ["McpClientSessionFactory", "McpHttpClientFactory", "McpSessionFactory"]

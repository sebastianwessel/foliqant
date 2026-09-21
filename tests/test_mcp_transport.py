"""MCP session authentication and lifecycle remain scoped and bounded."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from mcp.client.auth import TokenStorage
from mcp.server import MCPServer
from mcp.shared._otel import inject_trace_context
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from opentelemetry import baggage as otel_baggage
from opentelemetry import context as otel_context

import foliqant.adapters.mcp.transport as transport_module
from foliqant.adapters.mcp.auth import (
    McpCredentialScope,
    McpHttpAuthorization,
    SdkOAuthCredentialProvider,
)
from foliqant.adapters.mcp.transport import McpClientSessionFactory
from foliqant.contracts.mcp import McpProfiles
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.ports.execution import StepContext


def _profiles(*, auth: str | None = None, concurrency: int = 1) -> McpProfiles:
    return McpProfiles.model_validate(
        {
            "servers": {
                "tools": {
                    "transport": {
                        "type": "streamable_http",
                        "endpoint": "https://tools.example.test/mcp",
                    },
                    "auth": auth,
                    "catalog": {
                        "tools": {
                            "lookup": {
                                "input_schema": {"type": "object"},
                                "effect": "read",
                            }
                        }
                    },
                    "concurrency": concurrency,
                    "queue_limit": 0,
                    "request_timeout": 0.5,
                }
            }
        },
        strict=True,
    )


def _context(identity: Identity, *, tool_timeout: float = 0.5) -> StepContext:
    metadata = cast(FrozenObject, freeze_json({}))
    return StepContext(
        execution_id="execution",
        workflow="workflow",
        revision="a" * 64,
        step_id="call",
        caller=CallerContext(identity, metadata),
        deadline=asyncio.get_running_loop().time() + 2,
        model_timeout=1,
        tool_timeout=tool_timeout,
        budget=StepBudget(model_requests=0, tool_calls=1),
    )


class _MemoryStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class _StorageFactory:
    def __init__(self) -> None:
        self.scopes: list[McpCredentialScope] = []
        self.stores: list[_MemoryStorage] = []

    async def open(self, scope: McpCredentialScope) -> TokenStorage:
        self.scopes.append(scope)
        store = _MemoryStorage()
        self.stores.append(store)
        return store


async def test_sdk_oauth_provider_creates_isolated_identity_scoped_state() -> None:
    storage = _StorageFactory()
    provider = SdkOAuthCredentialProvider(
        client_metadata=OAuthClientMetadata.model_validate(
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "client_name": "Foliqant",
            }
        ),
        storage_factory=storage,
        allowed_authorization_server_origins=frozenset({"https://auth.example.test"}),
    )
    first_scope = McpCredentialScope(
        "tools", "https://tools.example.test/mcp", "oauth", Identity("one", "alice")
    )
    second_scope = McpCredentialScope(
        "tools", "https://tools.example.test/mcp", "oauth", Identity("two", "bob")
    )

    first = await provider.create_auth(scope=first_scope, context=_context(first_scope.identity))
    second = await provider.create_auth(scope=second_scope, context=_context(second_scope.identity))

    assert first.auth is not second.auth
    assert first.allowed_origins == frozenset({"https://auth.example.test"})
    assert storage.scopes == [first_scope, second_scope]
    assert storage.stores[0] is not storage.stores[1]
    assert first.auth.context.storage is storage.stores[0]  # type: ignore[attr-defined]
    assert second.auth.context.storage is storage.stores[1]  # type: ignore[attr-defined]
    assert first.auth.context.redirect_handler is None  # type: ignore[attr-defined]
    assert first.auth.context.callback_handler is None  # type: ignore[attr-defined]


class _Operator:
    def __init__(self) -> None:
        self.authorization_url: str | None = None
        self.scope: McpCredentialScope | None = None
        self.context: StepContext | None = None

    async def present_authorization_url(
        self, url: str, scope: McpCredentialScope, context: StepContext
    ) -> None:
        self.authorization_url = url
        self.scope = scope
        self.context = context

    async def wait_for_callback(
        self, scope: McpCredentialScope, context: StepContext
    ) -> AuthorizationCodeResult:
        assert self.authorization_url is not None
        assert scope is self.scope
        assert context is self.context
        state = parse_qs(urlparse(self.authorization_url).query)["state"][0]
        return AuthorizationCodeResult(code="operator-code", state=state)


async def test_sdk_oauth_wire_flow_uses_discovery_pkce_resource_and_refresh() -> None:
    storage = _StorageFactory()
    operator = _Operator()
    provider = SdkOAuthCredentialProvider(
        client_metadata=OAuthClientMetadata.model_validate(
            {
                "redirect_uris": ["http://127.0.0.1:8765/callback"],
                "client_name": "Foliqant",
            }
        ),
        storage_factory=storage,
        allowed_authorization_server_origins=frozenset({"https://auth.example.test"}),
        operator=operator,
    )
    identity = Identity("tenant", "alice")
    scope = McpCredentialScope("tools", "https://tools.example.test/mcp", "oauth", identity)
    context = _context(identity)
    authorization = await provider.create_auth(scope=scope, context=context)
    requests: list[httpx2.Request] = []
    token_forms: list[dict[str, list[str]]] = []
    access_token = "first-access"

    async def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal access_token
        requests.append(request)
        path = request.url.path
        if request.url == httpx2.URL(scope.endpoint):
            if request.headers.get("Authorization") == f"Bearer {access_token}":
                return httpx2.Response(200, request=request, json={"ok": True})
            return httpx2.Response(
                401,
                request=request,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="https://tools.example.test/'
                        '.well-known/oauth-protected-resource/mcp"'
                    )
                },
            )
        if "oauth-protected-resource" in path:
            return httpx2.Response(
                200,
                request=request,
                json={
                    "resource": scope.endpoint,
                    "authorization_servers": ["https://auth.example.test"],
                },
            )
        if "oauth-authorization-server" in path:
            return httpx2.Response(
                200,
                request=request,
                json={
                    "issuer": "https://auth.example.test",
                    "authorization_endpoint": "https://auth.example.test/authorize",
                    "token_endpoint": "https://auth.example.test/token",
                    "registration_endpoint": "https://auth.example.test/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if path == "/register":
            registration = json.loads(request.content)
            assert "access_token" not in registration
            return httpx2.Response(
                201,
                request=request,
                json={
                    "client_id": "foliqant-client",
                    "redirect_uris": ["http://127.0.0.1:8765/callback"],
                    "token_endpoint_auth_method": "none",
                },
            )
        if path == "/token":
            form = parse_qs(request.content.decode("ascii"))
            token_forms.append(form)
            if form["grant_type"] == ["refresh_token"]:
                access_token = "refreshed-access"
            return httpx2.Response(
                200,
                request=request,
                json={
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "refresh-token",
                },
            )
        return httpx2.Response(404, request=request)

    allowed_hosts = {"tools.example.test", "auth.example.test"}

    async def enforce_allowlist(request: httpx2.Request) -> None:
        assert request.url.host in allowed_hosts

    async with httpx2.AsyncClient(
        auth=authorization.auth,
        transport=httpx2.MockTransport(respond),
        event_hooks={"request": [enforce_allowlist]},
    ) as client:
        response = await client.get(scope.endpoint, headers={"MCP-Protocol-Version": "2026-07-28"})
        assert response.status_code == 200
        oauth = authorization.auth
        oauth.context.token_expiry_time = time.time() - 1  # type: ignore[attr-defined]
        refreshed = await client.get(scope.endpoint, headers={"MCP-Protocol-Version": "2026-07-28"})
        assert refreshed.status_code == 200

    assert operator.scope == scope
    assert operator.context is context
    assert operator.authorization_url is not None
    authorization_query = parse_qs(urlparse(operator.authorization_url).query)
    assert authorization_query["resource"] == [scope.endpoint]
    assert authorization_query["code_challenge_method"] == ["S256"]
    verifier = token_forms[0]["code_verifier"][0]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert authorization_query["code_challenge"] == [expected_challenge]
    assert token_forms[0]["resource"] == [scope.endpoint]
    assert token_forms[0]["grant_type"] == ["authorization_code"]
    assert token_forms[1]["grant_type"] == ["refresh_token"]
    assert storage.stores[0].tokens is not None
    assert storage.stores[0].tokens.access_token == "refreshed-access"
    assert all(request.url.host in allowed_hosts for request in requests)


@pytest.mark.parametrize(
    "origin",
    [
        "http://auth.example.test",
        "https://user:secret@auth.example.test",
        "https://auth.example.test/path",
        "https://auth.example.test?token=secret",
    ],
)
def test_oauth_authorization_server_allowlist_accepts_only_https_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        SdkOAuthCredentialProvider(
            client_metadata=OAuthClientMetadata.model_validate(
                {"redirect_uris": ["http://127.0.0.1:8765/callback"]}
            ),
            storage_factory=_StorageFactory(),
            allowed_authorization_server_origins=frozenset({origin}),
        )


@dataclass
class _FakeSdkTransport:
    endpoint: str
    http_client: httpx2.AsyncClient


class _FakeClient:
    entered = 0
    exited = 0
    fail_exit = False
    hang_exit = False

    def __init__(self, server: _FakeSdkTransport, **settings: Any) -> None:
        self.server = server
        self.settings = settings

    async def __aenter__(self) -> _FakeClient:
        type(self).entered += 1
        response = await self.server.http_client.get(self.server.endpoint)
        response.raise_for_status()
        return self

    async def __aexit__(self, *args: object) -> None:
        type(self).exited += 1
        if type(self).hang_exit:
            await asyncio.Event().wait()
        if type(self).fail_exit:
            raise ExceptionGroup("sdk cleanup", [RuntimeError("private server failure")])


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.entered = _FakeClient.exited = 0
    _FakeClient.fail_exit = False
    _FakeClient.hang_exit = False
    monkeypatch.setattr(transport_module, "Client", _FakeClient)
    monkeypatch.setattr(
        transport_module,
        "streamable_http_client",
        lambda endpoint, *, http_client, terminate_on_close: _FakeSdkTransport(
            endpoint, http_client
        ),
    )


def _mock_http_factory(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[
    Callable[..., httpx2.AsyncClient],
    list[httpx2.AsyncClient],
]:
    clients: list[httpx2.AsyncClient] = []

    def create(
        *,
        auth: httpx2.Auth | None,
        timeout: httpx2.Timeout,
        request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    ) -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(
            auth=auth,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": request_hooks},
            transport=httpx2.MockTransport(handler),
        )
        clients.append(client)
        return client

    return create, clients


async def test_http_factory_owns_fresh_clients_and_bounds_session_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    factory_hook, clients = _mock_http_factory(
        lambda request: httpx2.Response(200, request=request)
    )
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=factory_hook
    )
    release = asyncio.Event()
    entered = asyncio.Event()

    async def hold() -> None:
        async with factory.open("tools", _context(Identity("tenant", "alice"))):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    with pytest.raises(ServiceError) as raised:
        async with factory.open("tools", _context(Identity("tenant", "bob"))):
            pytest.fail("saturated server session was admitted")
    assert raised.value.code == ErrorCode.CAPACITY_EXCEEDED
    release.set()
    await task

    async with factory.open("tools", _context(Identity("tenant", "bob"))):
        pass
    assert len(clients) == 2
    assert clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)
    assert _FakeClient.entered == _FakeClient.exited == 2
    assert all(client._trust_env is False for client in clients)


async def test_real_sdk_streamable_http_session_connects_calls_and_closes() -> None:
    server = MCPServer("test-tools")

    @server.tool()
    async def echo(value: str) -> str:
        return value

    app = server.streamable_http_app(stateless_http=True, host="tools.example.test")
    clients: list[httpx2.AsyncClient] = []

    def create_client(
        *,
        auth: httpx2.Auth | None,
        timeout: httpx2.Timeout,
        request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    ) -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(
            auth=auth,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": request_hooks},
            transport=httpx2.ASGITransport(app=app),
        )
        clients.append(client)
        return client

    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=create_client
    )
    async with app.router.lifespan_context(app):
        async with factory.open("tools", _context(Identity("tenant", "alice"))) as client:
            page = await client.list_tools()
            assert [tool.name for tool in page.tools] == ["echo"]
            result = await client.call_tool("echo", {"value": "hello"})
            assert result.content[0].text == "hello"  # type: ignore[union-attr]

    assert len(clients) == 1
    assert clients[0].is_closed


async def test_real_sdk_hanging_http_termination_is_bounded_in_entering_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MCPServer("test-tools")

    @server.tool()
    async def echo(value: str) -> str:
        return value

    app = server.streamable_http_app(host="tools.example.test")
    clients: list[httpx2.AsyncClient] = []

    async def hanging_delete(scope: Any, receive: Any, send: Any) -> None:
        if scope.get("method") == "DELETE":
            await asyncio.Event().wait()
        messages: list[dict[str, Any]] = []
        if scope.get("method") == "POST":
            while True:
                message = await receive()
                messages.append(message)
                if not message.get("more_body", False):
                    break
            body = b"".join(message.get("body", b"") for message in messages)
            request = json.loads(body)
            if request.get("method") == "server/discover":
                response = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": response})
                return

            async def replay() -> dict[str, Any]:
                if messages:
                    return messages.pop(0)
                return {"type": "http.disconnect"}

            receive = replay
        await app(scope, receive, send)

    def create_client(
        *,
        auth: httpx2.Auth | None,
        timeout: httpx2.Timeout,
        request_hooks: list[Callable[[httpx2.Request], Awaitable[None]]],
    ) -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(
            auth=auth,
            timeout=timeout,
            event_hooks={"request": request_hooks},
            transport=httpx2.ASGITransport(app=hanging_delete),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(transport_module, "_SESSION_CLEANUP_TIMEOUT_SECONDS", 0.01)
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=create_client
    )
    async with app.router.lifespan_context(app):
        with pytest.raises(ServiceError) as raised:
            async with factory.open("tools", _context(Identity("tenant", "alice"))) as client:
                result = await client.call_tool("echo", {"value": "hello"})
                assert result.content
        assert raised.value.code == ErrorCode.DEPENDENCY_FAILURE

    assert clients[0].is_closed


class _HangingCredentialProvider:
    async def create_auth(
        self, *, scope: McpCredentialScope, context: StepContext
    ) -> McpHttpAuthorization:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_credential_resolution_is_inside_connection_deadline() -> None:
    factory = McpClientSessionFactory(
        _profiles(auth="oauth"),
        credential_providers={"oauth": _HangingCredentialProvider()},
    )
    with pytest.raises(ServiceError) as raised:
        async with factory.open("tools", _context(Identity("tenant", "alice"), tool_timeout=0.01)):
            pytest.fail("hanging credential provider was admitted")
    assert raised.value.code == ErrorCode.TIMEOUT


async def test_body_failure_and_cancellation_survive_sdk_cleanup_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    _FakeClient.fail_exit = True
    hook, clients = _mock_http_factory(lambda request: httpx2.Response(200, request=request))
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=hook
    )

    body_error = RuntimeError("application control flow")
    with pytest.raises(RuntimeError) as raised:
        async with factory.open("tools", _context(Identity("tenant", "alice"))):
            raise body_error
    assert raised.value is body_error
    assert clients[-1].is_closed

    entered = asyncio.Event()

    async def wait_forever() -> None:
        async with factory.open("tools", _context(Identity("tenant", "bob"))):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[-1].is_closed


async def test_cleanup_failure_without_body_error_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    _FakeClient.fail_exit = True
    hook, clients = _mock_http_factory(lambda request: httpx2.Response(200, request=request))
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=hook
    )

    with pytest.raises(ServiceError) as raised:
        async with factory.open("tools", _context(Identity("tenant", "alice"))):
            pass
    assert raised.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert str(raised.value) == "A required dependency is unavailable."
    assert clients[0].is_closed


async def test_cleanup_timeout_remains_same_task_and_closes_later_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    _FakeClient.hang_exit = True
    monkeypatch.setattr(transport_module, "_SESSION_CLEANUP_TIMEOUT_SECONDS", 0.01)
    hook, clients = _mock_http_factory(lambda request: httpx2.Response(200, request=request))
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=hook
    )

    with pytest.raises(ServiceError) as raised:
        async with factory.open("tools", _context(Identity("tenant", "alice"))):
            pass
    assert raised.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert clients[0].is_closed


async def test_session_removes_baggage_without_changing_the_ambient_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    hook, _ = _mock_http_factory(lambda request: httpx2.Response(200, request=request))
    factory = McpClientSessionFactory(
        _profiles(), credential_providers={}, http_client_factory=hook
    )
    baggage_context = otel_baggage.set_baggage("private-caller-data", "must-not-forward")
    baggage_token = otel_context.attach(baggage_context)
    try:
        assert otel_baggage.get_baggage("private-caller-data") == "must-not-forward"
        async with factory.open("tools", _context(Identity("tenant", "alice"))):
            assert otel_baggage.get_baggage("private-caller-data") is None
            carrier: dict[str, object] = {}
            inject_trace_context(carrier)
            assert "baggage" not in carrier
        assert otel_baggage.get_baggage("private-caller-data") == "must-not-forward"
    finally:
        otel_context.detach(baggage_token)


class _CrossOriginAuth(httpx2.Auth):
    def __init__(self, target: str) -> None:
        self.target = target

    async def async_auth_flow(self, request: httpx2.Request):  # type: ignore[no-untyped-def]
        response = yield request
        if response.status_code == 401:
            yield httpx2.Request("GET", self.target)


class _StaticCredentialProvider:
    def __init__(self, target: str, allowed_origins: frozenset[str]) -> None:
        self.target = target
        self.allowed_origins = allowed_origins
        self.scopes: list[McpCredentialScope] = []

    async def create_auth(
        self, *, scope: McpCredentialScope, context: StepContext
    ) -> McpHttpAuthorization:
        self.scopes.append(scope)
        assert scope.identity == context.caller.identity
        return McpHttpAuthorization(
            auth=_CrossOriginAuth(self.target),
            allowed_origins=self.allowed_origins,
        )


async def test_http_auth_requests_cannot_leave_configured_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch)
    seen: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        status = 401 if request.url.host == "tools.example.test" else 200
        return httpx2.Response(status, request=request)

    hook, _ = _mock_http_factory(respond)
    denied_provider = _StaticCredentialProvider(
        "https://untrusted.example.test/authorize",
        frozenset({"https://auth.example.test"}),
    )
    denied = McpClientSessionFactory(
        _profiles(auth="oauth"),
        credential_providers={"oauth": denied_provider},
        http_client_factory=hook,
    )
    with pytest.raises(ServiceError) as raised:
        async with denied.open("tools", _context(Identity("tenant", "alice"))):
            pytest.fail("untrusted OAuth origin was contacted")
    assert raised.value.code == ErrorCode.FORBIDDEN
    assert seen == ["https://tools.example.test/mcp"]

    seen.clear()
    allowed_provider = _StaticCredentialProvider(
        "https://auth.example.test/authorize",
        frozenset({"https://auth.example.test"}),
    )
    allowed = McpClientSessionFactory(
        _profiles(auth="oauth"),
        credential_providers={"oauth": allowed_provider},
        http_client_factory=hook,
    )
    async with allowed.open("tools", _context(Identity("tenant", "bob"))):
        pass
    assert seen == [
        "https://tools.example.test/mcp",
        "https://auth.example.test/authorize",
    ]
    assert allowed_provider.scopes[0].identity == Identity("tenant", "bob")

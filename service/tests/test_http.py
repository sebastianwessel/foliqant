"""The HTTP adapter keeps authentication, identity, body, and task state isolated."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import httpx2
import pytest
from pydantic import ValidationError

from foliqant.adapters.transports.http import create_http_app
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.contracts.http import HttpConfig, validate_http_mode
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.json import thaw_json
from foliqant.ports.auth import AuthenticatedCaller
from foliqant.ports.observation import TraceContext


def _config(*, max_body_bytes: int = 1024 * 1024) -> HttpConfig:
    return HttpConfig.model_validate(
        {
            "auth": {"type": "development"},
            "max_body_bytes": max_body_bytes,
        },
        strict=True,
    )


def _usage() -> dict[str, object]:
    return {
        "model_requests": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def _result(
    envelope: Envelope,
    identity: Identity,
    *,
    status: str = "completed",
    code: ErrorCode = ErrorCode.DEPENDENCY_FAILURE,
    retryable: bool = False,
) -> ExecutionResult:
    accepted = accept_envelope(envelope, identity)
    execution: dict[str, object] = {
        "id": "run-1",
        "workflow": "inbox",
        "revision": "revision-1",
        "status": status,
        "usage": _usage(),
    }
    if status in {"failed", "cancelled"}:
        execution["error"] = {
            "code": code.value,
            "message": str(ServiceError(code)),
            "retryable": retryable,
        }
    return ExecutionResult.model_validate(
        {
            "payload": thaw_json(accepted.payload),
            "metadata": thaw_json(accepted.metadata),
            "decisions": {},
            "execution": execution,
        },
        strict=True,
    )


class _Authenticator:
    def __init__(
        self,
        callers: dict[str | None, AuthenticatedCaller] | None = None,
        *,
        error: ServiceError | None = None,
    ) -> None:
        self.callers = callers or {None: AuthenticatedCaller(Identity(), frozenset({"inbox"}))}
        self.error = error
        self.seen: list[str | None] = []

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        self.seen.append(authorization)
        if self.error is not None:
            raise self.error
        try:
            return self.callers[authorization]
        except KeyError:
            raise ServiceError(ErrorCode.UNAUTHENTICATED) from None


class _Application:
    workflow_names = ("inbox",)

    def __init__(self, *, status: str = "completed", ready: bool = True) -> None:
        self.status = status
        self.ready = ready
        self.calls: list[tuple[str, Envelope, Identity, TraceContext | None]] = []

    async def run(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        self.calls.append((workflow, envelope, identity, transport_trace))
        return _result(envelope, identity, status=self.status)


def _client(
    application: _Application,
    authenticator: _Authenticator,
    *,
    config: HttpConfig | None = None,
) -> httpx2.AsyncClient:
    app = create_http_app(
        application,
        authenticator,
        config=config or _config(),
        mode="development",
    )
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("status", ["completed", "needs_review"])
async def test_terminal_business_results_are_returned_synchronously(status: str) -> None:
    application = _Application(status=status)
    async with _client(application, _Authenticator()) as client:
        response = await client.post("/workflows/inbox/runs", json={"payload": {"invoice": 7}})

    assert response.status_code == 200
    assert response.json()["execution"]["status"] == status
    assert response.json()["payload"] == {"invoice": 7}
    assert len(application.calls) == 1


async def test_failed_result_is_problem_with_preserved_caller_owned_result() -> None:
    class FailingApplication(_Application):
        async def run(
            self,
            workflow: str,
            envelope: Envelope,
            *,
            identity: Identity,
            transport_trace: TraceContext | None = None,
        ) -> ExecutionResult:
            return _result(
                envelope,
                identity,
                status="failed",
                code=ErrorCode.DEPENDENCY_FAILURE,
                retryable=True,
            )

    async with _client(FailingApplication(), _Authenticator()) as client:
        response = await client.post("/workflows/inbox/runs", json={"payload": "caller-owned"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "dependency_failure"
    assert problem["retryable"] is True
    assert problem["result"]["payload"] == "caller-owned"
    assert problem["result"]["execution"]["status"] == "failed"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.INVALID_CONFIGURATION, 503),
        (ErrorCode.INVALID_INPUT, 422),
        (ErrorCode.INVALID_OUTPUT, 502),
        (ErrorCode.UNAUTHENTICATED, 401),
        (ErrorCode.FORBIDDEN, 403),
        (ErrorCode.NOT_FOUND, 404),
        (ErrorCode.MISSING_BINDING, 422),
        (ErrorCode.TIMEOUT, 504),
        (ErrorCode.BUDGET_EXHAUSTED, 422),
        (ErrorCode.DEPENDENCY_FAILURE, 503),
        (ErrorCode.CONFLICT, 409),
        (ErrorCode.UNCERTAIN_EFFECT, 503),
        (ErrorCode.CANCELLED, 503),
        (ErrorCode.CAPACITY_EXCEEDED, 429),
    ],
)
async def test_boundary_error_status_mapping_is_stable(code: ErrorCode, status: int) -> None:
    class ErrorApplication(_Application):
        async def run(
            self,
            workflow: str,
            envelope: Envelope,
            *,
            identity: Identity,
            transport_trace: TraceContext | None = None,
        ) -> ExecutionResult:
            raise ServiceError(code, retryable=True)

    async with _client(ErrorApplication(), _Authenticator()) as client:
        response = await client.post("/workflows/inbox/runs", json={"payload": None})
    assert response.status_code == status
    assert response.json()["code"] == code.value
    assert response.json()["retryable"] is True
    assert "result" not in response.json()
    assert (response.headers.get("retry-after") == "1") is (code is ErrorCode.CAPACITY_EXCEEDED)


async def test_unknown_application_exception_maps_to_fixed_503() -> None:
    class BrokenApplication(_Application):
        async def run(
            self,
            workflow: str,
            envelope: Envelope,
            *,
            identity: Identity,
            transport_trace: TraceContext | None = None,
        ) -> ExecutionResult:
            raise RuntimeError("PRIVATE dependency body")

    async with _client(BrokenApplication(), _Authenticator()) as client:
        response = await client.post("/workflows/inbox/runs", json={"payload": "PRIVATE input"})
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_failure"
    assert "PRIVATE" not in response.text


async def test_authenticated_identity_is_independent_of_body_and_impersonation_is_denied() -> None:
    caller = AuthenticatedCaller(
        Identity(tenant_id="trusted-tenant", principal_id="trusted-user"),
        frozenset({"inbox"}),
    )
    auth = _Authenticator({"Bearer secret": caller})
    application = _Application()
    async with _client(application, auth) as client:
        accepted = await client.post(
            "/workflows/inbox/runs",
            headers={"Authorization": "Bearer secret"},
            json={"payload": {}, "metadata": {"business": "kept"}},
        )
        rejected = await client.post(
            "/workflows/inbox/runs",
            headers={
                "Authorization": "Bearer secret",
                "X-Tenant-Id": "attacker-header",
                "X-Principal-Id": "attacker-header",
            },
            json={"payload": {}, "metadata": {"tenant_id": "attacker-body"}},
        )

    assert accepted.status_code == 200
    assert accepted.json()["metadata"] == {
        "tenant_id": "trusted-tenant",
        "principal_id": "trusted-user",
        "business": "kept",
    }
    assert rejected.status_code == 403
    assert len(application.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        b'{"payload":1,"payload":2}',
        b'{"payload":NaN}',
        b'{"payload":1,"unknown":2}',
        b"\xff",
    ],
)
async def test_strict_json_failures_are_safe_422(content: bytes) -> None:
    async with _client(_Application(), _Authenticator()) as client:
        response = await client.post(
            "/workflows/inbox/runs",
            headers={"Content-Type": "application/json"},
            content=content,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert set(response.json()) == {
        "type",
        "title",
        "status",
        "detail",
        "code",
        "retryable",
    }
    decoded = content.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in response.text


async def test_body_limit_applies_to_declared_and_streamed_bytes() -> None:
    config = _config(max_body_bytes=16)
    async with _client(_Application(), _Authenticator(), config=config) as client:
        declared = await client.post(
            "/workflows/inbox/runs",
            headers={"Content-Type": "application/json", "Content-Length": "17"},
            content=b"{}",
        )

        async def chunks() -> AsyncIterator[bytes]:
            yield b'{"payload":"'
            yield b"too-large-value"
            yield b'"}'

        streamed = await client.post(
            "/workflows/inbox/runs",
            headers={"Content-Type": "application/json"},
            content=chunks(),
        )

    assert declared.status_code == 413
    assert streamed.status_code == 413


async def test_body_read_timeout_is_bounded_without_starting_workflow() -> None:
    application = _Application()
    config = HttpConfig.model_validate(
        {
            "auth": {"type": "development"},
            "body_timeout": 0.01,
        },
        strict=True,
    )

    async def delayed() -> AsyncIterator[bytes]:
        yield b'{"payload":'
        await asyncio.sleep(1)
        yield b"null}"

    async with _client(application, _Authenticator(), config=config) as client:
        response = await client.post(
            "/workflows/inbox/runs",
            headers={"Content-Type": "application/json"},
            content=delayed(),
        )
    assert response.status_code == 504
    assert response.json()["code"] == "timeout"
    assert application.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        [(b"authorization", b"one"), (b"authorization", b"two")],
        [(b"content-length", b"2"), (b"content-length", b"2")],
        [(b"traceparent", b"one"), (b"traceparent", b"two")],
        [(b"tracestate", b"one"), (b"tracestate", b"two")],
    ],
)
async def test_security_relevant_duplicate_headers_are_rejected(
    headers: list[tuple[bytes, bytes]],
) -> None:
    request_headers = [(b"content-type", b"application/json"), *headers]
    async with _client(_Application(), _Authenticator()) as client:
        response = await client.post(
            "/workflows/inbox/runs", headers=request_headers, content=b"{}"
        )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


async def test_authentication_and_grant_checks_precede_body_read_and_existence() -> None:
    body_read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal body_read
        body_read = True
        yield b"PRIVATE"

    denied_auth = _Authenticator(error=ServiceError(ErrorCode.UNAUTHENTICATED))
    async with _client(_Application(), denied_auth) as client:
        unauthenticated = await client.post(
            "/workflows/inbox/runs",
            headers={"Content-Type": "application/json"},
            content=body(),
        )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert body_read is False

    no_grants = _Authenticator({None: AuthenticatedCaller(Identity(), frozenset())})
    async with _client(_Application(), no_grants) as client:
        forbidden = await client.post("/workflows/unknown/runs", json={"payload": None})
    assert forbidden.status_code == 403

    unavailable_but_granted = _Authenticator(
        {None: AuthenticatedCaller(Identity(), frozenset({"unknown"}))}
    )
    async with _client(_Application(), unavailable_but_granted) as client:
        missing = await client.post("/workflows/unknown/runs", json={"payload": None})
    assert missing.status_code == 404


async def test_trace_headers_remain_a_separate_transport_carrier() -> None:
    application = _Application()
    async with _client(application, _Authenticator()) as client:
        response = await client.post(
            "/workflows/inbox/runs",
            headers={
                "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
                "tracestate": "transport=value",
            },
            json={
                "payload": None,
                "metadata": {
                    "telemetry": {
                        "traceparent": ("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"),
                        "tracestate": "body=value",
                    }
                },
            },
        )

    assert response.status_code == 200
    trace = application.calls[0][3]
    assert trace == TraceContext(
        "00-11111111111111111111111111111111-2222222222222222-01",
        "transport=value",
    )
    assert response.json()["metadata"]["telemetry"]["tracestate"] == "body=value"


async def test_request_cancellation_reaches_workflow_run() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingApplication(_Application):
        async def run(
            self,
            workflow: str,
            envelope: Envelope,
            *,
            identity: Identity,
            transport_trace: TraceContext | None = None,
        ) -> ExecutionResult:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable")

    async with _client(WaitingApplication(), _Authenticator()) as client:
        request = asyncio.create_task(client.post("/workflows/inbox/runs", json={"payload": None}))
        await asyncio.wait_for(started.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.wait_for(cancelled.wait(), timeout=1)


async def test_overlapping_runs_keep_identity_and_result_isolated() -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    seen: list[Identity] = []

    class ConcurrentApplication(_Application):
        async def run(
            self,
            workflow: str,
            envelope: Envelope,
            *,
            identity: Identity,
            transport_trace: TraceContext | None = None,
        ) -> ExecutionResult:
            seen.append(identity)
            if len(seen) == 2:
                both_started.set()
            await release.wait()
            return _result(envelope, identity)

    auth = _Authenticator(
        {
            "Bearer one": AuthenticatedCaller(
                Identity("tenant-one", "alice"), frozenset({"inbox"})
            ),
            "Bearer two": AuthenticatedCaller(Identity("tenant-two", "bob"), frozenset({"inbox"})),
        }
    )
    async with _client(ConcurrentApplication(), auth) as client:
        one = asyncio.create_task(
            client.post(
                "/workflows/inbox/runs",
                headers={"Authorization": "Bearer one"},
                json={"payload": "one"},
            )
        )
        two = asyncio.create_task(
            client.post(
                "/workflows/inbox/runs",
                headers={"Authorization": "Bearer two"},
                json={"payload": "two"},
            )
        )
        await asyncio.wait_for(both_started.wait(), timeout=1)
        release.set()
        first, second = await asyncio.gather(one, two)

    by_payload = {response.json()["payload"]: response.json() for response in (first, second)}
    assert by_payload["one"]["metadata"] == {
        "tenant_id": "tenant-one",
        "principal_id": "alice",
    }
    assert by_payload["two"]["metadata"] == {
        "tenant_id": "tenant-two",
        "principal_id": "bob",
    }


async def test_health_and_readiness_expose_only_fixed_status() -> None:
    application = _Application(ready=False)
    async with _client(application, _Authenticator()) as client:
        health = await client.get("/health")
        readiness = await client.get("/ready")
        missing = await client.get("/runs/private-id")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert readiness.status_code == 503
    assert readiness.json() == {"status": "unavailable"}
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_http_config_is_bounded_and_development_auth_is_literal_loopback_only() -> None:
    validate_http_mode(_config(), "development")
    with pytest.raises(ValueError):
        validate_http_mode(_config(), cast(str, "staging"))  # type: ignore[arg-type]
    for host, mode in (
        ("localhost", "development"),
        ("0.0.0.0", "development"),
        ("127.0.0.1", "production"),
    ):
        config = HttpConfig.model_validate(
            {"auth": {"type": "development"}, "host": host}, strict=True
        )
        with pytest.raises(ValueError):
            validate_http_mode(config, cast(str, mode))  # type: ignore[arg-type]

    for field, value in (
        ("port", 0),
        ("max_body_bytes", 16 * 1024 * 1024 + 1),
        ("body_timeout", 61.0),
    ):
        with pytest.raises(ValidationError):
            HttpConfig.model_validate({"auth": {"type": "development"}, field: value}, strict=True)

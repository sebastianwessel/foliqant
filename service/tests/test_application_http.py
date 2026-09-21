"""Composed HTTP application checks with real compilation, auth, and execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import httpx2
import pytest

from foliqant.adapters.auth import BearerAuthenticator
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.adapters.transports.http import create_http_app
from foliqant.bootstrap import (
    PreparedApplication,
    WorkflowApplication,
    open_application,
    prepare_application,
)
from foliqant.contracts.auth import BearerAuthConfig
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.ports.execution import StepContext

Handler = Callable[[FrozenObject, StepContext], Awaitable[StepOutcome]]


def _schema(value: object) -> FrozenObject:
    return cast(FrozenObject, freeze_json(value))


def _settings(
    tmp_path: Path,
    handler: Handler,
    *,
    workflows: tuple[str, ...] = ("echo", "other"),
    run_timeout: float = 1.0,
    concurrency: int = 1,
) -> tuple[Path, dict[str, HandlerRegistration], dict[str, str]]:
    for name in workflows:
        bundle = tmp_path / "workflows" / name
        (bundle / "steps").mkdir(parents=True)
        (bundle / "workflow.yaml").write_text(
            f"version: 1\nname: {name}\nstart: handle\n", encoding="utf-8"
        )
        (bundle / "steps" / "handle.yaml").write_text(
            "type: handler\nhandler: integration\ninput:\n  value:\n    pointer: /payload/value\n",
            encoding="utf-8",
        )

    config = {
        "version": 1,
        "mode": "production",
        "workflows": {name: f"workflows/{name}" for name in workflows},
        "execution": {
            "concurrency": concurrency,
            "queue_limit": 0,
            "run_timeout": run_timeout,
        },
        "http": {
            "auth": {
                "type": "bearer",
                "bindings": {
                    "alice": {
                        "token_env": "TOKEN_ALICE",
                        "tenant_id": "tenant-one",
                        "principal_id": "alice",
                        "workflows": list(workflows),
                    },
                    "bob": {
                        "token_env": "TOKEN_BOB",
                        "tenant_id": "tenant-two",
                        "principal_id": "bob",
                        "workflows": [workflows[0]],
                    },
                },
            }
        },
    }
    config_path = tmp_path / "foliqant.yaml"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    registration = HandlerRegistration(
        handler,
        _schema(
            {
                "type": "object",
                "properties": {"value": {}},
                "required": ["value"],
                "additionalProperties": False,
            }
        ),
        _schema({}),
    )
    return (
        config_path,
        {"integration": registration},
        {"TOKEN_ALICE": "alice-secret", "TOKEN_BOB": "bob-secret"},
    )


def _auth(prepared: PreparedApplication, environment: dict[str, str]) -> BearerAuthenticator:
    http = prepared.config.http
    assert http is not None
    assert isinstance(http.auth, BearerAuthConfig)
    return BearerAuthenticator(http.auth, environment=environment)


def _client(
    prepared: PreparedApplication,
    application: WorkflowApplication,
    environment: dict[str, str],
) -> httpx2.AsyncClient:
    http = prepared.config.http
    assert http is not None
    app = create_http_app(
        application,
        _auth(prepared, environment),
        config=http,
        mode=prepared.config.mode,
    )
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test")


async def test_composed_http_keeps_concurrent_authenticated_identities_independent(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[tuple[str | None, str | None]] = []

    async def echo(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        identity = context.caller.identity
        seen.append((identity.tenant_id, identity.principal_id))
        if len(seen) == 2:
            started.set()
        await release.wait()
        return StepOutcome(
            {
                "value": inputs["value"],
                "tenant": identity.tenant_id,
                "principal": identity.principal_id,
            }
        )

    path, handlers, environment = _settings(tmp_path, echo, run_timeout=2.0, concurrency=2)
    prepared = prepare_application(path, handlers=handlers)

    async with open_application(prepared, environment=environment) as application:
        async with _client(prepared, application, environment) as client:
            alice = asyncio.create_task(
                client.post(
                    "/workflows/echo/runs",
                    headers={"Authorization": "Bearer alice-secret"},
                    json={"payload": {"value": "one"}, "metadata": {"business": "a"}},
                )
            )
            bob = asyncio.create_task(
                client.post(
                    "/workflows/echo/runs",
                    headers={"Authorization": "Bearer bob-secret"},
                    json={"payload": {"value": "two"}, "metadata": {"business": "b"}},
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            release.set()
            responses = await asyncio.gather(alice, bob)

    assert all(response.status_code == 200 for response in responses)
    by_value = {response.json()["payload"]["value"]: response.json() for response in responses}
    assert by_value["one"]["metadata"] == {
        "business": "a",
        "tenant_id": "tenant-one",
        "principal_id": "alice",
    }
    assert by_value["two"]["metadata"] == {
        "business": "b",
        "tenant_id": "tenant-two",
        "principal_id": "bob",
    }
    assert by_value["one"]["decisions"]["handle"]["result"] == {
        "value": "one",
        "tenant": "tenant-one",
        "principal": "alice",
    }
    assert by_value["two"]["decisions"]["handle"]["result"] == {
        "value": "two",
        "tenant": "tenant-two",
        "principal": "bob",
    }


async def test_composed_http_enforces_bearer_workflow_grants_before_execution(
    tmp_path: Path,
) -> None:
    calls = 0

    async def echo(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        nonlocal calls
        calls += 1
        return StepOutcome(inputs["value"])

    path, handlers, environment = _settings(tmp_path, echo)
    prepared = prepare_application(path, handlers=handlers)
    async with open_application(prepared, environment=environment) as application:
        async with _client(prepared, application, environment) as client:
            denied = await client.post(
                "/workflows/other/runs",
                headers={"Authorization": "Bearer bob-secret"},
                json={"payload": {"value": "private"}},
            )
            allowed = await client.post(
                "/workflows/echo/runs",
                headers={"Authorization": "Bearer bob-secret"},
                json={"payload": {"value": "accepted"}},
            )

    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"
    assert "private" not in denied.text
    assert allowed.status_code == 200
    assert calls == 1


async def test_composed_http_deadline_releases_capacity_shared_by_workflows(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def bounded(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        if inputs["value"] == "wait":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return StepOutcome(inputs["value"])

    path, handlers, environment = _settings(tmp_path, bounded, run_timeout=0.1)
    prepared = prepare_application(path, handlers=handlers)
    async with open_application(prepared, environment=environment) as application:
        async with _client(prepared, application, environment) as client:
            waiting = asyncio.create_task(
                client.post(
                    "/workflows/echo/runs",
                    headers={"Authorization": "Bearer alice-secret"},
                    json={"payload": {"value": "wait"}},
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            saturated = await client.post(
                "/workflows/other/runs",
                headers={"Authorization": "Bearer alice-secret"},
                json={"payload": {"value": "fast"}},
            )
            timed_out = await asyncio.wait_for(waiting, timeout=1)
            recovered = await client.post(
                "/workflows/other/runs",
                headers={"Authorization": "Bearer alice-secret"},
                json={"payload": {"value": "fast"}},
            )

    assert saturated.status_code == 429
    assert saturated.json()["code"] == "capacity_exceeded"
    assert timed_out.status_code == 504
    assert timed_out.json()["code"] == "timeout"
    assert cancelled.is_set()
    assert recovered.status_code == 200


async def test_composed_http_cancellation_releases_shared_capacity(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def cancellable(inputs: FrozenObject, context: StepContext) -> StepOutcome:
        if inputs["value"] == "wait":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return StepOutcome(inputs["value"])

    path, handlers, environment = _settings(tmp_path, cancellable, run_timeout=10.0)
    prepared = prepare_application(path, handlers=handlers)
    async with open_application(prepared, environment=environment) as application:
        async with _client(prepared, application, environment) as client:
            waiting = asyncio.create_task(
                client.post(
                    "/workflows/echo/runs",
                    headers={"Authorization": "Bearer alice-secret"},
                    json={"payload": {"value": "wait"}},
                )
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            saturated = await client.post(
                "/workflows/other/runs",
                headers={"Authorization": "Bearer alice-secret"},
                json={"payload": {"value": "fast"}},
            )
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            await asyncio.wait_for(cancelled.wait(), timeout=1)
            recovered = await client.post(
                "/workflows/other/runs",
                headers={"Authorization": "Bearer alice-secret"},
                json={"payload": {"value": "fast"}},
            )

    assert saturated.status_code == 429
    assert recovered.status_code == 200

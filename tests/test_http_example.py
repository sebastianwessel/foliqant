"""The optional HTTP example stays transport-only and offline-testable."""

import asyncio
import importlib.util
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx2
import uvicorn

from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.core.execution import RunResult, Usage
from foliqant.core.json import JsonValue, freeze_json

ROOT = Path(__file__).resolve().parents[1]
type SupportRun = Callable[[dict[str, JsonValue]], Awaitable[ExecutionResult]]


@asynccontextmanager
async def offline_support() -> AsyncIterator[SupportRun]:
    async def run(payload: dict[str, JsonValue]) -> ExecutionResult:
        return to_execution_result(
            RunResult(
                str(uuid4()),
                "support_triage",
                "offline-test-revision",
                "completed",
                freeze_json(
                    {
                        "requested_action": payload["message"],
                        "deadline": None,
                        "account_reference": None,
                    }
                ),
                {"source": "http_test"},
                (),
                Usage(),
            )
        )

    yield run


def example_app():  # type: ignore[no-untyped-def]
    path = ROOT / "examples/http-workflow/server.py"
    spec = importlib.util.spec_from_file_location("foliqant_http_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app(offline_support)


async def test_http_example_runs_each_request_without_storage() -> None:
    app = example_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = {
                "payload": {"requestId": "http-001", "message": "Cancel renewal."},
                "metadata": {"reference": "synthetic"},
            }
            first = await client.post("/run", json=body)
            second = await client.post("/run", json=body)
            assert first.status_code == second.status_code == 200
            result = first.json()
            assert result["execution"]["status"] == "completed"
            assert result["payload"]["requested_action"] == "Cancel renewal."
            assert result["execution"]["id"] != second.json()["execution"]["id"]
            assert (await client.get("/runs/anything")).status_code == 404


async def test_http_example_rejects_invalid_input_and_identity_claims_safely() -> None:
    app = example_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post("/run", content="hello")).status_code == 415
            malformed = await client.post(
                "/run",
                content='{"payload": "private", "payload": 2}',
                headers={"content-type": "application/json"},
            )
            assert malformed.status_code == 400
            assert "private" not in malformed.text
            claimed = await client.post(
                "/run", json={"payload": {}, "metadata": {"principal_id": "unverified"}}
            )
            assert claimed.status_code == 400
            assert "unverified" not in claimed.text
            oversized = await client.post(
                "/run",
                content=b"x" * (1024 * 1024 + 1),
                headers={"content-type": "application/json"},
            )
            assert oversized.status_code == 413


async def test_example_responds_over_real_loopback_http() -> None:
    app = example_app()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_config=None)
    )
    task = asyncio.create_task(server.serve())
    try:
        async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
            async with asyncio.timeout(10):
                while True:
                    if task.done():
                        task.result()
                    try:
                        response = await client.post(
                            "/run",
                            json={
                                "payload": {
                                    "requestId": "http-002",
                                    "message": "Cancel renewal.",
                                },
                                "metadata": {},
                            },
                        )
                        break
                    except httpx2.RequestError:
                        await asyncio.sleep(0.05)
            assert response.status_code == 200
            assert response.json()["payload"]["requested_action"] == "Cancel renewal."
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)

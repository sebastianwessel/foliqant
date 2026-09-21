"""The optional example wraps the same in-memory API without infrastructure."""

import importlib.util
from pathlib import Path

import httpx2


def example_app():
    path = Path(__file__).resolve().parents[2] / "examples/http-workflow/server.py"
    spec = importlib.util.spec_from_file_location("foliqant_http_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app()


async def test_http_example_runs_each_request_without_storage():
    app = example_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = {"payload": {"message": "Hallo"}, "metadata": {"reference": "fixture"}}
            first = await client.post("/run", json=body)
            second = await client.post("/run", json=body)
            assert first.status_code == second.status_code == 200
            result = first.json()
            assert result["execution"]["status"] == "completed"
            assert result["payload"] == body["payload"]
            assert result["metadata"] == body["metadata"]
            assert result["execution"]["id"] != second.json()["execution"]["id"]
            assert (await client.get("/runs/anything")).status_code == 404


async def test_http_example_rejects_invalid_input_and_identity_claims_safely():
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


async def test_example_responds_over_real_loopback_http():
    import asyncio
    import socket
    import sys

    root = Path(__file__).resolve().parents[2]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "server:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        cwd=root / "examples/http-workflow",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
            async with asyncio.timeout(10):
                while True:
                    assert process.returncode is None
                    try:
                        response = await client.post("/run", json={"payload": {"value": 42}})
                        break
                    except httpx2.RequestError:
                        await asyncio.sleep(0.05)
            assert response.status_code == 200
            assert response.json()["payload"] == {"value": 42}
            assert response.json()["execution"]["status"] == "completed"
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()

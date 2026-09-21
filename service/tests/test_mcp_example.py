"""The MCP example executes through a real bounded stdio subprocess."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import cast

from foliqant.contracts.execution import ExecutionResult

ROOT = Path(__file__).resolve().parents[2]
RUN_PATH = ROOT / "examples/mcp-tools/run.py"
SPEC = importlib.util.spec_from_file_location("mcp_tools_example", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
mcp_example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mcp_example)


async def test_real_stdio_server_executes_compiled_mcp_step_without_logging_identity() -> None:
    result = cast(
        ExecutionResult,
        await mcp_example.run_example({"query": "alpha"}, configure_process_logging=False),
    )

    assert result.execution.status == "completed"
    assert result.execution.workflow == "mcp_stdio_lookup"
    assert result.execution.usage.model_requests == 0
    assert result.execution.usage.tool_calls == 1
    assert result.payload == {
        "symbol": "SYN-ALPHA",
        "classification": "synthetic",
        "identity": {"tenant_id": "demo_tenant", "principal_id": "demo_user"},
    }
    assert result.decisions["lookup"].status == "completed"
    assert result.decisions["done"].status == "completed"


def test_documented_offline_command_starts_stdio_server_and_prints_json() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "service",
            "--no-sync",
            "python",
            "examples/mcp-tools/run.py",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = json.loads(completed.stdout)
    assert output["execution"]["status"] == "completed"
    assert output["execution"]["usage"]["model_requests"] == 0
    assert output["execution"]["usage"]["tool_calls"] == 1
    assert output["payload"]["identity"] == {
        "tenant_id": "demo_tenant",
        "principal_id": "demo_user",
    }
    assert "demo_tenant" not in completed.stderr
    assert "demo_user" not in completed.stderr

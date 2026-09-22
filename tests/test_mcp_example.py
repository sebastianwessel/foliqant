"""The MCP example executes through a real bounded stdio subprocess."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import cast

from foliqant.contracts.execution import ExecutionResult

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "examples/public_request_mcp/run.py"
SPEC = importlib.util.spec_from_file_location("public_request_mcp_example", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
mcp_example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mcp_example)


async def test_real_stdio_server_executes_public_request_lookup() -> None:
    result = cast(
        ExecutionResult,
        await mcp_example.run_example({"reference": "FOI-2026-0142"}),
    )

    assert result.execution.status == "completed"
    assert result.execution.workflow == "public_request_lookup"
    assert result.execution.usage.model_requests == 0
    assert result.execution.usage.tool_calls == 1
    assert result.payload == {
        "reference": "FOI-2026-0142",
        "status": "in_review",
        "due_date": "2026-10-05",
        "assigned_team": "records_review",
    }
    assert result.flows["lookup"].steps["lookup"].status == "completed"
    assert result.flows["lookup"].status == "completed"


def test_documented_offline_command_starts_stdio_server_and_prints_json() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-m",
            "examples.public_request_mcp.run",
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
    assert output["payload"]["reference"] == "FOI-2026-0142"
    assert output["payload"]["status"] == "in_review"

"""A real MCP tool result is consumed by a second scripted model turn."""

import json
from pathlib import Path

from examples.model_tool_loop.evaluate import run_evaluations
from examples.model_tool_loop.run import DEMO_PAYLOAD, run_example


async def test_real_tool_loop_and_selected_output() -> None:
    result = await run_example(DEMO_PAYLOAD)
    assert result.execution.status == "completed"
    assert result.execution.usage.model_requests == 2
    assert result.execution.usage.tool_calls == 1
    assert result.payload == {
        "reference": "FOI-2026-0142",
        "language": "en",
        "status": "in_review",
        "due_date": "2026-10-05",
    }
    assert "contact_email" not in json.dumps(result.payload)


async def test_tool_loop_gold_all_scopes(tmp_path: Path) -> None:
    result = await run_evaluations(output=tmp_path / "tool-loop.json")
    assert result["ok"] is True
    assert result["suites"] == 3
    assert result["cases"] == 6

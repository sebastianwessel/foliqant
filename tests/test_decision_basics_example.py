"""Focused offline checks for the first learning example."""

import json
import os
import subprocess
from pathlib import Path

from examples.decision_basics.evaluate import run_evaluations
from examples.decision_basics.run import CONFIG_PATH, DEMO_PAYLOAD, run_example

from foliqant import prepare_application
from foliqant.core.plan import DecisionStepPlan

ROOT = Path(__file__).resolve().parents[1]


def test_example_compiles_to_one_decision_and_review_route() -> None:
    plan = prepare_application(CONFIG_PATH).plans["decision_basics"]
    assert tuple(flow.name for flow in plan.flows) == ("classify",)
    flow = plan.flow("classify")
    assert isinstance(flow.step("classify"), DecisionStepPlan)
    assert flow.on_unresolved is not None
    assert flow.on_unresolved.outcome == "needs_review"


async def test_scripted_pipeline_returns_the_reviewed_category() -> None:
    result = await run_example(DEMO_PAYLOAD)
    assert result.execution.status == "completed"
    assert result.payload == "billing"
    assert result.execution.usage.model_requests == 1
    assert result.execution.usage.tool_calls == 0


async def test_pipeline_flow_and_step_gold_pass_offline() -> None:
    summary = await run_evaluations()
    assert summary["ok"] is True
    assert summary["mode"] == "offline_wiring"
    assert summary["suites"] == 3
    assert summary["cases"] == 12


def test_default_command_never_calls_the_configured_endpoint() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    environment["PYDANTIC_AI_NO_BANNER"] = "1"
    completed = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-m", "examples.decision_basics.run"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = json.loads(completed.stdout)
    assert output["ok"] is True
    assert output["payload"] == "billing"

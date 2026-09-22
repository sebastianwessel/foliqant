"""Focused offline checks for deterministic multi-flow routing."""

import json
import os
import subprocess
from pathlib import Path

from examples.routed_intake.evaluate import run_evaluations
from examples.routed_intake.handlers import HANDLERS
from examples.routed_intake.run import CONFIG_PATH, run_example

from foliqant import prepare_application
from foliqant.core.plan import DecisionStepPlan, HandlerStepPlan, MatchRoutingPlan

ROOT = Path(__file__).resolve().parents[1]


def test_example_compiles_with_exact_routes_and_registered_handlers() -> None:
    plan = prepare_application(CONFIG_PATH, handlers=HANDLERS).plans["routed_intake"]
    assert {flow.name for flow in plan.flows} == {"classify", "billing", "cancellation"}
    assert isinstance(plan.flow("classify").step("classify"), DecisionStepPlan)
    assert isinstance(plan.flow("billing").step("prepare"), HandlerStepPlan)
    assert isinstance(plan.flow("cancellation").step("prepare"), HandlerStepPlan)
    route = plan.flow("classify").transition
    assert isinstance(route, MatchRoutingPlan)
    assert {name for name, _target in route.cases} == {"billing", "cancellation"}


async def test_each_category_runs_only_its_selected_branch() -> None:
    billing = await run_example({"message": "Please send a copy of invoice INV-42."})
    cancellation = await run_example({"message": "Cancel my subscription at renewal."})

    assert billing.execution.status == cancellation.execution.status == "completed"
    assert billing.flows["billing"].result["queue"] == "billing"
    assert billing.flows["cancellation"].status == "skipped"
    assert cancellation.flows["cancellation"].result["queue"] == "cancellation"
    assert cancellation.flows["billing"].status == "skipped"
    assert billing.transitions[0].flow == "billing"
    assert cancellation.transitions[0].flow == "cancellation"
    assert (
        billing.execution.usage.model_requests == cancellation.execution.usage.model_requests == 1
    )


async def test_pipeline_flow_and_step_gold_pass_offline() -> None:
    summary = await run_evaluations()
    assert summary["ok"] is True
    assert summary["mode"] == "offline_wiring"
    assert summary["suites"] == 3
    assert summary["cases"] == 12


def test_default_command_uses_scripted_model_and_trusted_handler() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    environment["PYDANTIC_AI_NO_BANNER"] = "1"
    completed = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-m", "examples.routed_intake.run"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = json.loads(completed.stdout)
    assert output["ok"] is True
    assert output["flows"]["billing"]["result"]["action"] == "request_invoice_review"

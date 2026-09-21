"""Example checks use the public lifecycle with a scripted model, never inference."""

import json
import os
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from examples.support_triage import offline
from examples.support_triage.evaluate import run_evaluations, suite
from examples.support_triage.run import CONFIG_PATH, open_example
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from foliqant import RuntimePlugins, prepare_application
from foliqant.adapters.models import ModelBinding
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.plan import DecisionStepPlan

ROOT = Path(__file__).resolve().parents[1]


def test_compiled_workflow_has_one_unresolved_review_route() -> None:
    plan = prepare_application(CONFIG_PATH).plans["support_triage"]
    classify = plan.step("classify")
    assert isinstance(classify, DecisionStepPlan)
    assert classify.fallback is not None
    assert classify.fallback.on == ("no_supported_answer",)
    assert classify.on_unresolved is not None
    assert classify.on_unresolved.default == "review"
    assert classify.on_unresolved.issues == ()
    assert {step.name for step in plan.steps} == {"classify", "extract", "done", "review"}


async def test_pipeline_and_each_model_step_have_passing_offline_evaluations() -> None:
    report = await run_evaluations()
    assert report["ok"] is True
    assert report["mode"] == "offline_wiring"
    assert isinstance(report["report"], str)
    reports = json.loads(Path(report["report"]).read_text())["reports"]
    assert isinstance(reports, list)
    assert len(reports) == 3
    assert all(isinstance(item, dict) and item["check_coverage"] == 1.0 for item in reports)


async def test_invalid_decision_evidence_fails_without_exposing_output() -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        valid = offline.scripted_response(messages, info)
        part = valid.parts[0]
        assert isinstance(part, TextPart)
        value = json.loads(part.content)
        value["results"][0]["explanation"]["evidence"][0]["quote"] = "PRIVATE absent quote"
        return ModelResponse(parts=[TextPart(json.dumps(value))])

    @asynccontextmanager
    async def factory(
        profiles: ModelProfiles, *, environment: Mapping[str, str]
    ) -> AsyncIterator[Mapping[str, ModelBinding]]:
        yield {
            alias: ModelBinding(
                model=FunctionModel(model),
                settings=ModelSettings(),
                output_mode="native",
                admission=CapacityLimiter(concurrency=1, queue_limit=0),
            )
            for alias in profiles.models
        }

    async with open_example(
        environment=offline.ENVIRONMENT, plugins=RuntimePlugins(model_factory=factory)
    ) as app:
        result = await app.run("support_triage", suite().cases[0].envelope())
    assert result.execution.status == "failed"
    assert result.execution.error is not None
    assert result.execution.error.code == "invalid_output"
    assert "PRIVATE" not in result.execution.error.message


def test_example_compiles_without_credentials_or_endpoint_environment() -> None:
    prepared = prepare_application(CONFIG_PATH)
    assert set(prepared.plans) == {"support_triage"}


def test_command_without_live_flag_does_not_call_endpoint() -> None:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-m", "examples.support_triage.run"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "--live" in completed.stdout

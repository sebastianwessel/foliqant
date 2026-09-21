"""The support example is fully testable without model or network I/O."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import ModelBinding, ModelExecutor
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.support_triage import run as support  # noqa: E402


def _response(value: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(json.dumps(value))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


def _classification(quote: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": "classify",
                "type": "choice",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"optionId": "cancellation"},
                "explanation": {
                    "summary": "The sender explicitly requests non-renewal.",
                    "evidence": [{"sourceId": "message", "quote": quote}],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            }
        ],
    }


async def test_decision_and_extraction_run_with_local_function_model() -> None:
    prompts: list[str] = []

    async def model(messages: Any, _info: Any) -> ModelResponse:
        serialized = repr(messages)
        prompts.append(serialized)
        if '"id":"classify"' in serialized:
            return _response(_classification("cancel renewal"))
        return _response(
            {
                "value": {
                    "requested_action": "cancel renewal and send confirmation",
                    "deadline": "30 September 2026",
                    "account_reference": "C-1049",
                }
            }
        )

    plan = support.compile_example({"local_qwen": "function-model"})
    binding = ModelBinding(
        model=FunctionModel(model),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
        supports_text=True,
        supports_json_schema=True,
    )
    result = await support.run_example(
        cast(dict[str, Any], support.DEMO_PAYLOAD),
        plan=plan,
        executor=ModelExecutor({"local_qwen": binding}, WorkflowSchemas(plan)),
    )

    assert result.execution.status == "completed"
    assert result.decisions["classify"].result["answer"]["optionId"] == "cancellation"
    assert result.payload == {
        "requested_action": "cancel renewal and send confirmation",
        "deadline": "30 September 2026",
        "account_reference": "C-1049",
    }
    assert len(prompts) == 2
    assert all("example_org" not in prompt and "support_agent" not in prompt for prompt in prompts)


async def test_invalid_decision_evidence_fails_without_exposing_output() -> None:
    async def model(_messages: Any, _info: Any) -> ModelResponse:
        return _response(_classification("PRIVATE absent quote"))

    plan = support.compile_example({"local_qwen": "function-model"})
    binding = ModelBinding(
        model=FunctionModel(model),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
        supports_text=True,
        supports_json_schema=True,
    )
    result = await support.run_example(
        cast(dict[str, Any], support.DEMO_PAYLOAD),
        plan=plan,
        executor=ModelExecutor({"local_qwen": binding}, WorkflowSchemas(plan)),
    )

    assert result.execution.status == "failed"
    assert result.execution.error is not None
    assert result.execution.error.code == "invalid_output"
    assert "PRIVATE" not in result.execution.error.message


def test_local_profile_requires_explicit_model_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "FOLIQANT_CURATION_ENDPOINT_URL=http://127.0.0.1:8000/v1\n"
        "FOLIQANT_CURATION_MODEL=local/qwen\n"
        "FOLIQANT_CURATION_REASONING_EFFORT=low\n"
        "FOLIQANT_CURATION_MAX_TOKENS=8192\n"
        "FOLIQANT_CURATION_TEMPERATURE=0.1\n"
        "FOLIQANT_CURATION_TIMEOUT_SECONDS=300\n"
        "IGNORED_SECRET=do-not-read\n",
        encoding="utf-8",
    )
    profile = support.local_qwen_profiles(tmp_path, {}).models["local_qwen"]
    assert profile.model == "local/qwen"
    assert profile.options.reasoning_effort == "low"
    assert profile.options.temperature == 0.1
    assert profile.options.max_tokens == 8192

    (tmp_path / ".env").write_text("FOLIQANT_CURATION_MODEL=\n", encoding="utf-8")
    with pytest.raises(ServiceError) as error:
        support.local_qwen_profiles(tmp_path, {})
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


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

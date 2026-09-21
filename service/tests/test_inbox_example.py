"""The inbox example uses native decision validation without model I/O."""

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import ModelBinding, ModelExecutor
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError

ROOT = Path(__file__).resolve().parents[2]
RUN_PATH = ROOT / "examples/inbox/run.py"
SPEC = importlib.util.spec_from_file_location("inbox_example", RUN_PATH)
assert SPEC is not None and SPEC.loader is not None
inbox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inbox)


def _response(value: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(json.dumps(value))],
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


def _answer(
    question_id: str,
    question_type: str,
    answer: dict[str, str],
    quote: str,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": question_id,
                "type": question_type,
                "answerability": {"status": "answerable", "issues": []},
                "answer": answer,
                "explanation": {
                    "summary": "The message states the relevant fact explicitly.",
                    "evidence": [{"sourceId": "message", "quote": quote}],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            }
        ],
    }


async def test_choice_and_predicate_use_native_validation_without_protected_metadata() -> None:
    prompts: list[str] = []

    async def model(messages: Any, _info: Any) -> ModelResponse:
        serialized = repr(messages)
        prompts.append(serialized)
        if '"id":"triage"' in serialized:
            return _response(_answer("triage", "choice", {"optionId": "access"}, "sign in"))
        return _response(
            _answer("access_blocked", "predicate", {"value": "false"}, "still sign in")
        )

    plan = inbox.compile_example({"local": "function-model"})
    binding = ModelBinding(
        model=FunctionModel(model),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
        supports_text=True,
        supports_json_schema=True,
    )
    result = await inbox.run_example(
        cast(dict[str, Any], inbox.DEMO_PAYLOAD),
        plan=plan,
        executor=ModelExecutor({"local": binding}, WorkflowSchemas(plan)),
    )

    assert result.execution.status == "completed"
    assert result.decisions["triage"].result["answer"]["optionId"] == "access"
    assert result.decisions["access_blocked"].result["answer"]["value"] == "false"
    assert len(prompts) == 2
    assert all("example_org" not in prompt and "example_user" not in prompt for prompt in prompts)


async def test_invalid_evidence_is_rejected_by_native_semantic_validation() -> None:
    async def model(_messages: Any, _info: Any) -> ModelResponse:
        return _response(
            _answer("triage", "choice", {"optionId": "access"}, "PRIVATE absent quote")
        )

    plan = inbox.compile_example({"local": "function-model"})
    binding = ModelBinding(
        model=FunctionModel(model),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
        supports_text=True,
        supports_json_schema=True,
    )
    result = await inbox.run_example(
        cast(dict[str, Any], inbox.DEMO_PAYLOAD),
        plan=plan,
        executor=ModelExecutor({"local": binding}, WorkflowSchemas(plan)),
    )

    assert result.execution.status == "failed"
    assert result.execution.error is not None
    assert result.execution.error.code == "invalid_output"
    assert "PRIVATE" not in result.execution.error.message


def test_profile_json_rejects_duplicate_keys_safely(tmp_path: Path) -> None:
    profile = tmp_path / "profiles.json"
    profile.write_text('{"models": {}, "models": {}}', encoding="utf-8")

    try:
        inbox.load_profiles(profile)
    except ServiceError as error:
        assert error.code == ErrorCode.INVALID_CONFIGURATION
        assert "models" not in str(error)
    else:
        raise AssertionError("duplicate profile key was accepted")

"""Bounded output retries return validation problems to the model; they never hide failures."""

import json
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.usage import RequestUsage
from test_model_executor import (
    _binding,
    _context,
    _decision_result,
    _decision_step,
    _executor,
    _schema_step,
    _structured_response,
)

from foliqant.contracts.models import ModelProfileOverride, ModelProfiles
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Failure


def _feedback(messages: list[Any]) -> str:
    part = next(part for part in messages[-1].parts if isinstance(part, RetryPromptPart))
    return part.model_response()


@pytest.mark.parametrize("mode", ["native", "tool"])
async def test_invalid_schema_output_is_corrected_once_and_counted(mode) -> None:
    step = _schema_step()
    seen: list[str] = []

    async def model(messages: Any, info: Any) -> ModelResponse:
        if len(seen) == 0:
            seen.append("first")
            return _structured_response(info, {"value": {"answer": "seven"}})
        seen.append(_feedback(messages))
        return _structured_response(info, {"value": {"answer": 7}})

    binding = _binding(model, output_mode=mode, output_retries=1)
    budget = StepBudget(model_requests=4, tool_calls=0)
    outcome = await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert outcome.result == {"answer": 7}
    # The model saw where and which constraint its output violated.
    assert "/answer" in seen[1] and "type" in seen[1]
    usage = budget.snapshot()
    assert usage.model_requests == 2 and usage.output_retries == 1


async def test_decision_contract_violation_is_corrected() -> None:
    step = _decision_step()
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        option = "unknown_queue" if calls == 1 else "billing"
        return _structured_response(info, _decision_result(option_id=option))

    binding = _binding(model, output_retries=1)
    budget = StepBudget(model_requests=4, tool_calls=0)
    outcome = await _executor(step, binding).execute(
        step, {"ticket": "Billing failed."}, _context(step.name, budget=budget)
    )
    assert not outcome.needs_review and calls == 2
    assert budget.snapshot().output_retries == 1


@pytest.mark.parametrize(
    "respond,reason,location,constraint",
    [
        (
            lambda info: _structured_response(info, {"value": {"answer": "PRIVATE"}}),
            "schema_violation",
            "/answer",
            "type",
        ),
        (
            lambda info: ModelResponse(parts=[TextPart("not json PRIVATE")]),
            "json_parse_error",
            None,
            "json_invalid",
        ),
    ],
    ids=["schema", "parse"],
)
async def test_exhausted_output_retries_fail_with_a_content_free_reason(
    respond, reason, location, constraint
) -> None:
    step = _schema_step()
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return respond(info)

    binding = _binding(model, output_retries=2)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(
            step, {}, _context(step.name, budget=StepBudget(model_requests=4, tool_calls=0))
        )
    failure = Failure.of(raised.value)
    assert calls == 3 and failure.code is ErrorCode.INVALID_OUTPUT
    assert failure.reason == reason and failure.constraint == constraint
    if location is not None:
        assert failure.location == location
    assert "PRIVATE" not in json.dumps(failure.as_json())


async def test_length_stop_is_never_retried_and_explains_the_budget() -> None:
    step = _schema_step()
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[TextPart('{"value": {"ans')],
            finish_reason="length",
            usage=RequestUsage(input_tokens=5, output_tokens=100, details={"reasoning_tokens": 40}),
        )

    binding = _binding(model, output_retries=3)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(
            step, {}, _context(step.name, budget=StepBudget(model_requests=4, tool_calls=0))
        )
    assert raised.value.code is ErrorCode.OUTPUT_LIMIT_REACHED and calls == 1
    # Reasoning left room: the answer itself was too long or repetitive.
    assert raised.value.reason == "answer_exceeded_budget"


async def test_a_refused_correction_reports_the_invalid_output() -> None:
    step = _schema_step()

    async def model(messages: Any, info: Any) -> ModelResponse:
        return _structured_response(info, {"value": {"answer": "seven"}})

    binding = _binding(model, output_retries=1)
    budget = StepBudget(model_requests=1, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code is ErrorCode.INVALID_OUTPUT
    assert raised.value.reason == "schema_violation"
    assert budget.snapshot().output_retries == 0


async def test_output_retries_do_not_count_as_loop_iterations() -> None:
    step = replace(_schema_step(), max_iterations=1)
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        value = "seven" if calls == 1 else 7
        return _structured_response(info, {"value": {"answer": value}})

    binding = _binding(model, output_retries=1)
    outcome = await _executor(step, binding).execute(
        step, {}, _context(step.name, budget=StepBudget(model_requests=4, tool_calls=0))
    )
    assert outcome.result == {"answer": 7} and calls == 2


def test_output_retries_default_to_one_and_a_step_can_override_them() -> None:
    profiles = ModelProfiles.model_validate(
        {
            "models": {
                "local": {
                    "provider": "openai_compatible",
                    "model": "m",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "allow_insecure_http": True,
                    "output_mode": "native",
                }
            }
        }
    )
    assert profiles.models["local"].output_retries == 1
    override = ModelProfileOverride.model_validate({"profile": "local", "output_retries": 0})
    assert override.output_retries == 0
    with pytest.raises(ValueError):
        ModelProfileOverride.model_validate({"profile": "local", "output_retries": 9})
    with pytest.raises(ValueError):
        ModelProfileOverride.model_validate({"profile": "local", "output_retries": None})

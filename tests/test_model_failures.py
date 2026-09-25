"""Every model and tool failure mode has one precise, content-free code."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from tests.test_model_executor import _binding, _context, _executor, _plan, _text_step

from foliqant.adapters.models import ModelExecutor
from foliqant.adapters.models.failures import model_http_failure
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, FrozenObject
from foliqant.core.plan import ToolPolicyPlan
from foliqant.core.retry import RetryPolicy
from foliqant.ports.execution import StepContext


@pytest.mark.parametrize(
    "status,body,code",
    [
        # OpenAI and Azure OpenAI (the SDK passes the inner error object).
        (400, {"code": "context_length_exceeded", "message": "PRIVATE"}, "context_limit_exceeded"),
        # Anthropic (the full error document).
        (
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long: 208310 tokens > 200000 maximum",
                },
            },
            "context_limit_exceeded",
        ),
        # vLLM and other OpenAI-compatible servers.
        (
            400,
            {
                "object": "error",
                "message": "This model's maximum context length is 32768 tokens. However, "
                "you requested 40000 tokens.",
                "type": "BadRequestError",
            },
            "context_limit_exceeded",
        ),
        (400, {"error": {"type": "exceed_context_size_error"}}, "context_limit_exceeded"),
        (413, None, "context_limit_exceeded"),
        (400, {"code": "content_filter"}, "output_refused"),
        (400, {"code": "invalid_prompt"}, "output_refused"),
        (404, {"code": "model_not_found"}, "model_not_found"),
        (404, {"error": {"code": "DeploymentNotFound"}}, "model_not_found"),
        (400, {"code": "unsupported_parameter", "message": "temperature"}, "request_rejected"),
        (422, "PRIVATE validation text", "request_rejected"),
        (401, None, "unauthenticated"),
        (403, None, "forbidden"),
        (501, None, "dependency_failure"),
    ],
)
def test_provider_http_errors_map_to_precise_codes(status, body, code) -> None:
    failure = model_http_failure(ModelHTTPError(status, "test", body))
    assert failure.code.value == code and not failure.retryable
    assert "PRIVATE" not in str(failure)


@pytest.mark.parametrize(
    "status,code",
    [
        (408, ErrorCode.REQUEST_TIMEOUT),
        (429, ErrorCode.RATE_LIMITED),
        (503, ErrorCode.DEPENDENCY_OVERLOADED),
        (504, ErrorCode.REQUEST_TIMEOUT),
        (529, ErrorCode.DEPENDENCY_OVERLOADED),
        (500, ErrorCode.DEPENDENCY_FAILURE),
        (502, ErrorCode.DEPENDENCY_FAILURE),
    ],
)
async def test_exhausted_transient_responses_keep_their_code_and_retry_permission(
    status, code
) -> None:
    step = _text_step()
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise ModelHTTPError(status, "test", headers={"Retry-After": "0"})

    binding = replace(_binding(model), retry=RetryPolicy(2, 0, 0))
    budget = StepBudget(model_requests=2, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code is code and raised.value.retryable
    assert calls == 2


async def test_invalid_retry_after_keeps_the_code_but_grants_no_retry() -> None:
    step = _text_step()
    calls = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise ModelHTTPError(429, "test", headers={"Retry-After": "PRIVATE"})

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name))
    assert raised.value.code is ErrorCode.RATE_LIMITED and not raised.value.retryable
    assert calls == 1


class _Session:
    """A tool session that validates arguments like a declared MCP catalog."""

    names = ("lookup",)

    def __init__(self) -> None:
        self.successful: set[str] = set()
        self.calls = 0
        self.context: StepContext | None = None

    def input_schema(self, name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        }

    async def call(self, name: str, arguments: FrozenObject) -> FrozenJson:
        assert self.context is not None
        await self.context.budget.start_tool_call()
        self.calls += 1
        if set(arguments) != {"key"} or not isinstance(arguments["key"], str):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        self.successful.add(name)
        return {"value": "found"}


class _Runtime:
    def __init__(self) -> None:
        self.session = _Session()

    @asynccontextmanager
    async def open(
        self, server: str, allowed: tuple[str, ...], context: StepContext
    ) -> AsyncIterator[_Session]:
        self.session.context = context
        yield self.session


def _tool_executor(model: Any, runtime: _Runtime, *, max_iterations: int = 8) -> Any:
    step = replace(
        _text_step(tools=ToolPolicyPlan("records", ("lookup",), "auto")),
        max_iterations=max_iterations,
    )
    executor = ModelExecutor(
        {"configured-alias": _binding(model)}, WorkflowSchemas(_plan(step)), tools=runtime
    )
    return step, executor


@pytest.mark.parametrize(
    "call",
    [
        ToolCallPart("lookup", {"key": 17}, tool_call_id="1"),  # violates the tool schema
        ToolCallPart("unknown", {"key": "one"}, tool_call_id="1"),  # names no tool
        ToolCallPart("lookup", '{"key": ', tool_call_id="1"),  # malformed JSON arguments
    ],
)
async def test_invalid_model_tool_calls_are_invalid_tool_call_not_invalid_input(call) -> None:
    runtime = _Runtime()

    async def model(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(parts=[call])

    step, executor = _tool_executor(model, runtime)
    budget = StepBudget(model_requests=4, tool_calls=4)
    with pytest.raises(ServiceError) as raised:
        await executor.execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code is ErrorCode.INVALID_TOOL_CALL
    assert runtime.session.successful == set()


async def test_long_tool_loops_are_bounded_by_the_step_limits_only() -> None:
    """PydanticAI's own default of 50 requests must not fail a configured longer loop."""
    runtime = _Runtime()
    turns = 0

    async def model(messages: Any, info: Any) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns <= 55:
            return ModelResponse(parts=[ToolCallPart("lookup", {"key": "one"}, tool_call_id="1")])
        return ModelResponse(parts=[TextPart("Found.")])

    step, executor = _tool_executor(model, runtime, max_iterations=64)
    budget = StepBudget(model_requests=64, tool_calls=64)
    outcome = await executor.execute(step, {}, _context(step.name, budget=budget))
    assert outcome.result == "Found." and turns == 56 and runtime.session.calls == 55


@pytest.mark.parametrize(
    "iterations,requests,tools,code",
    [
        (2, 8, 8, ErrorCode.ITERATION_LIMIT_REACHED),
        (8, 2, 8, ErrorCode.MODEL_REQUEST_LIMIT_REACHED),
        (8, 8, 1, ErrorCode.TOOL_CALL_LIMIT_REACHED),
    ],
)
async def test_each_loop_limit_reports_its_own_code(iterations, requests, tools, code) -> None:
    runtime = _Runtime()

    async def model(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("lookup", {"key": "one"}, tool_call_id="1")])

    step, executor = _tool_executor(model, runtime, max_iterations=iterations)
    budget = StepBudget(model_requests=requests, tool_calls=tools)
    with pytest.raises(ServiceError) as raised:
        await executor.execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code is code and not raised.value.retryable

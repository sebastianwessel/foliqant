"""Optional retries preserve attempt accounting, admission and monotonic deadlines."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage
from tests.test_model_executor import _binding, _context, _executor, _schema_step, _text_step

from foliqant.adapters.execution.retry import transient_response
from foliqant.contracts.retry import RetryConfig
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.retry import RetryPolicy, TransientFailure, retry


@pytest.mark.parametrize(
    "raw",
    [
        {"max_attempts": 0},
        {"max_attempts": 9},
        {"max_attempts": True},
        {"initial_delay_seconds": -1},
        {"initial_delay_seconds": 61},
        {"max_delay_seconds": 301},
        {"max_delay_seconds": float("nan")},
        {"initial_delay_seconds": 2, "max_delay_seconds": 1},
        {"unknown": 1},
    ],
)
def test_retry_config_rejects_unbounded_or_malformed_policy(raw):
    with pytest.raises((ValidationError, ValueError)):
        RetryConfig.model_validate(raw)


def test_defaults_disable_retries_and_retry_after_is_bounded_safe_metadata():
    assert RetryConfig().policy() == RetryPolicy(max_attempts=1)
    assert transient_response(429, {"Retry-After": "2"}).retry_after_seconds == 2
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=30))
    assert 28 < transient_response(503, {"retry-after": future}).retry_after_seconds <= 30
    for status in (400, 401, 403, 408, 409, 422, 504):
        assert transient_response(status, {}) is None
    for value in ("nan", "inf", "-1", "PRIVATE INVALID HEADER", "9" * 129):
        assert transient_response(429, {"Retry-After": value}) is None


async def test_generic_retryable_error_does_not_grant_permission():
    calls = 0

    async def operation(attempt):
        nonlocal calls
        calls += 1
        raise ServiceError(ErrorCode.DEPENDENCY_FAILURE, retryable=True)

    with pytest.raises(ServiceError):
        await retry(
            operation,
            policy=RetryPolicy(3, 0, 0),
            deadline=lambda: asyncio.get_running_loop().time() + 1,
        )
    assert calls == 1


@pytest.mark.parametrize("retry_after,remaining", [(10, 20), (1, 0.1)])
async def test_retry_after_is_not_shortened_to_fit_cap_or_deadline(retry_after, remaining):
    calls = 0

    async def operation(attempt):
        nonlocal calls
        calls += 1
        raise TransientFailure(retry_after_seconds=retry_after)

    deadline = asyncio.get_running_loop().time() + remaining
    with pytest.raises(TransientFailure):
        await retry(operation, policy=RetryPolicy(3, 0, 5), deadline=lambda: deadline)
    assert calls == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
async def test_model_transient_response_retries_exact_request_with_honest_usage(status):
    step = _text_step()
    requests = []
    active = 0

    async def model(messages, info):
        nonlocal active
        active += 1
        assert active == 1
        try:
            requests.append(messages)
            if len(requests) == 1:
                raise ModelHTTPError(status, "test", headers={"Retry-After": "0"})
            return ModelResponse(
                parts=[TextPart("ok")], usage=RequestUsage(input_tokens=3, output_tokens=2)
            )
        finally:
            active -= 1

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    budget = StepBudget(model_requests=3, tool_calls=0)
    result = await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert result.result == "ok" and requests[0] == requests[1]
    assert budget.snapshot().model_requests == 2
    assert budget.snapshot().tokens.input_tokens is None  # Failed request was not reported as zero.
    assert budget.snapshot().tokens.output_tokens is None


@pytest.mark.parametrize(
    "error,code",
    [
        (ModelHTTPError(400, "test"), ErrorCode.DEPENDENCY_FAILURE),
        (ModelHTTPError(401, "test"), ErrorCode.UNAUTHENTICATED),
        (ModelHTTPError(403, "test"), ErrorCode.FORBIDDEN),
        (ModelHTTPError(408, "test"), ErrorCode.TIMEOUT),
        (ModelHTTPError(504, "test"), ErrorCode.TIMEOUT),
        (ModelAPIError("test", "connection outcome unknown"), ErrorCode.DEPENDENCY_FAILURE),
        (TimeoutError(), ErrorCode.TIMEOUT),
    ],
)
async def test_model_terminal_errors_never_repeat(error, code):
    step = _text_step()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        raise error

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    budget = StepBudget(model_requests=3, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code == code and calls == 1
    assert budget.snapshot().model_requests == 1


async def test_invalid_model_output_is_not_a_recovery_attempt():
    step = _schema_step()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("not JSON")])

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name))
    assert raised.value.code == ErrorCode.INVALID_OUTPUT and calls == 1


async def test_model_budget_limits_actual_attempts_before_wire():
    step = _text_step()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        raise ModelHTTPError(503, "test")

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    budget = StepBudget(model_requests=1, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code == ErrorCode.BUDGET_EXHAUSTED
    assert calls == budget.snapshot().model_requests == 1


async def test_backoff_releases_model_admission_and_cancellation_stops_next_request(monkeypatch):
    import foliqant.core.retry as retry_module

    monkeypatch.setattr(retry_module.random, "uniform", lambda low, high: high)
    step = _text_step()
    first = asyncio.Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        first.set()
        raise ModelHTTPError(429, "test")

    binding = replace(_binding(model), retry=RetryPolicy(3, 0.2, 0.2))
    budget = StepBudget(model_requests=3, tool_calls=0)
    task = asyncio.create_task(
        _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    )
    await first.wait()
    await asyncio.sleep(0)
    assert binding.admission.active == 0
    async with binding.admission.slot(deadline=asyncio.get_running_loop().time() + 1):
        assert binding.admission.active == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == budget.snapshot().model_requests == 1


async def test_retry_does_not_restart_model_timeout_window(monkeypatch):
    import foliqant.core.retry as retry_module

    monkeypatch.setattr(retry_module.random, "uniform", lambda low, high: high)
    step = _text_step()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.025)
            raise ModelHTTPError(503, "test")
        await asyncio.sleep(0.025)
        return ModelResponse(parts=[TextPart("late")])

    binding = replace(_binding(model), retry=RetryPolicy(3, 0.01, 0.01))
    budget = StepBudget(model_requests=3, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(
            step, {}, _context(step.name, budget=budget, model_timeout=0.05)
        )
    assert raised.value.code == ErrorCode.TIMEOUT and calls == 2
    assert budget.snapshot().model_requests == 2


async def test_anthropic_stale_thinking_400_has_no_hidden_sdk_recovery(monkeypatch):
    import anthropic
    import httpx2
    from pydantic_ai.models.anthropic import AnthropicModel

    from foliqant.adapters.models.providers import open_model_bindings
    from foliqant.contracts.models import ModelProfiles

    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            400,
            request=request,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "The block is bound to a different conversation",
                },
            },
        )

    original = anthropic.AsyncAnthropic

    def client(**kwargs):
        return original(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client)
    profiles = ModelProfiles.model_validate(
        {
            "models": {
                "configured": {
                    "provider": "anthropic",
                    "model": "claude-fable-5-1",
                    "output_mode": "native",
                    "retry": {
                        "max_attempts": 3,
                        "initial_delay_seconds": 0,
                        "max_delay_seconds": 0,
                    },
                }
            }
        }
    )
    step = _text_step()
    budget = StepBudget(model_requests=3, tool_calls=0)
    async with open_model_bindings(profiles, environment={"ANTHROPIC_API_KEY": "test"}) as bindings:
        binding = bindings["configured"]
        assert isinstance(binding.model, AnthropicModel)
        assert binding.model.profile.get("anthropic_binds_thinking_blocks") is False
        with pytest.raises(ServiceError) as raised:
            await _executor(step, binding).execute(step, {}, _context(step.name, budget=budget))
    assert raised.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert len(requests) == budget.snapshot().model_requests == 1
    assert requests[0].headers["x-stainless-retry-count"] == "0"


async def test_late_response_after_swallowed_cancellation_is_timeout_with_known_usage():
    step = _text_step()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ModelResponse(
                parts=[TextPart("late")], usage=RequestUsage(input_tokens=4, output_tokens=2)
            )

    binding = replace(_binding(model), retry=RetryPolicy(3, 0, 0))
    budget = StepBudget(model_requests=3, tool_calls=0)
    with pytest.raises(ServiceError) as raised:
        await _executor(step, binding).execute(
            step, {}, _context(step.name, budget=budget, model_timeout=0.01)
        )
    assert raised.value.code == ErrorCode.TIMEOUT and calls == 1
    assert budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens.input_tokens == 4

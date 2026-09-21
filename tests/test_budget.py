"""Failed attempts consume limits; absent usage must not become measured zero."""

import asyncio

import pytest

from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import TokenUsage


async def test_reserves_failed_attempt_and_does_not_invent_usage() -> None:
    budget = StepBudget(model_requests=1, tool_calls=1)
    assert budget.snapshot().tokens == TokenUsage.zero()
    await budget.start_model_request()
    await budget.start_tool_call()
    with pytest.raises(ServiceError) as error:
        await budget.start_model_request()
    assert error.value.code == ErrorCode.BUDGET_EXHAUSTED
    with pytest.raises(ServiceError):
        await budget.start_tool_call()
    snapshot = budget.snapshot()
    assert snapshot.model_requests == snapshot.tool_calls == 1
    assert snapshot.tokens == TokenUsage()


async def test_partial_measurements_aggregate_independently_without_double_counting() -> None:
    budget = StepBudget(model_requests=3, tool_calls=0)
    first = await budget.start_model_request()
    second = await budget.start_model_request()
    await budget.finish_model_request(first, TokenUsage(20, 10, 5, None, 4))
    await budget.finish_model_request(second, TokenUsage(30, 15, 7, 0, None))
    assert budget.snapshot().tokens == TokenUsage(50, 25, 12, None, None)
    with pytest.raises(ServiceError) as error:
        await budget.finish_model_request(first, TokenUsage.zero())
    assert error.value.code == ErrorCode.CONFLICT
    await budget.start_model_request()
    assert budget.snapshot().tokens == TokenUsage()


async def test_concurrent_reservations_never_exceed_budget() -> None:
    budget = StepBudget(model_requests=2, tool_calls=0)
    results = await asyncio.gather(
        *(budget.start_model_request() for _ in range(20)), return_exceptions=True
    )
    assert sorted(value for value in results if isinstance(value, int)) == [1, 2]
    assert sum(isinstance(value, ServiceError) for value in results) == 18
    assert budget.snapshot().model_requests == 2


@pytest.mark.parametrize("ticket", [0, 2, True])
async def test_unreserved_usage_is_rejected(ticket: int) -> None:
    budget = StepBudget(model_requests=1, tool_calls=0)
    await budget.start_model_request()
    with pytest.raises(ServiceError):
        await budget.finish_model_request(ticket, TokenUsage.zero())


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_token_measurement_is_rejected(value: int) -> None:
    with pytest.raises(ServiceError):
        TokenUsage(input_tokens=value)


async def test_invalid_report_keeps_reserved_attempt_and_unknown_usage() -> None:
    budget = StepBudget(model_requests=1, tool_calls=0)
    ticket = await budget.start_model_request()
    with pytest.raises(ServiceError) as error:
        await budget.finish_model_request(ticket, object())  # type: ignore[arg-type]
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens == TokenUsage()


@pytest.mark.parametrize(
    "component", ["cache_read_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens"]
)
def test_measured_subsets_cannot_exceed_their_known_total(component: str) -> None:
    with pytest.raises(ServiceError):
        TokenUsage(input_tokens=1, output_tokens=1, **{component: 2})

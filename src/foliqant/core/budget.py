"""Per-step attempt accounting for in-memory workflow execution."""

from dataclasses import dataclass

from .errors import ErrorCode, ServiceError
from .execution import ModelUsage, TokenUsage, Usage
from .pricing import PricingPlan


@dataclass(slots=True)
class _ModelAttempt:
    model: str
    pricing: PricingPlan | None
    usage: TokenUsage | None = None


class StepBudget:
    """Reserve before I/O; failures and missing reports do not erase attempts.

    Each instance belongs to one step execution on one event loop.
    """

    def __init__(self, *, model_requests: int, tool_calls: int) -> None:
        if any(type(value) is not int or value < 0 for value in (model_requests, tool_calls)):
            raise ValueError("attempt limits must be nonnegative integers")
        self._model_limit = model_requests
        self._tool_limit = tool_calls
        self._models: dict[int, _ModelAttempt] = {}
        self._tools = 0
        self._output_retries = 0

    async def start_model_request(self, model: str, pricing: PricingPlan | None = None) -> int:
        """Reserve one attempt for a provider model ID and return its accounting ticket.

        ``pricing`` estimates the request's cost once its usage is reported.
        """
        if type(model) is not str or not 1 <= len(model) <= 512 or not model.strip():
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if pricing is not None and type(pricing) is not PricingPlan:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if len(self._models) >= self._model_limit:
            raise ServiceError(ErrorCode.MODEL_REQUEST_LIMIT_REACHED)
        ticket = len(self._models) + 1
        self._models[ticket] = _ModelAttempt(model, pricing)
        return ticket

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None:
        """Record measured usage once; this does not refund the reserved attempt."""
        if (
            type(ticket) is not int
            or ticket not in self._models
            or self._models[ticket].usage is not None
        ):
            raise ServiceError(ErrorCode.CONFLICT)
        if type(usage) is not TokenUsage:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        self._models[ticket].usage = TokenUsage(
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens,
            usage.cache_write_input_tokens,
            usage.reasoning_output_tokens,
        )

    async def start_tool_call(self) -> int:
        """Reserve an external tool invocation, including an eventual failure."""
        if self._tools >= self._tool_limit:
            raise ServiceError(ErrorCode.TOOL_CALL_LIMIT_REACHED)
        self._tools += 1
        return self._tools

    def record_output_retry(self) -> None:
        """Count one admitted model request that asks for a corrected invalid output."""
        self._output_retries += 1

    def snapshot(self) -> Usage:
        """Return measured totals; unreported attempts make tokens and cost unavailable."""
        usage = Usage(0, self._tools, output_retries=self._output_retries)
        for attempt in self._models.values():
            tokens = attempt.usage if attempt.usage is not None else TokenUsage()
            cost = attempt.pricing.request_cost(attempt.usage) if attempt.pricing else None
            usage = usage.plus(Usage(1, 0, tokens, ((attempt.model, ModelUsage(1, tokens, cost)),)))
        return usage

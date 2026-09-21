"""Per-step attempt accounting for in-memory workflow execution."""

from .errors import ErrorCode, ServiceError
from .execution import TokenUsage, Usage


class StepBudget:
    """Reserve before I/O; failures and missing reports do not erase attempts.

    Each instance belongs to one step execution on one event loop.
    """

    def __init__(self, *, model_requests: int, tool_calls: int) -> None:
        if any(type(value) is not int or value < 0 for value in (model_requests, tool_calls)):
            raise ValueError("attempt limits must be nonnegative integers")
        self._model_limit = model_requests
        self._tool_limit = tool_calls
        self._models: dict[int, TokenUsage | None] = {}
        self._tools = 0

    async def start_model_request(self) -> int:
        """Reserve one model attempt and return its local accounting ticket."""
        if len(self._models) >= self._model_limit:
            raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
        ticket = len(self._models) + 1
        self._models[ticket] = None
        return ticket

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None:
        """Record measured usage once; this does not refund the reserved attempt."""
        if (
            type(ticket) is not int
            or ticket not in self._models
            or self._models[ticket] is not None
        ):
            raise ServiceError(ErrorCode.CONFLICT)
        if type(usage) is not TokenUsage:
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        self._models[ticket] = TokenUsage(
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens,
            usage.cache_write_input_tokens,
            usage.reasoning_output_tokens,
        )

    async def start_tool_call(self) -> int:
        """Reserve an external tool invocation, including an eventual failure."""
        if self._tools >= self._tool_limit:
            raise ServiceError(ErrorCode.BUDGET_EXHAUSTED)
        self._tools += 1
        return self._tools

    def snapshot(self) -> Usage:
        """Return measured totals; unreported attempts make tokens unavailable."""
        tokens = TokenUsage.zero()
        for report in self._models.values():
            tokens = tokens.plus(report if report is not None else TokenUsage())
        return Usage(len(self._models), self._tools, tokens)

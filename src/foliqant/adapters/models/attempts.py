"""The request attempt in flight, for classification inside the model client span."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from foliqant.core.errors import ErrorCode, timeout_code

from .failures import model_http_failure


@dataclass(frozen=True, slots=True)
class RequestAttempt:
    """One provider attempt: its one-based number within the logical request,
    whether the request asks for a corrected output, and both deadlines."""

    number: int
    output_retry: bool
    deadline: float
    run_deadline: float
    timeout_errors: tuple[type[Exception], ...] = ()

    def failure_code(self, error: BaseException) -> ErrorCode:
        """The code a failed attempt reports on its span, matching the step's failure."""
        now = asyncio.get_running_loop().time()
        if isinstance(error, asyncio.CancelledError | TimeoutError):
            return (
                timeout_code(now, self.run_deadline)
                if now >= self.deadline
                else (ErrorCode.CANCELLED)
            )
        if isinstance(error, ModelHTTPError):
            return model_http_failure(error).code
        if isinstance(error, self.timeout_errors) or (
            isinstance(error, ModelAPIError) and isinstance(error.__cause__, self.timeout_errors)
        ):
            return ErrorCode.REQUEST_TIMEOUT
        return ErrorCode.DEPENDENCY_FAILURE


CURRENT_ATTEMPT: ContextVar[RequestAttempt | None] = ContextVar(
    "foliqant_model_request_attempt", default=None
)

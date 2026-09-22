"""Opt-in bounded retries for explicitly classified, completed transient failures."""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .errors import ErrorCode, ServiceError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Total attempts and capped exponential full-jitter backoff in seconds."""

    max_attempts: int = 1
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 8:
            raise ValueError("retry attempts must be an integer from one through eight")
        for value, maximum in ((self.initial_delay_seconds, 60), (self.max_delay_seconds, 300)):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError("retry delays must be finite and bounded")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("maximum retry delay must cover initial delay")


class TransientFailure(ServiceError):
    """An adapter proved a completed transient response; never contains provider text."""

    def __init__(self, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(ErrorCode.DEPENDENCY_FAILURE, retryable=True)
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("invalid retry-after delay")
        self.retry_after_seconds = retry_after_seconds


async def retry[T](
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    deadline: Callable[[], float],
) -> T:
    """Retry only typed transient responses within one unchanged operation deadline.

    The first attempt may shorten the root deadline after admission. It may never
    extend it. Cancellation, timeout and all unclassified failures pass through.
    """
    for attempt in range(1, policy.max_attempts + 1):
        if deadline() <= asyncio.get_running_loop().time():
            raise ServiceError(ErrorCode.TIMEOUT)
        try:
            return await operation(attempt)
        except TransientFailure as error:
            if attempt == policy.max_attempts:
                raise
            cap = min(policy.max_delay_seconds, policy.initial_delay_seconds * 2 ** (attempt - 1))
            delay = max(random.uniform(0, cap), error.retry_after_seconds or 0)
            end = deadline()
            if delay > policy.max_delay_seconds or delay >= end - asyncio.get_running_loop().time():
                raise  # Never violate Retry-After by clipping it to a smaller wait.
            try:
                async with asyncio.timeout_at(end):
                    await asyncio.sleep(delay)
            except TimeoutError:
                raise ServiceError(ErrorCode.TIMEOUT) from None
    raise AssertionError("validated retry policy has at least one attempt")  # pragma: no cover

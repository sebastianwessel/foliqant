"""Bounded, cancellation-safe admission for one event loop and resource scope."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .errors import ErrorCode, ServiceError


class CapacityLimiter:
    """Bound active and waiting operations without serializing unrelated work.

    Create one limiter per scarce resource at application startup. It is local
    to one replica and event loop; shared provider quotas need a shared admission
    adapter. An admission slot does not replace the operation's own deadline.
    """

    def __init__(self, *, concurrency: int, queue_limit: int) -> None:
        if (
            type(concurrency) is not int
            or type(queue_limit) is not int
            or concurrency < 1
            or queue_limit < 0
        ):
            raise ValueError(
                "concurrency must be a positive integer and queue limit nonnegative integer"
            )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._maximum = concurrency + queue_limit
        self._admitted = 0
        self._active = 0

    @property
    def active(self) -> int:
        """Current active operations, for bounded operational measurements."""
        return self._active

    @property
    def waiting(self) -> int:
        """Current admitted operations waiting for capacity."""
        return self._admitted - self._active

    @asynccontextmanager
    async def slot(
        self, *, deadline: float, timeout: ErrorCode = ErrorCode.REQUEST_TIMEOUT
    ) -> AsyncIterator[None]:
        """Acquire capacity by an absolute monotonic deadline and always release.

        Saturation fails immediately instead of growing an unbounded task queue.
        Task cancellation propagates unchanged; a wait past the deadline fails
        with ``timeout``: ``request_timeout`` for an operation's slot,
        ``run_timeout`` when the deadline is the run's own.
        """
        if not math.isfinite(deadline) or deadline <= asyncio.get_running_loop().time():
            raise ServiceError(timeout)
        if self._admitted >= self._maximum:
            raise ServiceError(ErrorCode.CAPACITY_EXCEEDED, retryable=True)
        self._admitted += 1
        acquired = False
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    await self._semaphore.acquire()
            except TimeoutError:
                raise ServiceError(timeout) from None
            acquired = True
            self._active += 1
            # A released waiter may already be runnable before the timeout
            # callback is scheduled. Recheck the deadline before starting work.
            if deadline <= asyncio.get_running_loop().time():
                raise ServiceError(timeout)
            yield
        finally:
            if acquired:
                self._active -= 1
                self._semaphore.release()
            self._admitted -= 1

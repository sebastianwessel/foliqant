"""Bound blocking SDK work without freeing real capacity on caller cancellation."""

import asyncio
import contextvars
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError


@dataclass(slots=True)
class _Job:
    started: bool = False


class BlockingExecutor:
    """Explicitly owned, bounded executor for blocking-only I/O adapters.

    Prefer native async clients. Every submitted SDK operation must also have
    its own socket/request timeout: Python cannot kill a running thread. Caller
    cancellation stops queued work, but started work holds a permit until it
    finishes. There are no retries, and late results are discarded.

    Construct inside its owning event loop. Call ``await aclose(timeout=...)``
    before closing the loop; False reports unfinished workers, not success.
    """

    def __init__(self, *, concurrency: int, queue_limit: int) -> None:
        if type(concurrency) is not int or type(queue_limit) is not int:
            raise ValueError("executor limits must be integers")
        self._limiter = CapacityLimiter(concurrency=concurrency, queue_limit=queue_limit)
        self._maximum = concurrency + queue_limit
        self._loop = asyncio.get_running_loop()
        self._executor = ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="foliqant-sdk"
        )
        self._jobs: dict[asyncio.Task[object], _Job] = {}
        self._closed = False

    @property
    def outstanding(self) -> int:
        """Owned operations, including those not yet scheduled for admission."""
        return len(self._jobs)

    @property
    def active(self) -> int:
        """Submitted operations not yet completed, including abandoned callers."""
        return self._limiter.active

    @property
    def waiting(self) -> int:
        """Admitted operations waiting to submit blocking work."""
        return self._limiter.waiting

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("executor used outside its owning event loop")

    async def run[T](self, operation: Callable[[], T], *, deadline: float) -> T:
        """Run by an absolute monotonic deadline with isolated context and capacity.

        Exceptions are mapped to a content-free dependency failure. A timeout or
        cancellation cannot determine an external mutation's outcome; the calling
        adapter must record uncertainty rather than retry that mutation blindly.
        """
        self._check_loop()
        if self._closed:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        if not math.isfinite(deadline) or deadline <= self._loop.time():
            raise ServiceError(ErrorCode.REQUEST_TIMEOUT)
        # Reserve before task creation, not one event-loop turn later inside
        # _execute. A burst must not retain an unbounded set of caller contexts.
        if len(self._jobs) >= self._maximum:
            raise ServiceError(ErrorCode.CAPACITY_EXCEEDED, retryable=True)
        job = _Job()
        task = self._loop.create_task(self._execute(operation, deadline=deadline, job=job))
        # Task's result is covariant: tracking never writes into it.
        self._jobs[task] = job
        task.add_done_callback(self._finished)
        try:
            async with asyncio.timeout_at(deadline):
                return await asyncio.shield(task)
        except TimeoutError:
            if not job.started:
                task.cancel()
            raise ServiceError(ErrorCode.REQUEST_TIMEOUT) from None
        except asyncio.CancelledError:
            if not job.started:
                task.cancel()
            raise

    async def _execute[T](self, operation: Callable[[], T], *, deadline: float, job: _Job) -> T:
        async with self._limiter.slot(deadline=deadline):
            if self._closed or self._loop.time() >= deadline:
                raise ServiceError(
                    ErrorCode.DEPENDENCY_FAILURE if self._closed else ErrorCode.REQUEST_TIMEOUT
                )
            context = contextvars.copy_context()
            job.started = True
            try:
                future = self._executor.submit(context.run, operation)
                return await asyncio.wrap_future(future)
            except Exception:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None

    def _finished(self, task: asyncio.Task[object]) -> None:
        self._jobs.pop(task, None)
        if not task.cancelled():
            # Retrieve abandoned errors without rendering or logging their content.
            task.exception()

    async def aclose(self, *, timeout: float = 1.0) -> bool:
        """Stop intake, cancel queued work and attempt a bounded asynchronous drain.

        Repeated calls can finish a previously incomplete drain. A false result
        means an SDK thread is still running; it can also delay interpreter exit.
        This method never blocks the event-loop thread on executor.shutdown().
        """
        self._check_loop()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("shutdown timeout must be finite and nonnegative")
        self._closed = True
        for task, job in tuple(self._jobs.items()):
            if not job.started:
                task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        pending = set(self._jobs)
        if pending:
            _, remaining = await asyncio.wait(pending, timeout=timeout)
            return not remaining
        return True

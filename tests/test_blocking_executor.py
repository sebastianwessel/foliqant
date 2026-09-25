"""Blocking SDK cancellation must not produce unbounded abandoned threads."""

import asyncio
import contextvars
import threading
from collections.abc import Callable

import pytest

from foliqant.adapters.execution.blocking import BlockingExecutor
from foliqant.core.errors import ErrorCode, ServiceError


async def eventually(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.001)


async def test_blocked_sdk_does_not_block_loop_and_cancel_keeps_real_capacity() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=0)
    entered = threading.Event()
    release = threading.Event()

    def operation() -> int:
        entered.set()
        assert release.wait(2)
        return 7

    task = asyncio.create_task(
        executor.run(operation, deadline=asyncio.get_running_loop().time() + 1)
    )
    try:
        await eventually(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert executor.active == 1
        with pytest.raises(ServiceError) as error:
            await executor.run(lambda: 1, deadline=asyncio.get_running_loop().time() + 1)
        assert error.value.code == ErrorCode.CAPACITY_EXCEEDED
        assert not await executor.aclose(timeout=0.001)
    finally:
        release.set()
        assert await executor.aclose(timeout=2)
    assert executor.active == 0


async def test_timeout_keeps_real_capacity_until_sdk_finishes() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=0)
    entered = threading.Event()
    release = threading.Event()

    def operation() -> None:
        entered.set()
        assert release.wait(2)

    try:
        with pytest.raises(ServiceError) as error:
            await executor.run(operation, deadline=asyncio.get_running_loop().time() + 0.05)
        assert error.value.code == ErrorCode.REQUEST_TIMEOUT
        assert entered.is_set()
        assert executor.active == 1
    finally:
        release.set()
        assert await executor.aclose(timeout=2)


async def test_cancelled_queued_call_never_starts_and_frees_waiting_capacity() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=1)
    entered = threading.Event()
    release = threading.Event()
    unwanted = threading.Event()
    loop = asyncio.get_running_loop()

    def first() -> None:
        entered.set()
        assert release.wait(2)

    active = asyncio.create_task(executor.run(first, deadline=loop.time() + 2))
    try:
        await eventually(entered.is_set)
        queued = asyncio.create_task(executor.run(unwanted.set, deadline=loop.time() + 2))
        await eventually(lambda: executor.waiting == 1)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await eventually(lambda: executor.waiting == 0)
        release.set()
        await active
        assert await executor.run(lambda: 9, deadline=loop.time() + 1) == 9
        assert not unwanted.is_set()
    finally:
        release.set()
        assert await executor.aclose(timeout=2)


async def test_independent_calls_overlap_and_copy_context_without_leaking_it() -> None:
    caller: contextvars.ContextVar[tuple[str, str, str]] = contextvars.ContextVar("caller")
    executor = BlockingExecutor(concurrency=2, queue_limit=0)
    barrier = threading.Barrier(2, timeout=2)
    loop = asyncio.get_running_loop()

    def operation() -> tuple[str, str, str]:
        barrier.wait()
        before = caller.get()
        caller.set(("worker-only", "worker-only", "worker-only"))
        return before

    async def work(tenant: str) -> tuple[str, str, str]:
        identity = (tenant, f"user-{tenant}", f"trace-{tenant}")
        token = caller.set(identity)
        try:
            result = await executor.run(operation, deadline=loop.time() + 2)
            assert caller.get() == identity
            return result
        finally:
            caller.reset(token)

    try:
        async with asyncio.TaskGroup() as group:
            first = group.create_task(work("first"))
            second = group.create_task(work("second"))
        assert first.result() == ("first", "user-first", "trace-first")
        assert second.result() == ("second", "user-second", "trace-second")
        assert await executor.run(lambda: caller.get(None), deadline=loop.time() + 1) is None
    finally:
        assert await executor.aclose(timeout=2)


async def test_failure_is_safe_and_does_not_leak_capacity() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=0)

    def failing() -> None:
        raise RuntimeError("SECRET-CUSTOMER-DATA")

    try:
        with pytest.raises(ServiceError) as error:
            await executor.run(failing, deadline=asyncio.get_running_loop().time() + 1)
        assert error.value.code == ErrorCode.DEPENDENCY_FAILURE
        assert "SECRET" not in str(error.value)
        assert error.value.__suppress_context__
        assert executor.active == executor.waiting == 0
    finally:
        assert await executor.aclose(timeout=2)


async def test_shutdown_cancels_queue_rejects_intake_and_can_be_repeated() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=1)
    entered = threading.Event()
    release = threading.Event()
    unwanted = threading.Event()
    loop = asyncio.get_running_loop()

    def first() -> None:
        entered.set()
        assert release.wait(2)

    active = asyncio.create_task(executor.run(first, deadline=loop.time() + 2))
    try:
        await eventually(entered.is_set)
        queued = asyncio.create_task(executor.run(unwanted.set, deadline=loop.time() + 2))
        await eventually(lambda: executor.waiting == 1)
        assert not await executor.aclose(timeout=0.01)
        with pytest.raises(asyncio.CancelledError):
            await queued
        with pytest.raises(ServiceError):
            await executor.run(unwanted.set, deadline=loop.time() + 1)
        release.set()
        await active
        assert await executor.aclose(timeout=2)
        assert await executor.aclose(timeout=0)
        assert not unwanted.is_set()
    finally:
        release.set()
        assert await executor.aclose(timeout=2)


async def test_expired_call_does_not_start() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=0)
    entered = threading.Event()
    try:
        with pytest.raises(ServiceError) as error:
            await executor.run(entered.set, deadline=asyncio.get_running_loop().time() - 1)
        assert error.value.code == ErrorCode.REQUEST_TIMEOUT
        assert not entered.is_set()
    finally:
        assert await executor.aclose(timeout=2)


async def test_burst_is_bounded_before_internal_tasks_can_acquire_admission() -> None:
    executor = BlockingExecutor(concurrency=1, queue_limit=0)
    release = threading.Event()
    deadline = asyncio.get_running_loop().time() + 2

    def operation() -> bool:
        return release.wait(2)

    callers = [asyncio.create_task(executor.run(operation, deadline=deadline)) for _ in range(100)]
    try:
        # Each caller gets a turn, before any newly created worker task gets one.
        await asyncio.sleep(0)
        assert executor.outstanding == 1
        release.set()
        outcomes = await asyncio.gather(*callers, return_exceptions=True)
        assert outcomes.count(True) == 1
        rejected = [value for value in outcomes if isinstance(value, ServiceError)]
        assert len(rejected) == 99
        assert all(value.code == ErrorCode.CAPACITY_EXCEEDED for value in rejected)
    finally:
        release.set()
        assert await executor.aclose(timeout=2)

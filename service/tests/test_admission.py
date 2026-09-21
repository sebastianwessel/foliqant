"""Admission must bound queued work without blocking the event loop."""

import asyncio

import pytest

from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError


async def test_independent_operations_overlap_with_bounded_capacity() -> None:
    limiter = CapacityLimiter(concurrency=2, queue_limit=0)
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def work() -> None:
        nonlocal entered
        async with limiter.slot(deadline=asyncio.get_running_loop().time() + 2):
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()

    async with asyncio.TaskGroup() as group:
        group.create_task(work())
        group.create_task(work())
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        with pytest.raises(ServiceError) as error:
            async with limiter.slot(deadline=asyncio.get_running_loop().time() + 1):
                pytest.fail("saturated capacity must not admit another operation")
        assert error.value.code == ErrorCode.CAPACITY_EXCEEDED
        release.set()


async def test_cancelled_waiter_does_not_leak_or_create_permits() -> None:
    limiter = CapacityLimiter(concurrency=1, queue_limit=1)
    deadline = asyncio.get_running_loop().time() + 2
    attempted = asyncio.Event()

    async def wait() -> None:
        attempted.set()
        async with limiter.slot(deadline=deadline):
            pytest.fail("cancelled waiter must not run")

    async with limiter.slot(deadline=deadline):
        task = asyncio.create_task(wait())
        await attempted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with limiter.slot(deadline=deadline):
        assert limiter.active == 1
    assert limiter.active == limiter.waiting == 0


async def test_wait_timeout_releases_queue_position() -> None:
    limiter = CapacityLimiter(concurrency=1, queue_limit=1)
    loop = asyncio.get_running_loop()
    async with limiter.slot(deadline=loop.time() + 2):
        with pytest.raises(ServiceError) as error:
            async with limiter.slot(deadline=loop.time() + 0.01):
                pytest.fail("busy limiter must time out")
        assert error.value.code == ErrorCode.TIMEOUT
        assert limiter.waiting == 0
    assert limiter.active == 0


async def test_expired_deadline_cannot_start_work_even_with_free_capacity() -> None:
    limiter = CapacityLimiter(concurrency=1, queue_limit=0)
    with pytest.raises(ServiceError) as error:
        async with limiter.slot(deadline=asyncio.get_running_loop().time() - 1):
            pytest.fail("deadline already elapsed")
    assert error.value.code == ErrorCode.TIMEOUT
    assert limiter.active == 0


async def test_cancelling_active_work_releases_its_permit() -> None:
    limiter = CapacityLimiter(concurrency=1, queue_limit=0)
    entered = asyncio.Event()

    async def work() -> None:
        async with limiter.slot(deadline=asyncio.get_running_loop().time() + 2):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.active == limiter.waiting == 0


@pytest.mark.parametrize("concurrency, queue", [(0, 1), (-1, 0), (1, -1)])
def test_invalid_capacity_rejected(concurrency: int, queue: int) -> None:
    with pytest.raises(ValueError):
        CapacityLimiter(concurrency=concurrency, queue_limit=queue)


async def test_woken_waiter_past_deadline_cannot_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = CapacityLimiter(concurrency=1, queue_limit=1)
    loop = asyncio.get_running_loop()
    original_time = loop.time
    offset = 0.0
    monkeypatch.setattr(loop, "time", lambda: original_time() + offset)
    deadline = loop.time() + 1
    attempted = asyncio.Event()

    async def wait() -> None:
        attempted.set()
        async with limiter.slot(deadline=deadline):
            pytest.fail("a woken waiter must recheck its deadline before starting work")

    async with limiter.slot(deadline=loop.time() + 10):
        task = asyncio.create_task(wait())
        await attempted.wait()
        assert limiter.waiting == 1
    # release() has queued the waiter ahead of the timeout callback. Advance
    # the clock before either resumes, reproducing a delayed event-loop turn.
    offset = 2.0
    with pytest.raises(ServiceError) as error:
        await task
    assert error.value.code == ErrorCode.TIMEOUT
    assert limiter.active == limiter.waiting == 0
    async with limiter.slot(deadline=loop.time() + 1):
        assert limiter.active == 1
    assert limiter.active == limiter.waiting == 0


@pytest.mark.parametrize(
    "concurrency, queue",
    [(True, 0), (1, False), (1.5, 0), (1, 0.5), (float("nan"), 0), (1, float("nan"))],
)
def test_non_integer_capacity_rejected(concurrency: int, queue: int) -> None:
    with pytest.raises(ValueError):
        CapacityLimiter(concurrency=concurrency, queue_limit=queue)

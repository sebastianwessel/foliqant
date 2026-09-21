"""Retain shared clients until cancellation-resistant async work has stopped."""

import asyncio
import logging
from typing import Protocol

from foliqant.adapters.telemetry.logging import LogEvent, emit_event
from foliqant.core.errors import ErrorCode


class AsyncDrain(Protocol):
    async def aclose(self, *, timeout: float = 10) -> bool: ...


async def drain_before_close(owner: AsyncDrain, *, timeout: float = 10) -> None:
    """Drain before releasing dependencies, including when the host is cancelled.

    A failed bounded drain emits one safe warning. Keep ownership and wait for
    remaining tasks rather than closing clients under them. Operators can force
    termination of an uncooperative process; unfinished in-memory runs are lost.
    """

    async def drain() -> None:
        if await owner.aclose(timeout=timeout):
            return
        emit_event(
            logging.getLogger("foliqant.runtime"),
            LogEvent.DEPENDENCY_REJECTED,
            level=logging.WARNING,
            error_code=ErrorCode.DEPENDENCY_FAILURE,
        )
        while not await owner.aclose(timeout=1):
            # An implementation may report immediately while its active call stops.
            await asyncio.sleep(0.1)

    task = asyncio.create_task(drain())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError

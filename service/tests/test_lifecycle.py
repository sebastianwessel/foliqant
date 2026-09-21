"""Shared clients stay owned while an in-memory call ignores cancellation."""

import asyncio

import pytest

from foliqant.lifecycle import drain_before_close


async def test_cancelled_host_cannot_close_clients_before_owned_call_stops():
    started, release, clients_closed = (asyncio.Event() for _ in range(3))

    class Owner:
        first = True

        async def aclose(self, *, timeout=10):
            if self.first:
                self.first = False
                started.set()
                return False
            await release.wait()
            return True

    async def close_scope():
        try:
            await drain_before_close(Owner(), timeout=0)
        finally:
            clients_closed.set()

    closing = asyncio.create_task(close_scope())
    await started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0.01)
    assert not clients_closed.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert clients_closed.is_set()

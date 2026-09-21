"""Worker-local control around persistent attempt accounting."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from foliqant.adapters.storage.budget import PersistentStepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import TokenUsage, Usage

_T = TypeVar("_T")


class WorkerBudget:
    """Retain persistence failures even when an executor translates exceptions."""

    def __init__(
        self, budget: PersistentStepBudget, interrupted: asyncio.Event, deadline: float
    ) -> None:
        self._budget = budget
        self._interrupted = interrupted
        self._deadline = deadline
        self.persistence_error: ServiceError | None = None

    async def _record(self, operation: Awaitable[_T]) -> _T:
        try:
            return await operation
        except ServiceError as error:
            if error.code not in (ErrorCode.CANCELLED, ErrorCode.BUDGET_EXHAUSTED):
                self.persistence_error = error
            raise

    async def start_model_request(self) -> int:
        if self._interrupted.is_set():
            raise asyncio.CancelledError
        if asyncio.get_running_loop().time() >= self._deadline:
            raise ServiceError(ErrorCode.TIMEOUT)
        return await self._record(self._budget.start_model_request())

    async def finish_model_request(self, ticket: int, usage: TokenUsage) -> None:
        await self._record(self._budget.finish_model_request(ticket, usage))

    async def start_tool_call(self) -> int:
        if self._interrupted.is_set():
            raise asyncio.CancelledError
        if asyncio.get_running_loop().time() >= self._deadline:
            raise ServiceError(ErrorCode.TIMEOUT)
        return await self._record(self._budget.start_tool_call())

    def snapshot(self) -> Usage:
        return self._budget.snapshot()

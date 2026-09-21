"""Host-owned tool sessions shared by explicit and model-driven invocations."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from foliqant.core.json import FrozenJson, FrozenObject, JsonValue

from .execution import StepContext


class ToolInputRequired(Exception):
    """Stop execution for human input without automatic interaction rounds."""


class ToolSession(Protocol):
    names: tuple[str, ...]
    successful: set[str]

    def input_schema(self, name: str) -> dict[str, JsonValue]: ...

    async def call(self, name: str, arguments: FrozenObject) -> FrozenJson: ...


class ToolRuntime(Protocol):
    def open(
        self, server: str, allowed: tuple[str, ...], context: StepContext
    ) -> AbstractAsyncContextManager[ToolSession]: ...

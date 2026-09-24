"""Trusted async handler registration and confined schema enforcement."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from jsonschema import Draft202012Validator

from foliqant.compiler.schema_helpers import validate_confined_tool_schema
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import HandlerStepPlan
from foliqant.ports.execution import OperationStep, StepContext

Handler = Callable[[FrozenObject, StepContext], Awaitable[StepOutcome]]


@dataclass(frozen=True, slots=True)
class HandlerRegistration:
    """Register trusted code explicitly; YAML can only select its declared name.

    The contract (schemas and effect) is declared in ``settings.yaml``. Schemas
    given here are optional; when present they must equal the declaration.
    """

    handler: Handler
    input_schema: FrozenObject | None = None
    output_schema: FrozenObject | None = None
    effect: Literal["read", "write"] = "read"

    def __post_init__(self) -> None:
        is_async = inspect.iscoroutinefunction(self.handler) or inspect.iscoroutinefunction(
            getattr(self.handler, "__call__", None)  # noqa: B004 - inspect async callable instances
        )
        if not is_async or self.effect not in {"read", "write"}:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        for field in ("input_schema", "output_schema"):
            value = getattr(self, field)
            if value is None:
                continue
            frozen = freeze_json(value)
            if not isinstance(frozen, Mapping):
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            object.__setattr__(self, field, frozen)


@dataclass(frozen=True, slots=True)
class _Binding:
    registration: HandlerRegistration
    input: Draft202012Validator
    output: Draft202012Validator


class HandlerExecutor:
    """Validate frozen arguments/results around one awaited host callback."""

    def __init__(self, handlers: Mapping[str, HandlerRegistration]) -> None:
        self._handlers: dict[str, _Binding] = {}
        try:
            for name, registration in handlers.items():
                if registration.input_schema is None or registration.output_schema is None:
                    raise ValueError("handler contract is not resolved")
                self._handlers[name] = _Binding(
                    registration,
                    validate_confined_tool_schema(
                        cast(dict[str, object], thaw_json(registration.input_schema))
                    ),
                    validate_confined_tool_schema(
                        cast(dict[str, object], thaw_json(registration.output_schema))
                    ),
                )
        except Exception:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        if not isinstance(step, HandlerStepPlan) or step.name != context.step_id:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        binding = self._handlers.get(step.handler)
        if binding is None or binding.registration.effect != "read":
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        try:
            binding.input.validate(thaw_json(inputs))
        except Exception:
            raise ServiceError(ErrorCode.INVALID_INPUT) from None
        try:
            async with asyncio.timeout_at(context.deadline):
                outcome = await binding.registration.handler(inputs, context)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ServiceError(ErrorCode.TIMEOUT) from None
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None
        try:
            if not isinstance(outcome, StepOutcome) or type(outcome.needs_review) is not bool:
                raise ValueError("invalid outcome")
            frozen = freeze_json(outcome.result)
            binding.output.validate(thaw_json(frozen))
        except Exception:
            raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
        # Review facts pass through unchanged; the runner validates their shape.
        return StepOutcome(
            frozen,
            needs_review=outcome.needs_review,
            selection=outcome.selection,
            unresolved_issues=outcome.unresolved_issues,
        )

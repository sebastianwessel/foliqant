"""Bounded PydanticAI model execution behind the workflow step port."""

import asyncio
import copy
import json
import math
from collections.abc import Mapping
from typing import Any, Never

from foliqant_decisions import DecisionOutput
from pydantic import ValidationError
from pydantic_ai import Agent, NativeOutput, ToolOutput, UnexpectedModelBehavior, UserError
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import OutputSpec, StructuredDict
from pydantic_ai.settings import ModelSettings

from foliqant.adapters.decisions import build_decision_input, validate_decision_result
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import DecisionStepPlan, HandlerStepPlan, LlmStepPlan, McpStepPlan
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.ports.tools import ToolInputRequired, ToolRuntime

from .accounting import request_token_usage
from .binding import ModelBinding
from .tools import ModelTools


def _fail(code: ErrorCode) -> Never:
    raise ServiceError(code) from None


class _InvocationModel(WrapperModel):
    """Apply one step's admission, deadline, budget, and accounting to every request."""

    def __init__(
        self, binding: ModelBinding, context: StepContext, tools: ModelTools | None = None
    ) -> None:
        super().__init__(binding.model)
        self._binding = binding
        self._context = context
        self._tools = tools

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if self._tools is not None:
            model_settings = self._tools.settings(model_settings)
        loop = asyncio.get_running_loop()
        if not math.isfinite(self._context.model_timeout) or self._context.model_timeout <= 0:
            _fail(ErrorCode.TIMEOUT)
        # Provider preparation performs offline capability/schema validation.
        # Discard its result: the SDK request prepares its own inputs once.
        self.wrapped.prepare_request(model_settings, model_request_parameters)
        async with self._binding.admission.slot(deadline=self._context.deadline):
            deadline = min(self._context.deadline, loop.time() + self._context.model_timeout)
            if deadline <= loop.time():
                _fail(ErrorCode.TIMEOUT)
            ticket = await self._context.budget.start_model_request()
            try:
                async with asyncio.timeout_at(deadline):
                    response = await self.wrapped.request(
                        messages,
                        model_settings,
                        model_request_parameters,
                    )
            except TimeoutError:
                raise ServiceError(ErrorCode.TIMEOUT) from None
            except ModelAPIError as error:
                # PydanticAI wraps SDK connection errors; only a configured,
                # typed timeout cause establishes a provider request timeout.
                if isinstance(error.__cause__, self._binding.timeout_errors):
                    raise ServiceError(ErrorCode.TIMEOUT) from None
                raise
            await self._context.budget.finish_model_request(
                ticket,
                request_token_usage(response.usage),
            )
            if response.finish_reason in {"length", "content_filter", "error"}:
                _fail(ErrorCode.INVALID_OUTPUT)
            if (
                response.provider_details is not None
                and "refusal" in response.provider_details
                and response.provider_details["refusal"] is not None
            ):
                _fail(ErrorCode.INVALID_OUTPUT)
            return response


def _prompt(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        _fail(ErrorCode.INVALID_INPUT)


def _structured_output(
    output_type: type[Any],
    *,
    mode: str,
    name: str,
    strict: bool = True,
) -> NativeOutput[Any] | ToolOutput[Any]:
    if mode == "native":
        return NativeOutput(output_type, name=name, strict=strict, template=False)
    if mode == "tool":
        return ToolOutput(output_type, name=name, max_retries=0, strict=strict)
    _fail(ErrorCode.INVALID_CONFIGURATION)


class ModelExecutor:
    """Execute decision and LLM steps with configured PydanticAI models."""

    def __init__(
        self,
        bindings: Mapping[str, ModelBinding],
        schemas: WorkflowSchemas,
        *,
        tools: ToolRuntime | None = None,
    ) -> None:
        self._bindings = dict(bindings)
        self._schemas = schemas
        self._tools = tools

    async def execute(
        self,
        step: OperationStep,
        inputs: FrozenObject,
        context: StepContext,
    ) -> StepOutcome:
        """Run one supported model step without retaining request-scoped state."""

        try:
            return await self._execute(step, inputs, context)
        except asyncio.CancelledError:
            raise
        except ToolInputRequired:
            return StepOutcome(None, needs_review=True)
        except ServiceError:
            raise
        except UnexpectedModelBehavior:
            _fail(ErrorCode.INVALID_OUTPUT)
        except ValidationError:
            _fail(ErrorCode.INVALID_OUTPUT)
        except UserError:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        except Exception:
            _fail(ErrorCode.DEPENDENCY_FAILURE)

    async def _execute(
        self,
        step: OperationStep,
        inputs: FrozenObject,
        context: StepContext,
    ) -> StepOutcome:
        if isinstance(step, (McpStepPlan, HandlerStepPlan)):
            _fail(ErrorCode.INVALID_CONFIGURATION)
        if context.step_id != step.name:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        binding = self._bindings.get(step.model)
        if binding is None:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        if isinstance(step, DecisionStepPlan):
            if not binding.supports_json_schema:
                _fail(ErrorCode.INVALID_CONFIGURATION)
            return await self._run_decision(step, inputs, context, binding)
        if not isinstance(step, LlmStepPlan):
            _fail(ErrorCode.INVALID_CONFIGURATION)
        if step.output_kind == "text" and not binding.supports_text:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        if step.output_kind == "schema" and not binding.supports_json_schema:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        if step.tools is None:
            return await self._run_llm(step, inputs, context, binding)
        if self._tools is None or not binding.supports_tools:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        async with self._tools.open(step.tools.server, step.tools.allow, context) as session:
            model_tools = ModelTools(session, step.tools)
            outcome = await self._run_llm(step, inputs, context, binding, model_tools)
            model_tools.validate_completion()
            return outcome

    async def _run_llm(
        self,
        step: LlmStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
        tools: ModelTools | None = None,
    ) -> StepOutcome:
        if step.output_kind == "text":
            return await self._run_text(step, inputs, context, binding, tools)
        if step.output_kind == "schema":
            return await self._run_schema(step, inputs, context, binding, tools)
        _fail(ErrorCode.INVALID_CONFIGURATION)

    @staticmethod
    def _agent(
        binding: ModelBinding,
        context: StepContext,
        *,
        instructions: str,
        output_type: OutputSpec[Any],
        tools: ModelTools | None = None,
    ) -> Agent[None, Any]:
        try:
            settings = copy.deepcopy(binding.settings)
        except Exception:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        agent: Agent[None, Any] = Agent(
            _InvocationModel(binding, context, tools),
            name="foliqant_workflow_model",
            instructions=instructions,
            output_type=output_type,
            tools=tools.tools if tools is not None else [],
            model_settings=settings,
            retries=0,
        )
        agent.instrument = False
        return agent

    async def _run_text(
        self,
        step: LlmStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
        tools: ModelTools | None = None,
    ) -> StepOutcome:
        agent = self._agent(
            binding,
            context,
            instructions=step.instructions,
            output_type=str,
            tools=tools,
        )
        result = await agent.run(_prompt(thaw_json(inputs)), retries=0)
        if not isinstance(result.output, str):
            _fail(ErrorCode.INVALID_OUTPUT)
        return StepOutcome(result.output)

    async def _run_decision(
        self,
        step: DecisionStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
    ) -> StepOutcome:
        task = build_decision_input(step, inputs)
        output_type = _structured_output(
            DecisionOutput,
            mode=binding.output_mode,
            name="decision_result",
        )
        agent = self._agent(
            binding,
            context,
            instructions=step.instructions,
            output_type=output_type,
        )
        result = await agent.run(task.model_dump_json(by_alias=True), retries=0)
        validated = validate_decision_result(step, task, result.output)
        return StepOutcome(
            validated.value,
            needs_review=not validated.answerable,
            route_key=validated.route_key,
        )

    async def _run_schema(
        self,
        step: LlmStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
        tools: ModelTools | None = None,
    ) -> StepOutcome:
        schema = self._schemas.provider_output_schema(step.name)
        provider_schema: dict[str, object] = {
            "type": "object",
            "properties": {"value": schema},
            "required": ["value"],
            "additionalProperties": False,
        }
        dynamic_output = StructuredDict(provider_schema, name="workflow_result")
        output_type = _structured_output(
            dynamic_output,
            mode=binding.output_mode,
            name="workflow_result",
            strict=False,
        )
        agent = self._agent(
            binding,
            context,
            instructions=step.instructions,
            output_type=output_type,
            tools=tools,
        )
        result = await agent.run(_prompt(thaw_json(inputs)), retries=0)
        if not isinstance(result.output, Mapping) or set(result.output) != {"value"}:
            _fail(ErrorCode.INVALID_OUTPUT)
        try:
            frozen = freeze_json(result.output["value"])
        except ServiceError:
            _fail(ErrorCode.INVALID_OUTPUT)
        self._schemas.validate_output(step.name, frozen)
        return StepOutcome(frozen)

"""Bounded PydanticAI model execution behind the workflow step port."""

from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Never

from pydantic import ValidationError
from pydantic_ai import (
    Agent,
    AgentRetries,
    ModelRetry,
    NativeOutput,
    ToolOutput,
    UnexpectedModelBehavior,
    UserError,
)
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ToolRetryError,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import OutputSpec, StructuredDict
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from foliqant.adapters.decisions import build_decision_input, validate_decision_result
from foliqant.adapters.decisions.instructions import decision_instructions
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.adapters.validation.violations import (
    InvalidOutput,
    pydantic_violation,
    schema_vocabulary,
)
from foliqant.contracts.decisions import DecisionOutput
from foliqant.core.errors import TIMEOUT_CODES, ErrorCode, ServiceError, timeout_code
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import DecisionStepPlan, HandlerStepPlan, LlmStepPlan, McpStepPlan
from foliqant.core.prompt import render_prompt
from foliqant.core.retry import TransientFailure, retry
from foliqant.decisions.contracts import DecisionInput
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.ports.tools import ToolInputRequired, ToolRuntime

from .accounting import request_token_usage
from .attempts import CURRENT_ATTEMPT, RequestAttempt
from .binding import ModelBinding
from .failures import behavior_failure, model_http_failure
from .instructions import model_instructions
from .stops import reasoning_consumed_budget, stop_error
from .tools import ModelTools

if TYPE_CHECKING:
    from foliqant.adapters.telemetry.models import ModelTelemetry


def _fail(code: ErrorCode) -> Never:
    raise ServiceError(code) from None


#: The step budget and ``max_iterations`` bound requests; PydanticAI's own default
#: request limit must not fail a step with a different, generic error first.
_UNLIMITED = UsageLimits(request_limit=None)
_DECISION_VOCABULARY = schema_vocabulary(DecisionOutput.model_json_schema(by_alias=True))


class _OutputRetryRefused(ServiceError):
    """The step's request limit refused a correction request; the invalid output is the cause."""

    def __init__(self, prompt: RetryPromptPart | None) -> None:
        super().__init__(ErrorCode.MODEL_REQUEST_LIMIT_REACHED)
        self.prompt = prompt


def _retry_prompt(messages: list[ModelMessage]) -> RetryPromptPart | None:
    last = messages[-1] if messages else None
    if not isinstance(last, ModelRequest):
        return None
    return next((part for part in last.parts if isinstance(part, RetryPromptPart)), None)


class _OutputCheck:
    """Validate a parsed output inside the agent so an invalid one can be corrected.

    A violation becomes the model's correction prompt while output retries remain;
    the last violation explains the ``invalid_output`` failure once they are spent.
    """

    def __init__(self, vocabulary: frozenset[str]) -> None:
        self.vocabulary = vocabulary
        self.last: InvalidOutput | None = None

    def check(self, validate: Any) -> Any:
        def validator(output: Any) -> Any:
            try:
                validate(output)
            except InvalidOutput as violation:
                self.last = violation
                raise ModelRetry(violation.feedback) from None
            return output

        return validator

    def refused(self, error: _OutputRetryRefused) -> ServiceError:
        """The invalid output whose correction the request limit refused."""
        if self.last is not None:
            return self.last
        if error.prompt is not None and isinstance(error.prompt.content, list):
            return pydantic_violation(error.prompt.content, self.vocabulary)
        return ServiceError(ErrorCode.INVALID_OUTPUT)

    def failure(self, error: UnexpectedModelBehavior) -> ServiceError:
        """The explained failure of an output that stayed invalid."""
        code = behavior_failure(error)
        if code is not ErrorCode.INVALID_OUTPUT:
            return ServiceError(code)
        item: BaseException | None = error.__cause__
        for _ in range(6):
            if item is None:
                break
            if isinstance(item, ModelRetry) and self.last is not None:
                return self.last
            if isinstance(item, ValidationError):
                return pydantic_violation(item.errors(include_input=False), self.vocabulary)
            if isinstance(item, ToolRetryError) and isinstance(item.tool_retry.content, list):
                return pydantic_violation(item.tool_retry.content, self.vocabulary)
            item = item.__cause__ or item.__context__
        if error.message.startswith("Exceeded maximum output retries"):
            return ServiceError(ErrorCode.INVALID_OUTPUT, reason="missing_output")
        return ServiceError(ErrorCode.INVALID_OUTPUT)


class _InvocationModel(WrapperModel):
    """Apply one step's admission, deadline, budget, and accounting to every request."""

    def __init__(
        self,
        binding: ModelBinding,
        context: StepContext,
        tools: ModelTools | None = None,
        telemetry: ModelTelemetry | None = None,
        max_iterations: int | None = None,
    ) -> None:
        super().__init__(binding.model)
        self._binding = binding
        self._context = context
        self._tools = tools
        self._telemetry = telemetry
        self._request_model = binding.model
        self._max_iterations = max_iterations
        self._iterations = 0
        if telemetry is not None:
            self.wrapped = telemetry.instrument(binding.model, binding.pricing)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # An output retry is bounded by `output_retries`, not by the loop's turns.
        # Tool calls are never retried, so a retry prompt always concerns the output.
        retry_prompt = _retry_prompt(messages)
        output_retry = retry_prompt is not None
        if self._max_iterations is not None and not output_retry:
            if self._iterations >= self._max_iterations:
                _fail(ErrorCode.ITERATION_LIMIT_REACHED)
            self._iterations += 1
        if self._tools is not None:
            model_settings = self._tools.settings(model_settings)
        loop = asyncio.get_running_loop()
        if not math.isfinite(self._context.model_timeout) or self._context.model_timeout <= 0:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        # Provider preparation performs offline capability/schema validation.
        # Discard its result: the SDK request prepares its own inputs once.
        self.wrapped.prepare_request(model_settings, model_request_parameters)
        deadline = self._context.deadline
        first_attempt = True
        retry_counted = False

        async def request_once(_attempt: int) -> ModelResponse:
            nonlocal deadline, first_attempt, retry_counted
            async with self._binding.admission.slot(deadline=deadline):
                if first_attempt:
                    deadline = min(deadline, loop.time() + self._context.model_timeout)
                    first_attempt = False
                if deadline <= loop.time():
                    _fail(self._timeout(loop.time()))
                try:
                    ticket = await self._context.budget.start_model_request(
                        self._request_model.model_name, self._binding.pricing
                    )
                except ServiceError as error:
                    if output_retry and error.code is ErrorCode.MODEL_REQUEST_LIMIT_REACHED:
                        raise _OutputRetryRefused(retry_prompt) from None
                    raise
                if output_retry and not retry_counted:
                    self._context.budget.record_output_retry()
                    retry_counted = True
                started_at = (
                    self._telemetry.start_request() if self._telemetry is not None else None
                )
                usage = None
                telemetry_error: ErrorCode | None = None
                attempt = CURRENT_ATTEMPT.set(
                    RequestAttempt(
                        _attempt,
                        output_retry,
                        deadline,
                        self._context.deadline,
                        self._binding.timeout_errors,
                    )
                )
                try:
                    try:
                        async with asyncio.timeout_at(deadline):
                            response = await self.wrapped.request(
                                messages,
                                model_settings,
                                model_request_parameters,
                            )
                    finally:
                        CURRENT_ATTEMPT.reset(attempt)
                    # Snapshot and validate the mutable SDK value once. Re-reading it
                    # for metrics and budget accounting could observe different data.
                    usage = request_token_usage(response.usage)
                    await self._context.budget.finish_model_request(ticket, usage)
                    if deadline <= loop.time():
                        _fail(self._timeout(loop.time()))
                    # Classify the stop reason inside the recorded region so the
                    # request metric carries the step's real failure code.
                    stopped = stop_error(response)
                    if stopped is ErrorCode.OUTPUT_LIMIT_REACHED:
                        # A length stop recurs with the same input and options:
                        # never retried. Its reason says what to change.
                        consumed = reasoning_consumed_budget(response, usage)
                        raise ServiceError(
                            stopped,
                            reason=None
                            if consumed is None
                            else "reasoning_consumed_budget"
                            if consumed
                            else "answer_exceeded_budget",
                        ) from None
                    if stopped is not None:
                        _fail(stopped)
                    return response
                except asyncio.CancelledError:
                    telemetry_error = ErrorCode.CANCELLED
                    raise
                except TimeoutError:
                    telemetry_error = self._timeout(loop.time())
                    raise ServiceError(telemetry_error) from None
                except ModelHTTPError as error:
                    # A transient status keeps its retry permission for `retry`.
                    failure = model_http_failure(error)
                    telemetry_error = failure.code
                    raise failure from None
                except self._binding.timeout_errors:
                    # Some native SDKs let a typed transport timeout escape
                    # without wrapping it in ModelAPIError.
                    telemetry_error = ErrorCode.REQUEST_TIMEOUT
                    raise ServiceError(ErrorCode.REQUEST_TIMEOUT) from None
                except ModelAPIError as error:
                    # PydanticAI wraps SDK connection errors; only a configured,
                    # typed timeout cause establishes a provider request timeout.
                    if isinstance(error.__cause__, self._binding.timeout_errors):
                        telemetry_error = ErrorCode.REQUEST_TIMEOUT
                        raise ServiceError(ErrorCode.REQUEST_TIMEOUT) from None
                    # A connection failure without a response: a model request has
                    # no external effect, so it may be repeated within the deadline.
                    telemetry_error = ErrorCode.DEPENDENCY_FAILURE
                    raise TransientFailure(ErrorCode.DEPENDENCY_FAILURE) from None
                except ServiceError as error:
                    telemetry_error = error.code
                    raise
                except Exception:
                    telemetry_error = ErrorCode.DEPENDENCY_FAILURE
                    raise
                finally:
                    if self._telemetry is not None and started_at is not None:
                        self._telemetry.record_request(
                            self._request_model,
                            started_at,
                            usage,
                            telemetry_error,
                        )

        try:
            return await retry(request_once, policy=self._binding.retry, deadline=lambda: deadline)
        except ServiceError as error:
            if error.code in TIMEOUT_CODES and not error.retryable:
                raise ServiceError(self._timeout(loop.time())) from None
            raise

    def _timeout(self, now: float) -> ErrorCode:
        return timeout_code(now, self._context.deadline)


def _prompt(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        _fail(ErrorCode.INVALID_INPUT)


def _decision_prompt(task: DecisionInput) -> str:
    """Render the runtime request without changing native contract serialization."""
    return task.model_dump_json(by_alias=True)


def _structured_output(
    output_type: type[Any],
    *,
    mode: str,
    name: str,
    retries: int,
    strict: bool = True,
) -> NativeOutput[Any] | ToolOutput[Any]:
    if mode == "native":
        return NativeOutput(output_type, name=name, strict=strict, template=False)
    if mode == "tool":
        return ToolOutput(output_type, name=name, max_retries=retries, strict=strict)
    _fail(ErrorCode.INVALID_CONFIGURATION)


class ModelExecutor:
    """Execute decision and LLM steps with configured PydanticAI models."""

    def __init__(
        self,
        bindings: Mapping[str, ModelBinding],
        schemas: WorkflowSchemas,
        *,
        tools: ToolRuntime | None = None,
        telemetry: ModelTelemetry | None = None,
    ) -> None:
        self._bindings = dict(bindings)
        self._schemas = schemas
        self._tools = tools
        self._telemetry = telemetry

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
        except UnexpectedModelBehavior as error:
            _fail(behavior_failure(error))
        except UsageLimitExceeded:
            _fail(ErrorCode.MODEL_REQUEST_LIMIT_REACHED)
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

    def _agent(
        self,
        binding: ModelBinding,
        context: StepContext,
        *,
        instructions: str,
        output_type: OutputSpec[Any],
        tools: ModelTools | None = None,
        max_iterations: int | None = None,
        check: _OutputCheck | None = None,
        validate: Any = None,
    ) -> Agent[None, Any]:
        try:
            settings = copy.deepcopy(binding.settings)
        except Exception:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        agent: Agent[None, Any] = Agent(
            _InvocationModel(
                binding,
                context,
                tools,
                self._telemetry,
                max_iterations=max_iterations,
            ),
            name="foliqant_workflow_model",
            instructions=instructions,
            output_type=output_type,
            tools=tools.tools if tools is not None else [],
            model_settings=settings,
            retries=_retries(binding),
        )
        if check is not None and validate is not None:
            agent.output_validator(check.check(validate))
        agent.instrument = False
        return agent

    async def _run_agent(
        self, agent: Agent[None, Any], prompt: str, binding: ModelBinding, check: _OutputCheck
    ) -> Any:
        """Run with bounded output retries; an output that stays invalid is explained."""
        try:
            return await agent.run(prompt, retries=_retries(binding), usage_limits=_UNLIMITED)
        except _OutputRetryRefused as error:
            raise check.refused(error) from None
        except UnexpectedModelBehavior as error:
            raise check.failure(error) from None

    async def _run_text(
        self,
        step: LlmStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
        tools: ModelTools | None = None,
    ) -> StepOutcome:
        check = _OutputCheck(frozenset())
        agent = self._agent(
            binding,
            context,
            instructions=model_instructions(step.instructions),
            output_type=str,
            tools=tools,
            max_iterations=step.max_iterations,
        )
        user_input = (
            render_prompt(step.prompt, inputs)
            if step.prompt is not None
            else _prompt(thaw_json(inputs))
        )
        result = await self._run_agent(agent, user_input, binding, check)
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
            retries=binding.output_retries,
        )
        check = _OutputCheck(_DECISION_VOCABULARY)
        agent = self._agent(
            binding,
            context,
            instructions=decision_instructions(step.instructions),
            output_type=output_type,
            check=check,
            validate=lambda output: validate_decision_result(step, task, output),
        )
        result = await self._run_agent(agent, _decision_prompt(task), binding, check)
        validated = validate_decision_result(step, task, result.output)
        return StepOutcome(
            validated.value,
            needs_review=not validated.answerable,
            selection=validated.selection,
            unresolved_issues=validated.unresolved_issues,
        )

    async def _run_schema(
        self,
        step: LlmStepPlan,
        inputs: FrozenObject,
        context: StepContext,
        binding: ModelBinding,
        tools: ModelTools | None = None,
    ) -> StepOutcome:
        schema = self._schemas.provider_output_schema(context.flow_id, step.name)
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
            retries=binding.output_retries,
            strict=False,
        )
        check = _OutputCheck(schema_vocabulary(provider_schema))

        def validate(output: object) -> FrozenJson:
            if not isinstance(output, Mapping) or set(output) != {"value"}:
                raise InvalidOutput(
                    "schema_violation",
                    location="/",
                    constraint="required",
                    feedback="Return one JSON object with exactly the property `value`.",
                )
            try:
                frozen = freeze_json(output["value"])
            except ServiceError:
                raise InvalidOutput(
                    "schema_violation",
                    location="/value",
                    constraint="type",
                    feedback="The value at /value is not valid JSON data.",
                ) from None
            # Host validation of the authored schema; locations are relative to `value`.
            self._schemas.validate_output(context.flow_id, step.name, frozen)
            return frozen

        agent = self._agent(
            binding,
            context,
            instructions=model_instructions(step.instructions),
            output_type=output_type,
            tools=tools,
            max_iterations=step.max_iterations,
            check=check,
            validate=validate,
        )
        user_input = (
            render_prompt(step.prompt, inputs)
            if step.prompt is not None
            else _prompt(thaw_json(inputs))
        )
        result = await self._run_agent(agent, user_input, binding, check)
        return StepOutcome(validate(result.output))


def _retries(binding: ModelBinding) -> AgentRetries:
    """Tool calls are never retried; invalid outputs up to ``output_retries`` times."""
    return {"tools": 0, "output": binding.output_retries}

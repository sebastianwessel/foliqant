"""Deterministic acceptance tests for bounded PydanticAI model execution."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal, cast

import pytest
from flow_fixtures import operation_flow
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from foliqant.adapters.models import (
    ModelBinding,
    ModelExecutor,
    request_token_usage,
)
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.core.admission import CapacityLimiter
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext, TokenUsage
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import (
    BindingPlan,
    DecisionOptionPlan,
    DecisionQuestionPlan,
    DecisionStepPlan,
    HandlerStepPlan,
    LlmStepPlan,
    SchemaResourcePlan,
    SourceLocation,
    ToolPolicyPlan,
    WorkflowPlan,
)
from foliqant.core.retry import RetryPolicy
from foliqant.ports.execution import StepContext

_LOCATION = SourceLocation("steps/test.yaml", 1, 1)
_USAGE = RequestUsage(
    input_tokens=10,
    output_tokens=4,
    cache_read_tokens=3,
    cache_write_tokens=2,
    output_reasoning_tokens=1,
)


def _frozen_object(value: object) -> FrozenObject:
    return cast(FrozenObject, freeze_json(value))


def _text_step(*, tools: ToolPolicyPlan | None = None) -> LlmStepPlan:
    return LlmStepPlan(
        name="generate",
        type="llm",
        location=_LOCATION,
        model="configured-alias",
        instructions="Return a concise result.",
        output_kind="text",
        tools=tools,
    )


def _schema_step(schema_value: object | None = None) -> LlmStepPlan:
    schema = _frozen_object(
        schema_value
        if schema_value is not None
        else {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
    )
    return LlmStepPlan(
        name="generate",
        type="llm",
        location=_LOCATION,
        model="configured-alias",
        instructions="Return the computed answer.",
        output_kind="schema",
        output_schema_path="schemas/result.json",
        output_schema=schema,
    )


def _decision_step() -> DecisionStepPlan:
    return DecisionStepPlan(
        name="classify",
        type="decision",
        location=_LOCATION,
        model="configured-alias",
        sources=(("ticket", BindingPlan(kind="pointer", pointer="/payload/ticket")),),
        questions=(
            DecisionQuestionPlan(
                id="classify",
                type="choice",
                prompt="Which queue applies?",
                criteria=("Use only the ticket.",),
                allowed_source_ids=("ticket",),
                options=(
                    DecisionOptionPlan("billing", "Billing support"),
                    DecisionOptionPlan("technical", "Technical support"),
                ),
            ),
        ),
        instructions="Classify the ticket.",
    )


def _plan(step: DecisionStepPlan | LlmStepPlan | HandlerStepPlan) -> WorkflowPlan:
    resources: tuple[SchemaResourcePlan, ...] = ()
    if isinstance(step, LlmStepPlan) and step.output_schema is not None:
        resources = (SchemaResourcePlan("schemas/result.json", step.output_schema),)
    return WorkflowPlan(
        name="test-workflow",
        revision="a" * 64,
        start="main",
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=resources,
        output=None,
        flows=(operation_flow(step),),
        location=SourceLocation("workflow.yaml", 1, 1),
    )


def _context(
    step_name: str,
    *,
    budget: StepBudget | None = None,
    model_timeout: float = 1,
    deadline: float | None = None,
) -> StepContext:
    loop = asyncio.get_running_loop()
    return StepContext(
        execution_id="execution-id",
        workflow="test-workflow",
        revision="a" * 64,
        step_id=step_name,
        flow_id="main",
        caller=CallerContext(Identity("tenant", "principal"), _frozen_object({})),
        deadline=deadline if deadline is not None else loop.time() + 2,
        model_timeout=model_timeout,
        tool_timeout=1,
        budget=budget or StepBudget(model_requests=1, tool_calls=0),
    )


def _binding(
    function: Callable[..., Any],
    *,
    output_mode: Literal["native", "tool"] = "native",
    admission: CapacityLimiter | None = None,
    settings: ModelSettings | None = None,
    supports_text: bool = True,
    supports_json_schema: bool = True,
    retry: RetryPolicy | None = None,
    output_retries: int = 0,
) -> ModelBinding:
    # Fixtures disable output retries so one scripted response is one request;
    # tests of the correction loop pass `output_retries` explicitly.
    return ModelBinding(
        model=FunctionModel(function),
        settings=settings or ModelSettings(),
        admission=admission or CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode=output_mode,
        supports_text=supports_text,
        supports_json_schema=supports_json_schema,
        retry=retry or RetryPolicy(),
        output_retries=output_retries,
    )


def _executor(step: Any, binding: ModelBinding) -> ModelExecutor:
    return ModelExecutor(
        {"configured-alias": binding},
        WorkflowSchemas(_plan(step)),
    )


def _structured_response(info: Any, value: dict[str, object]) -> ModelResponse:
    if info.model_request_parameters.output_mode == "native":
        return ModelResponse(
            parts=[TextPart(json.dumps(value))],
            usage=_USAGE,
        )
    assert info.output_tools
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, value)],
        usage=_USAGE,
    )


def _decision_result(
    *,
    status: str = "answerable",
    option_id: str | None = "billing",
) -> dict[str, object]:
    answerable = status == "answerable"
    return {
        "results": [
            {
                "questionId": "classify",
                "type": "choice",
                "answerability": {
                    "status": status,
                    "issues": [] if answerable else ["no_supported_answer"],
                },
                "answer": None if option_id is None else {"optionId": option_id},
                "reason": "The ticket explicitly identifies billing."
                if answerable
                else "The queue cannot be established.",
                "evidence_strength": None if option_id is None else "strong",
            }
        ],
    }


@pytest.mark.parametrize("mode", ["native", "tool"])
async def test_schema_output_uses_explicit_mode_and_independent_validation(
    mode: Literal["native", "tool"],
) -> None:
    step = _schema_step()
    seen_mode = ""

    async def model(_messages: Any, info: Any) -> ModelResponse:
        nonlocal seen_mode
        seen_mode = info.model_request_parameters.output_mode
        return _structured_response(info, {"value": {"answer": 42}})

    budget = StepBudget(model_requests=1, tool_calls=0)
    outcome = await _executor(step, _binding(model, output_mode=mode)).execute(
        step,
        _frozen_object({"question": "six times seven"}),
        _context(step.name, budget=budget),
    )

    assert seen_mode == mode
    assert thaw_json(outcome.result) == {"answer": 42}
    assert budget.snapshot().tokens == TokenUsage(10, 4, 3, 2, 1)


@pytest.mark.parametrize("output_kind", ["text", "schema"])
async def test_one_iteration_allows_plain_llm_final_answer(output_kind: str) -> None:
    step = replace(
        _text_step() if output_kind == "text" else _schema_step(),
        max_iterations=1,
    )
    calls = 0

    async def model(_messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if output_kind == "text":
            return ModelResponse(parts=[TextPart("ok")], usage=_USAGE)
        return _structured_response(info, {"value": {"answer": 42}})

    budget = StepBudget(model_requests=2, tool_calls=0)
    outcome = await _executor(step, _binding(model)).execute(
        step,
        _frozen_object({}),
        _context(step.name, budget=budget),
    )
    assert thaw_json(outcome.result) == ("ok" if output_kind == "text" else {"answer": 42})
    assert calls == budget.snapshot().model_requests == 1


async def test_schema_output_is_rejected_by_host_validator_after_usage_is_recorded() -> None:
    step = _schema_step()

    async def model(_messages: Any, info: Any) -> ModelResponse:
        return _structured_response(info, {"value": {"answer": "PRIVATE INVALID VALUE"}})

    budget = StepBudget(model_requests=1, tool_calls=0)
    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(model)).execute(
            step,
            _frozen_object({"private": "input"}),
            _context(step.name, budget=budget),
        )
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert "PRIVATE" not in str(error.value)
    assert budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens.input_tokens == 10


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "integer", "minimum": 1}, 7),
        ({"type": "array", "items": {"type": "string"}}, ["a", "b"]),
    ],
)
async def test_scalar_and_array_schema_outputs_are_wrapped_only_at_provider_boundary(
    schema: dict[str, object], value: object
) -> None:
    step = _schema_step(schema)

    async def model(_messages: Any, info: Any) -> ModelResponse:
        output_schema = info.model_request_parameters.output_object.json_schema
        assert output_schema["type"] == "object"
        assert output_schema["required"] == ["value"]
        return _structured_response(info, {"value": value})

    outcome = await _executor(step, _binding(model)).execute(
        step,
        _frozen_object({}),
        _context(step.name),
    )
    assert thaw_json(outcome.result) == value


async def test_local_file_refs_are_bundled_for_provider_and_validated_by_host() -> None:
    root = _frozen_object({"$ref": "defs.json#/$defs/result"})
    definitions = _frozen_object(
        {
            "$defs": {
                "result": {
                    "type": "object",
                    "properties": {"answer": {"const": 42}},
                    "required": ["answer"],
                    "additionalProperties": False,
                }
            }
        }
    )
    step = replace(_schema_step(), output_schema=root)
    plan = replace(
        _plan(step),
        schema_resources=(
            SchemaResourcePlan("schemas/result.json", root),
            SchemaResourcePlan("schemas/defs.json", definitions),
        ),
    )
    calls = 0

    async def model(_messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        provider_schema = info.model_request_parameters.output_object.json_schema
        assert provider_schema["type"] == "object"
        assert "$defs" not in provider_schema
        return _structured_response(info, {"value": {"answer": 42}})

    executor = ModelExecutor(
        {"configured-alias": _binding(model)},
        WorkflowSchemas(plan),
    )
    outcome = await executor.execute(step, _frozen_object({}), _context(step.name))
    assert thaw_json(outcome.result) == {"answer": 42}
    assert calls == 1


async def test_recursive_provider_schema_is_rejected_before_model_io() -> None:
    recursive = {
        "$defs": {
            "node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/node"}},
            }
        },
        "$ref": "#/$defs/node",
    }
    step = _schema_step(recursive)
    calls = 0

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("unexpected")], usage=_USAGE)

    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(model)).execute(
            step,
            _frozen_object({}),
            _context(step.name),
        )
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION
    assert calls == 0


async def test_decision_output_is_semantically_validated_and_uncertainty_routes_to_review() -> None:
    step = _decision_step()

    async def uncertain(_messages: Any, info: Any) -> ModelResponse:
        return _structured_response(info, _decision_result(status="undetermined", option_id=None))

    outcome = await _executor(step, _binding(uncertain, output_mode="tool")).execute(
        step,
        _frozen_object({"ticket": "The service is unavailable."}),
        _context(step.name),
    )
    assert outcome.needs_review is True
    assert not hasattr(outcome, "route_key")

    async def invalid(_messages: Any, info: Any) -> ModelResponse:
        return _structured_response(info, _decision_result(option_id="unknown"))

    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(invalid)).execute(
            step,
            _frozen_object({"ticket": "Billing failed."}),
            _context(step.name),
        )
    assert error.value.code == ErrorCode.INVALID_OUTPUT


async def test_structurally_invalid_native_decision_is_not_repaired() -> None:
    step = _decision_step()
    calls = 0

    async def invalid(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart('{"unexpected":2}')], usage=_USAGE)

    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(invalid)).execute(
            step,
            _frozen_object({"ticket": "Billing failed."}),
            _context(step.name),
        )
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert calls == 1


async def test_concurrent_function_model_requests_overlap_with_resource_capacity() -> None:
    step = _text_step()
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("ok")], usage=_USAGE)

    executor = _executor(
        step,
        _binding(model, admission=CapacityLimiter(concurrency=2, queue_limit=0)),
    )
    first = asyncio.create_task(
        executor.execute(step, _frozen_object({"id": 1}), _context(step.name))
    )
    second = asyncio.create_task(
        executor.execute(step, _frozen_object({"id": 2}), _context(step.name))
    )
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()
    assert (await first).result == "ok"
    assert (await second).result == "ok"


async def test_cancellation_propagates_and_releases_admission_without_refunding_attempt() -> None:
    step = _text_step()
    entered = asyncio.Event()
    limiter = CapacityLimiter(concurrency=1, queue_limit=0)
    budget = StepBudget(model_requests=1, tool_calls=0)

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    task = asyncio.create_task(
        _executor(step, _binding(model, admission=limiter)).execute(
            step,
            _frozen_object({}),
            _context(step.name, budget=budget),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.active == limiter.waiting == 0
    assert budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens == TokenUsage()


async def test_each_request_obeys_model_timeout_within_absolute_deadline() -> None:
    step = _text_step()
    budget = StepBudget(model_requests=1, tool_calls=0)

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(model)).execute(
            step,
            _frozen_object({}),
            _context(step.name, budget=budget, model_timeout=0.01),
        )
    assert error.value.code == ErrorCode.REQUEST_TIMEOUT
    assert budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens == TokenUsage()


async def test_model_timeout_starts_after_admission_and_waiting_does_not_charge_attempt() -> None:
    step = _text_step()
    limiter = CapacityLimiter(concurrency=1, queue_limit=1)
    budget = StepBudget(model_requests=1, tool_calls=0)
    entered = asyncio.Event()

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        entered.set()
        return ModelResponse(parts=[TextPart("ok")], usage=_USAGE)

    deadline = asyncio.get_running_loop().time() + 1
    async with limiter.slot(deadline=deadline):
        task = asyncio.create_task(
            _executor(step, _binding(model, admission=limiter)).execute(
                step,
                _frozen_object({}),
                _context(
                    step.name,
                    budget=budget,
                    model_timeout=0.01,
                    deadline=deadline,
                ),
            )
        )
        await asyncio.sleep(0.02)
        assert budget.snapshot().model_requests == 0
        assert entered.is_set() is False
    assert (await task).result == "ok"
    assert budget.snapshot().model_requests == 1


async def test_capacity_rejection_does_not_charge_an_attempt() -> None:
    step = _text_step()
    limiter = CapacityLimiter(concurrency=1, queue_limit=0)
    budget = StepBudget(model_requests=1, tool_calls=0)

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        raise AssertionError("capacity rejection must prevent model I/O")

    async with limiter.slot(deadline=asyncio.get_running_loop().time() + 1):
        with pytest.raises(ServiceError) as error:
            await _executor(step, _binding(model, admission=limiter)).execute(
                step,
                _frozen_object({}),
                _context(step.name, budget=budget),
            )
    assert error.value.code == ErrorCode.CAPACITY_EXCEEDED
    assert budget.snapshot().model_requests == 0


@pytest.mark.parametrize(
    ("finish_reason", "provider_details", "code"),
    [
        ("length", None, ErrorCode.OUTPUT_LIMIT_REACHED),
        ("content_filter", None, ErrorCode.OUTPUT_REFUSED),
        ("stop", {"refusal": "PRIVATE refusal text"}, ErrorCode.OUTPUT_REFUSED),
        ("error", None, ErrorCode.DEPENDENCY_FAILURE),
    ],
)
async def test_nonfinal_responses_fail_with_their_stop_reason_and_are_not_retried(
    finish_reason: str, provider_details: dict[str, Any] | None, code: ErrorCode
) -> None:
    step = _text_step()
    calls = 0

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[TextPart("PRIVATE partial output")],
            usage=_USAGE,
            finish_reason=cast(Any, finish_reason),
            provider_details=provider_details,
        )

    budget = StepBudget(model_requests=3, tool_calls=0)
    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(model, retry=RetryPolicy(max_attempts=3))).execute(
            step,
            _frozen_object({}),
            _context(step.name, budget=budget),
        )
    assert error.value.code == code
    assert error.value.retryable is False
    assert "PRIVATE" not in str(error.value)
    assert calls == 1
    # The stopped request consumed its reported tokens.
    assert budget.snapshot().model_requests == 1


async def test_unsupported_tools_and_capabilities_fail_before_model_io() -> None:
    calls = 0

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("unexpected")], usage=_USAGE)

    tools = ToolPolicyPlan("mcp", ("lookup",), "auto")
    tool_step = _text_step(tools=tools)
    with pytest.raises(ServiceError) as error:
        await _executor(tool_step, _binding(model)).execute(
            tool_step,
            _frozen_object({}),
            _context(tool_step.name),
        )
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION

    schema_step = _schema_step()
    with pytest.raises(ServiceError) as error:
        await _executor(
            schema_step,
            _binding(model, supports_json_schema=False),
        ).execute(schema_step, _frozen_object({}), _context(schema_step.name))
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION
    assert calls == 0


async def test_unknown_model_exception_is_replaced_with_safe_dependency_failure() -> None:
    step = _text_step()

    async def model(_messages: Any, _info: Any) -> ModelResponse:
        raise RuntimeError("PRIVATE provider diagnostic")

    with pytest.raises(ServiceError) as error:
        await _executor(step, _binding(model)).execute(
            step,
            _frozen_object({"private": "input"}),
            _context(step.name),
        )
    assert error.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert "PRIVATE" not in str(error.value)


def test_missing_request_usage_remains_unknown_and_explicit_zero_is_preserved() -> None:
    assert request_token_usage(RequestUsage()) == TokenUsage()
    assert request_token_usage(
        RequestUsage(input_tokens=0, output_tokens=0, output_reasoning_tokens=0)
    ) == TokenUsage(input_tokens=0, output_tokens=0, reasoning_output_tokens=0)


async def test_binding_settings_are_copied_for_each_invocation() -> None:
    step = _text_step()
    settings = ModelSettings(extra_body={"nested": {"value": "original"}})

    async def model(_messages: Any, info: Any) -> ModelResponse:
        info.model_settings["extra_body"]["nested"]["value"] = "mutated"
        return ModelResponse(parts=[TextPart("ok")], usage=_USAGE)

    executor = _executor(step, _binding(model, settings=settings))
    await executor.execute(step, _frozen_object({}), _context(step.name))
    await executor.execute(step, _frozen_object({}), _context(step.name))
    assert settings["extra_body"] == {"nested": {"value": "original"}}


@pytest.mark.parametrize("reasoning", [None, 0, 2])
def test_responses_sdk_usage_preserves_reasoning_presence(reasoning: int | None) -> None:
    from openai.types.responses import Response
    from pydantic_ai.models.openai import _map_usage

    reported: dict[str, Any] = {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    if reasoning is not None:
        reported["output_tokens_details"] = {"reasoning_tokens": reasoning}
    response = Response.model_construct(model="gpt-4o-mini", usage=reported)
    usage = _map_usage(response, "openai", "https://api.openai.com/v1", "gpt-4o-mini")
    assert request_token_usage(usage).reasoning_output_tokens == reasoning


@pytest.mark.parametrize("mode", ["native", "tool"])
@pytest.mark.parametrize("valid", [True, False])
async def test_authored_dictionary_schema_survives_openai_wire_conversion(
    monkeypatch: pytest.MonkeyPatch, mode: str, valid: bool
) -> None:
    import httpx2
    import openai

    from foliqant.adapters.models import open_model_bindings
    from foliqant.contracts.models import ModelProfiles

    authored = {
        "type": "object",
        "additionalProperties": {"type": "integer"},
        "minProperties": 1,
    }
    step = _schema_step(authored)
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        body = json.loads(request.content)
        definition = (
            body["response_format"]["json_schema"]
            if mode == "native"
            else body["tools"][0]["function"]
        )
        schema = definition["schema"] if mode == "native" else definition["parameters"]
        assert schema["properties"]["value"] == {**authored, "properties": {}}
        assert definition.get("strict", False) is False
        output = json.dumps({"value": {"score": 3} if valid else {}})
        message: dict[str, Any] = {"role": "assistant"}
        if mode == "native":
            message["content"] = output
        else:
            message["tool_calls"] = [
                {
                    "id": "result-call",
                    "type": "function",
                    "function": {"name": definition["name"], "arguments": output},
                }
            ]
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-schema",
                "object": "chat.completion",
                "created": 1,
                "model": "local-model",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    original = openai.AsyncOpenAI

    def mock_client(**kwargs: Any) -> openai.AsyncOpenAI:
        return original(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", mock_client)
    profiles = ModelProfiles.model_validate(
        {
            "models": {
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "https://local.example.test/v1",
                    "model": "local-model",
                    "output_mode": mode,
                }
            }
        }
    )
    step = replace(step, model="local")
    budget = StepBudget(model_requests=1, tool_calls=0)
    async with open_model_bindings(profiles, environment={}) as bindings:
        executor = ModelExecutor(bindings, WorkflowSchemas(_plan(step)))
        if valid:
            result = await executor.execute(
                step, _frozen_object({}), _context(step.name, budget=budget)
            )
            assert thaw_json(result.result) == {"score": 3}
        else:
            with pytest.raises(ServiceError) as error:
                await executor.execute(step, _frozen_object({}), _context(step.name, budget=budget))
            assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert len(requests) == budget.snapshot().model_requests == 1

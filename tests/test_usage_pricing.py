"""Usage by provider model and configured cost estimates, from requests to public results."""

import json
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from test_cli import _cli
from test_conditional_runtime import Handlers, _corrector, _found_on, _repeat_plan
from test_flow_collection_runtime import collection_plan, envelope, item, runner
from test_model_executor import _context, _frozen_object, _plan, _text_step
from test_trace_privacy import RecordingProcessor

from foliqant.adapters.models import ModelBinding, ModelExecutor, request_token_usage
from foliqant.adapters.telemetry.models import ModelTelemetry
from foliqant.adapters.telemetry.privacy import SafeSpanProcessor, TelemetryLabels
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler.models import ModelRegistry
from foliqant.contracts.execution import ExecutionResult, Usage, to_execution_result
from foliqant.contracts.models import CompatibleModelConfig, ModelPricing, ModelProfileOverride
from foliqant.core.admission import CapacityLimiter
from foliqant.core.budget import StepBudget
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Cost, ModelUsage, StepOutcome, TokenUsage, usage_value
from foliqant.core.execution import Usage as CoreUsage
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, thaw_json
from foliqant.core.plan import SourceLocation
from foliqant.core.pricing import PricingPlan
from foliqant.core.runner import WorkflowRunner
from foliqant.ports.execution import StepContext

# GPT-5.6 Terra list prices used as the documented example; prices change.
_TERRA: dict[str, Any] = {
    "currency": "USD",
    "input_per_million": 2.00,
    "cached_input_per_million": 0.20,
    "output_per_million": 12.00,
    "long_context": {
        "threshold_input_tokens": 272_000,
        "input_per_million": 4.00,
        "cached_input_per_million": 0.40,
        "output_per_million": 18.00,
    },
}


def _pricing(**changes: Any) -> PricingPlan:
    return ModelPricing.model_validate({**_TERRA, **changes}, strict=True).plan()


TERRA = _pricing()


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        # 60k uncached x $2 + 40k cached x $0.20 + 5k output (incl. reasoning) x $12
        (TokenUsage(100_000, 5_000, 40_000, None, 2_000), Decimal("0.188")),
        # Exactly at the threshold the base tier still applies.
        (TokenUsage(272_000, 0, 0, None, None), Decimal("0.544")),
        # Above it every token of the request uses the long-context tier.
        (TokenUsage(300_000, 10_000, 100_000, 0, None), Decimal("1.02")),
        (TokenUsage(0, 0, 0, 0, 0), Decimal(0)),
    ],
)
def test_per_request_cost_uses_cached_and_long_context_prices(tokens, expected):
    cost = TERRA.request_cost(tokens)
    assert cost == Cost("USD", expected)
    assert type(cost.amount) is Decimal


def test_cached_input_falls_back_to_input_price_and_reasoning_can_bill_as_input():
    plain = _pricing(cached_input_per_million=None, long_context=None)
    # Cached input is billed as input, so the cached count is not needed.
    assert plain.request_cost(TokenUsage(1_000, 100)).amount == Decimal("0.0032")
    long_default = _pricing(
        long_context={
            "threshold_input_tokens": 10,
            "input_per_million": 4,
            "output_per_million": 18,
        }
    )
    # A long-context tier without a cached price bills cached input at its input price.
    assert long_default.request_cost(TokenUsage(1_000, 0, 500)).amount == Decimal("0.004")
    as_input = _pricing(
        reasoning_billed_as="input",
        input_per_million=1,
        cached_input_per_million=None,
        output_per_million=10,
        long_context=None,
    )
    # 1000 input + 200 reasoning at $1, the remaining 300 output tokens at $10.
    assert as_input.request_cost(TokenUsage(1_000, 500, None, None, 200)).amount == Decimal(
        "0.0042"
    )


@pytest.mark.parametrize(
    ("pricing", "tokens"),
    [
        (TERRA, None),
        (TERRA, TokenUsage(None, 10, 0)),
        (TERRA, TokenUsage(10, None, 0)),
        # A configured cached price needs the cached count.
        (TERRA, TokenUsage(10, 10, None)),
        (_pricing(reasoning_billed_as="input"), TokenUsage(10, 10, 0, None, None)),
    ],
)
def test_unknown_counts_leave_the_cost_unknown(pricing, tokens):
    cost = pricing.request_cost(tokens)
    assert cost.amount is None and cost.currency == "USD"


def test_reasoning_count_is_not_needed_when_billed_as_output():
    assert TERRA.request_cost(TokenUsage(10, 10, 0, None, None)).amount == Decimal("0.00014")


@pytest.mark.parametrize(
    "change",
    [
        {"currency": "EUR"},
        {"input_per_million": "2.00"},
        {"input_per_million": -1},
        {"input_per_million": True},
        {"output_per_million": float("inf")},
        {"reasoning_billed_as": "cached"},
        {"reference_model": " "},
        {"unexpected": 1},
        {
            "long_context": {
                "threshold_input_tokens": 0,
                "input_per_million": 1,
                "output_per_million": 1,
            }
        },
        {"long_context": {"threshold_input_tokens": 10, "input_per_million": 1}},
    ],
)
def test_pricing_validation_is_strict(change):
    with pytest.raises(ValidationError):
        ModelPricing.model_validate({**_TERRA, **change}, strict=True)
    with pytest.raises(ValidationError):
        ModelPricing.model_validate(
            {key: value for key, value in _TERRA.items() if key != "currency"}
        )


def test_prices_are_exact_decimals_and_serialize_as_numbers():
    pricing = ModelPricing.model_validate(
        {**_TERRA, "reference_model": "gpt-5.6-terra"}, strict=True
    )
    assert pricing.cached_input_per_million == Decimal("0.2")
    assert pricing.summary()["cached_input_per_million"] == 0.2
    assert ModelPricing.model_validate_json(json.dumps(pricing.summary()), strict=True) == pricing


async def _priced_requests(budget: StepBudget, *reports: tuple[str, PricingPlan | None, Any]):
    for model, pricing, tokens in reports:
        ticket = await budget.start_model_request(model, pricing)
        if tokens is not None:
            await budget.finish_model_request(ticket, tokens)


async def test_budget_splits_requests_by_model_and_never_guesses_unknown_usage():
    budget = StepBudget(model_requests=4, tool_calls=0)
    await _priced_requests(
        budget,
        ("gpt-5.6-terra", TERRA, TokenUsage(100_000, 5_000, 40_000, 0, 2_000)),
        ("gpt-5.6-terra", TERRA, TokenUsage(300_000, 10_000, 100_000, 0, 0)),
        ("local-model", None, TokenUsage(10, 5)),
    )
    usage = budget.snapshot()
    assert usage.model_requests == 3
    assert dict(usage.by_model)["gpt-5.6-terra"] == ModelUsage(
        2, TokenUsage(400_000, 15_000, 140_000, 0, 2_000), Cost("USD", Decimal("1.208"))
    )
    assert dict(usage.by_model)["local-model"] == ModelUsage(1, TokenUsage(10, 5))
    # An unpriced model makes the total incomplete; the priced model keeps its amount.
    assert usage.cost == Cost("USD", None)
    projected = thaw_json(usage_value(usage))
    assert projected["cost"] is None and projected["cost_complete"] is False
    assert projected["by_model"] == {
        "gpt-5.6-terra": {
            "requests": 2,
            "input_tokens": 400_000,
            "cached_input_tokens": 140_000,
            "output_tokens": 15_000,
            "reasoning_tokens": 2_000,
            "cost": 1.208,
            "cost_complete": True,
            "currency": "USD",
        },
        "local-model": {
            "requests": 1,
            "input_tokens": 10,
            "cached_input_tokens": None,
            "output_tokens": 5,
            "reasoning_tokens": None,
        },
    }
    Usage.model_validate(projected, strict=True)

    failed = StepBudget(model_requests=2, tool_calls=0)
    await _priced_requests(
        failed,
        ("gpt-5.6-terra", TERRA, TokenUsage(1_000, 10, 0, 0, 0)),
        ("gpt-5.6-terra", TERRA, None),
    )
    measured = failed.snapshot()
    assert dict(measured.by_model)["gpt-5.6-terra"].tokens == TokenUsage()
    assert measured.cost == Cost("USD", None)


async def test_budget_rejects_invalid_model_ids():
    budget = StepBudget(model_requests=1, tool_calls=0)
    for model in ("", " ", "x" * 513):
        with pytest.raises(ServiceError) as error:
            await budget.start_model_request(model)
        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert budget.snapshot() == CoreUsage()


def test_costs_are_rounded_to_six_decimals_only_in_results():
    usage = CoreUsage(
        3,
        0,
        TokenUsage(3, 0, 0, 0, 0),
        (("m", ModelUsage(3, TokenUsage(3, 0, 0), Cost("USD", Decimal("0.0000015")))),),
    )
    projected = thaw_json(usage_value(usage))
    assert projected["cost"] == 0.000002
    assert projected["by_model"]["m"]["cost"] == 0.000002
    reference = CoreUsage(
        1,
        0,
        TokenUsage(1, 0, 0, 0, 0),
        (("m", ModelUsage(1, TokenUsage(1, 0), Cost("USD", Decimal(1), "gpt-5.6-terra"))),),
    )
    projected = thaw_json(usage_value(reference))
    assert projected["reference_model"] == "gpt-5.6-terra"
    assert projected["by_model"]["m"]["reference_model"] == "gpt-5.6-terra"


@pytest.mark.parametrize(
    "document",
    [
        {"by_model": None},
        {"cost": 1.0},
        {"cost": 1.0, "cost_complete": False, "currency": "USD"},
        {"cost": None, "cost_complete": True, "currency": "USD"},
        {"reference_model": "gpt-5.6-terra"},
        {
            "by_model": {
                "m": {
                    "requests": 2,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                }
            }
        },
        {
            "by_model": {
                "m": {
                    "requests": 1,
                    "input_tokens": 1,
                    "cached_input_tokens": 2,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                }
            }
        },
    ],
)
def test_public_usage_rejects_inconsistent_cost_and_model_fields(document):
    base = {
        "model_requests": 1,
        "tool_calls": 0,
        "output_retries": 0,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_input_tokens": None,
        "cache_write_input_tokens": None,
        "reasoning_output_tokens": None,
    }
    with pytest.raises(ValidationError):
        Usage.model_validate({**base, **document}, strict=True)


def _result_schema() -> Draft202012Validator:
    text = files("foliqant").joinpath("schemas/execution-result.schema.json").read_text()
    return Draft202012Validator(json.loads(text))


async def test_collections_sum_items_per_model_at_every_level():
    async def execute(step, inputs, context):
        ticket = await context.budget.start_model_request("gpt-5.6-terra", TERRA)
        await context.budget.finish_model_request(ticket, TokenUsage(100_000, 5_000, 40_000, 0, 0))
        return StepOutcome({"value": inputs["message"]})

    result = await runner(collection_plan(), execute).run(
        envelope([item(), item("two", "zweite", "other")]), identity=Identity()
    )
    public = to_execution_result(result)
    document = public.model_dump(mode="json")
    _result_schema().validate(document)
    total = document["execution"]["usage"]
    assert total["by_model"]["gpt-5.6-terra"]["requests"] == 2
    assert total["cost"] == total["by_model"]["gpt-5.6-terra"]["cost"] == 0.376
    dispatch = document["flows"]["main"]["steps"]["dispatch"]
    assert dispatch["usage"]["by_model"] == total["by_model"]
    assert document["flows"]["main"]["usage"]["cost"] == 0.376
    child = dispatch["result"]["items"][0]
    assert child["usage"]["by_model"]["gpt-5.6-terra"]["requests"] == 1
    assert child["steps"]["step_0"]["usage"]["cost"] == 0.188
    assert ExecutionResult.model_validate(document, strict=True) == public


async def test_repeats_and_retry_flows_are_included_once(tmp_path):
    pricing = _pricing(long_context=None)

    def priced(handler, model):
        async def respond(inputs: FrozenObject, context: StepContext) -> StepOutcome:
            ticket = await context.budget.start_model_request(model, pricing)
            await context.budget.finish_model_request(ticket, TokenUsage(1_000, 100, 0, 0, 0))
            return await handler(inputs, context)

        return respond

    plan = _repeat_plan(tmp_path)
    executor = Handlers(
        lookup=priced(_found_on("LU0000000002"), "lookup-model"),
        correct=priced(_corrector(), "retry-model"),
    )
    result = await WorkflowRunner(
        plan,
        executor=executor,
        validator=WorkflowSchemas(plan),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
    ).run(
        AcceptedEnvelope(payload={"identifier": "LU0000000001"}, metadata={}), identity=Identity()
    )
    document = to_execution_result(result).model_dump(mode="json")
    _result_schema().validate(document)
    lookup = document["flows"]["lookup_fund"]
    assert lookup["attempt_count"] == 2
    # Top-level usage is the last attempt; attempts_usage sums both attempts.
    assert lookup["usage"]["by_model"]["lookup-model"]["requests"] == 1
    assert lookup["attempts_usage"]["by_model"]["lookup-model"]["requests"] == 2
    assert lookup["attempts_usage"]["cost"] == 0.0064
    by_model = document["execution"]["usage"]["by_model"]
    assert {model: row["requests"] for model, row in by_model.items()} == {
        "lookup-model": 2,
        "retry-model": 1,
    }
    assert document["execution"]["usage"]["model_requests"] == 3
    assert document["execution"]["usage"]["cost"] == pytest.approx(0.0096)


def _gpt_binding(usage: RequestUsage, pricing: PricingPlan | None) -> ModelBinding:
    async def respond(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("ok")], usage=usage, model_name="gpt-5.6-terra-2026-08-01"
        )

    return ModelBinding(
        model=FunctionModel(respond, model_name="gpt-5.6-terra"),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
        pricing=pricing,
    )


async def test_function_model_details_reach_usage_cost_and_the_chat_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    labels = TelemetryLabels(models=frozenset({"gpt-5.6-terra"}), providers=frozenset({"function"}))
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    step = _text_step()
    usage = RequestUsage(
        input_tokens=300_000,
        output_tokens=10_000,
        cache_read_tokens=100_000,
        output_reasoning_tokens=4_000,
    )
    budget = StepBudget(model_requests=1, tool_calls=0)
    executor = ModelExecutor(
        {"configured-alias": _gpt_binding(usage, TERRA)},
        WorkflowSchemas(_plan(step)),
        telemetry=ModelTelemetry(provider, NoOpMeterProvider(), labels),
    )
    outcome = await executor.execute(step, _frozen_object({}), _context(step.name, budget=budget))
    assert outcome.result == "ok"
    measured = budget.snapshot()
    assert dict(measured.by_model)["gpt-5.6-terra"] == ModelUsage(
        1, TokenUsage(300_000, 10_000, 100_000, None, 4_000), Cost("USD", Decimal("1.02"))
    )
    (span,) = exporter.get_finished_spans()
    provider.shutdown()
    assert span.name == "chat gpt-5.6-terra"
    assert {
        key: span.attributes[key]
        for key in (
            "gen_ai.operation.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.cache_read.input_tokens",
            "gen_ai.usage.reasoning.output_tokens",
            "foliqant.usage.cost",
        )
    } == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "gpt-5.6-terra",
        "gen_ai.response.model": "gpt-5.6-terra",
        "gen_ai.usage.input_tokens": 300_000,
        "gen_ai.usage.output_tokens": 10_000,
        "gen_ai.usage.cache_read.input_tokens": 100_000,
        "gen_ai.usage.reasoning.output_tokens": 4_000,
        "foliqant.usage.cost": 1.02,
    }


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        # Field: a recognized provider's genai-prices extractor mapped the counts onto
        # `RequestUsage`'s instance attributes directly (see the previous test, which also
        # verifies this reaches the chat span).
        (
            RequestUsage(
                input_tokens=1_000,
                output_tokens=200,
                cache_read_tokens=600,
                output_reasoning_tokens=50,
            ),
            TokenUsage(1_000, 200, 600, None, 50),
        ),
        # Details key: extraction never set the field (e.g. an unrecognized provider, or a
        # mapping genai-prices lacks), but the adapter that produced this `RequestUsage` still
        # left the raw counts in `details`, matching how pydantic_ai's OpenAI adapter mirrors
        # `completion_tokens_details.reasoning_tokens` into `details["reasoning_tokens"]`.
        (
            RequestUsage(
                input_tokens=1_000,
                output_tokens=200,
                details={"cached_tokens": 600, "reasoning_tokens": 50},
            ),
            TokenUsage(1_000, 200, 600, None, 50),
        ),
        # Absent: neither the field nor a known `details` key reports a count, so it stays
        # unknown instead of becoming a false zero.
        (
            RequestUsage(input_tokens=1_000, output_tokens=200),
            TokenUsage(1_000, 200),
        ),
        # Ambiguous zero: a bare `details` zero with no field is indistinguishable from the
        # placeholder pydantic_ai's OpenAI Responses adapter writes for an omitted measurement
        # (see `test_responses_sdk_usage_preserves_reasoning_presence`), so it stays unknown
        # rather than becoming a false zero; a details fallback is only trusted when nonzero.
        (
            RequestUsage(
                input_tokens=1_000,
                output_tokens=200,
                details={"cached_tokens": 0, "reasoning_tokens": 0},
            ),
            TokenUsage(1_000, 200),
        ),
    ],
    ids=["field", "details_key", "absent", "ambiguous_details_zero"],
)
async def test_function_model_reasoning_and_cached_tokens_read_field_then_details(usage, expected):
    step = _text_step()
    budget = StepBudget(model_requests=1, tool_calls=0)
    executor = ModelExecutor(
        {"configured-alias": _gpt_binding(usage, None)},
        WorkflowSchemas(_plan(step)),
        telemetry=ModelTelemetry(
            TracerProvider(shutdown_on_exit=False), NoOpMeterProvider(), TelemetryLabels()
        ),
    )
    outcome = await executor.execute(step, _frozen_object({}), _context(step.name, budget=budget))
    assert outcome.result == "ok"
    measured = budget.snapshot()
    assert dict(measured.by_model)["gpt-5.6-terra"].tokens == expected


async def test_unpriced_model_span_has_no_cost():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    labels = TelemetryLabels(models=frozenset({"gpt-5.6-terra"}), providers=frozenset({"function"}))
    provider.add_span_processor(SafeSpanProcessor(SimpleSpanProcessor(exporter), labels))
    step = _text_step()

    async def respond(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("ok")],
            usage=RequestUsage(input_tokens=5, output_tokens=1),
            model_name="PRIVATE",
        )

    binding = ModelBinding(
        model=FunctionModel(respond, model_name="gpt-5.6-terra"),
        settings=ModelSettings(),
        admission=CapacityLimiter(concurrency=1, queue_limit=0),
        output_mode="native",
    )
    budget = StepBudget(model_requests=1, tool_calls=0)
    await ModelExecutor(
        {"configured-alias": binding},
        WorkflowSchemas(_plan(step)),
        telemetry=ModelTelemetry(provider, NoOpMeterProvider(), labels),
    ).execute(step, _frozen_object({}), _context(step.name, budget=budget))
    (span,) = exporter.get_finished_spans()
    provider.shutdown()
    assert "foliqant.usage.cost" not in span.attributes
    assert "gen_ai.usage.cache_read.input_tokens" not in span.attributes
    assert dict(budget.snapshot().by_model)["gpt-5.6-terra"].cost is None


@pytest.mark.parametrize(
    ("response_model", "exported"),
    [
        ("gpt-5.6-terra", True),
        ("gpt-5.6-terra-2026-08-01", True),
        ("gpt-5.6-terranova", False),
        ("PRIVATE-model", False),
    ],
)
def test_response_model_is_exported_only_as_the_configured_model_or_its_snapshot(
    response_model, exported
):
    delegate = RecordingProcessor()
    labels = TelemetryLabels(models=frozenset({"gpt-5.6-terra"}))
    SafeSpanProcessor(delegate, labels).on_end(
        ReadableSpan(
            name="chat gpt-5.6-terra",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-5.6-terra",
                "gen_ai.response.model": response_model,
                "foliqant.usage.cost": 0.25,
            },
            instrumentation_scope=InstrumentationScope("pydantic-ai"),
        )
    )
    (span,) = delegate.ended
    assert span.attributes.get("gen_ai.response.model") == (response_model if exported else None)
    assert span.attributes["foliqant.usage.cost"] == 0.25


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("openai", "http://127.0.0.1:8000/v1"),
        ("vllm", "https://inference.example/v1"),
        ("azure", "https://resource.openai.azure.com/openai/v1"),
    ],
)
def test_compatible_and_azure_chat_usage_reports_cached_and_reasoning_tokens(provider, url):
    from openai.types import chat
    from openai.types.completion_usage import CompletionUsage
    from pydantic_ai.models.openai import _map_usage

    response = chat.ChatCompletion(
        id="response",
        choices=[],
        created=0,
        model="gpt-5.6-terra",
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=1_000,
            completion_tokens=200,
            total_tokens=1_200,
            prompt_tokens_details={"cached_tokens": 600},
            completion_tokens_details={"reasoning_tokens": 50},
        ),
    )
    tokens = request_token_usage(_map_usage(response, provider, url, "gpt-5.6-terra"))
    assert tokens == TokenUsage(1_000, 200, 600, None, 50)
    omitted = chat.ChatCompletion(
        id="response",
        choices=[],
        created=0,
        model="served",
        object="chat.completion",
        usage=CompletionUsage(prompt_tokens=1_000, completion_tokens=200, total_tokens=1_200),
    )
    # A server that omits the details leaves cached and reasoning tokens unknown.
    assert request_token_usage(_map_usage(omitted, provider, url, "served")) == TokenUsage(
        1_000, 200
    )


def test_azure_responses_usage_reports_cached_and_reasoning_tokens():
    from openai.types import responses
    from pydantic_ai.models.openai import _map_usage

    response = responses.Response.model_construct(
        model="gpt-5.6-terra",
        usage=responses.ResponseUsage(
            input_tokens=1_000,
            output_tokens=200,
            total_tokens=1_200,
            input_tokens_details={"cached_tokens": 600, "cache_write_tokens": 0},
            output_tokens_details={"reasoning_tokens": 50},
        ),
    )
    usage = _map_usage(response, "azure", "https://resource.openai.azure.com/openai/v1", "m")
    assert request_token_usage(usage) == TokenUsage(1_000, 200, 600, 0, 50)


def _profile(**changes: Any) -> CompatibleModelConfig:
    return CompatibleModelConfig.model_validate(
        {
            "provider": "openai_compatible",
            "model": "gpt-5.6-terra",
            "base_url": "https://inference.example/v1",
            "output_mode": "native",
            "pricing": _TERRA,
            **changes,
        },
        strict=True,
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"profile": "terra", "options": {"max_tokens": 10}}, TERRA),
        ({"profile": "terra", "model": "gpt-5.6-terra"}, TERRA),
        ({"profile": "terra", "model": "gpt-5.6-mini"}, None),
        ({"profile": "terra", "pricing": None}, None),
        (
            {
                "profile": "terra",
                "model": "gpt-5.6-mini",
                "pricing": {**_TERRA, "reference_model": "gpt-5.6-terra"},
            },
            _pricing(reference_model="gpt-5.6-terra"),
        ),
    ],
)
def test_profile_overrides_inherit_replace_or_clear_pricing(override, expected):
    registry = ModelRegistry({"terra": _profile()})
    alias = registry.select(
        ModelProfileOverride.model_validate(override, strict=True),
        default=None,
        aliases={"terra": "gpt-5.6-terra"},
        workflow="w",
        flow="f",
        step="s",
        location=SourceLocation("steps/s.yaml", 1, 1),
    )
    effective = registry.profiles[alias].pricing
    assert (effective.plan() if effective is not None else None) == expected


def test_explain_and_doctor_show_configured_pricing(tmp_path: Path):
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "config/settings.yaml"
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    settings["models"]["local"]["pricing"] = {**_TERRA, "reference_model": "gpt-5.6-terra"}
    config.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    expected = {
        "currency": "USD",
        "input_per_million": 2.0,
        "cached_input_per_million": 0.2,
        "output_per_million": 12.0,
        "reasoning_billed_as": "output",
        "long_context": {
            "threshold_input_tokens": 272_000,
            "input_per_million": 4.0,
            "cached_input_per_million": 0.4,
            "output_per_million": 18.0,
        },
        "reference_model": "gpt-5.6-terra",
    }

    explained = _cli("explain", "--config", str(config), "--workflow", "demo")
    assert explained.returncode == 0, explained.stderr
    step = json.loads(explained.stdout)["workflows"][0]["flows"][0]["steps"][0]
    assert step["model_selection"]["pricing"] == expected

    diagnosed = _cli("doctor", "--config", str(config))
    assert diagnosed.returncode == 0, diagnosed.stderr
    local = json.loads(diagnosed.stdout)["models"]["local"]
    assert local["provider"] == "openai_compatible"
    assert local["pricing"] == expected

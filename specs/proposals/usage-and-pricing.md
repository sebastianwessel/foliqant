# Readable traces, usage by model and cost estimation

## Goal

An operator can read a trace without looking up attributes, see which model a run
used and how many tokens each consumed, and estimate what a run cost from prices
the deployment reviewed, without the runtime guessing unreported counts or
shipping a price list.

## Span names

A trace used to show only `foliqant.workflow`, `foliqant.flow` and `foliqant.step`,
so every flow and step looked the same. Spans are now named by what they are:

| Span | Name |
|---|---|
| run | `workflow <workflow id>` |
| flow run | `flow <flow id>`; ` [item <index>]` for a callable flow in a collection; ` #<n>` for attempt 2 onwards (repeats and later retry-flow runs) |
| step | `step <step id> (<type>)`, the type being `decision`, `llm`, `mcp`, `handler` or `flow_collection` |
| model request | `chat <model>` (OTel GenAI) |
| tool call | `execute_tool <tool>` (OTel GenAI) |

Names contain configuration IDs and fixed words only, so they stay
low-cardinality. The collection index (bounded by `max_items`) and the attempt
number (bounded by `max_attempts`) are the only numbers; item IDs are business
data and never appear. The first attempt has no suffix, so the common case reads
like the configuration. A name that is not an allowlisted label is left out
(`step (llm)`), never replaced by a runtime value.

The instrumentation scopes and every `foliqant.*` attribute stay, so queries by
attribute keep working. A new `foliqant.step.kind` attribute carries the step
type. The name is computed once from the sanitized attributes by the same
function at span creation and on export, so a host-owned provider sees the same
names as the runtime exporter.

Model spans follow the OTel GenAI conventions: `gen_ai.operation.name`,
`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens` and, when the provider reports them,
`gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`
and `gen_ai.usage.reasoning.output_tokens`. PydanticAI emits reasoning tokens
only under a `details` key and omits zero counts, so the runtime annotates the
active `chat` span with the reported counts (including zero) from inside the
instrumented request, before the span closes. `foliqant.usage.cost` is the
request's estimate when pricing applies.

## Usage by model

Totals alone cannot answer "which model spent the tokens" once a workflow mixes a
small extraction model with a large decision model. Every usage object gains
`by_model`, keyed by the provider model ID the request was sent to (the
profile's resolved `model`), with `requests`, `input_tokens`,
`cached_input_tokens`, `output_tokens` and `reasoning_tokens`. The split is
recorded where the attempt is reserved: `start_model_request(model, pricing)` on
the step budget, so a failed or unreported attempt still counts as a request of
that model with unknown tokens. The existing totals are unchanged and the split
aggregates by the same rules: step → flow → collection → attempts → run, each
request counted once. `by_model` is omitted when no model request was made.

The provider model ID is the key rather than the profile name because two
profiles (or an override) may send requests to the same model, and cost is a
property of the model.

## Pricing

A profile may declare `pricing`:

```yaml
pricing:
  currency: USD
  input_per_million: 2.00
  cached_input_per_million: 0.20
  output_per_million: 12.00
  reasoning_billed_as: output
  long_context:
    threshold_input_tokens: 272000
    input_per_million: 4.00
    cached_input_per_million: 0.40
    output_per_million: 18.00
  reference_model: gpt-5.6-terra
```

The per-request estimate is uncached input × input price + cached input × cached
price (input price when absent) + output × output price. Output tokens include
reasoning tokens as reported; `reasoning_billed_as: input` moves that subset to
the input price. The long-context tier replaces all prices of a request whose
input tokens exceed the threshold, which is how tiered provider prices apply.

Decisions:

* **Prices are configuration.** Prices change and contracts differ, so the
  runtime ships no price table and never looks prices up (PydanticAI's own
  `genai-prices` estimate is not exported). `reference_model` records that a
  model is estimated with another model's prices, for example a local model
  compared with a hosted one.
* **Never guessed.** A request whose needed count is unknown has no amount; a
  count is needed only when the formula uses it (cached tokens when a cached
  price is set, reasoning tokens when billed as input). A total mixing priced
  and unpriced requests, or an unknown request, is `cost: null` with
  `cost_complete: false`. The currency is still reported so the field is typed.
* **Exact arithmetic.** Prices are read as `Decimal` from the YAML number as
  written (`0.2` is exactly `0.2`), request costs are summed unrounded, and
  results round once to six decimals (banker's rounding). JSON output uses
  numbers.
* **Strict validation.** Only `USD`; prices are numbers from 0 to 1,000,000;
  strings, booleans, infinities and unknown keys are rejected offline.
* **Overrides.** An override's `pricing` replaces the profile's and `null`
  removes it. An override that changes `model` does not inherit the profile's
  pricing: those prices describe the profile's own model.
* **Visibility.** `explain` shows each step's effective pricing under
  `model_selection.pricing`; `doctor` lists each profile's provider, model and
  pricing, so a reviewer sees the prices without running anything.

## Provider usage details

PydanticAI (via `genai-prices` extractors) maps OpenAI chat
`prompt_tokens_details.cached_tokens` and `completion_tokens_details.reasoning_tokens`,
and the Responses equivalents, to `cache_read_tokens` and `output_reasoning_tokens`
for `openai`, OpenAI-compatible (any base URL, falling back to the OpenAI
extractor) and `azure`. `RequestUsage.__init__` only ever `setattr`s the keys it
receives, so `request_token_usage` reads `vars(usage)` and a name that was
never reported (including `cache_read_tokens`/`cache_write_tokens`, which the
dataclass declares with a default of `0`) simply stays absent from it. pydantic-ai
2.46.0 declares no first-class field at all for reasoning tokens; `RequestUsage.extract`
only sets `output_reasoning_tokens` as an instance attribute when the provider was
recognized and mapped. When extraction doesn't map a count, `request_token_usage`
falls back to the same `details` key the adapter itself set from the raw response
(`details["reasoning_tokens"]`, `details["cached_tokens"]`) before giving up and
reporting `None`; the fallback is only consulted when the field is absent, so it
never overrides a genuinely reported zero. The fallback also only trusts a
*nonzero* `details` value: pydantic_ai's OpenAI Responses adapter writes
`details["reasoning_tokens"] = 0` both for a genuine zero (already resolved by
the declared-field check) and for an omitted measurement, so a bare zero found
only in `details` is indistinguishable from that placeholder and stays unknown.
Tests pin this for chat and Responses payloads and for `FunctionModel` usage
covering the field, `details`-key, fully-absent, and ambiguous-zero cases.

## Implementation notes

* `core/pricing.py` holds the standard-library `PricingPlan`; `core/execution.py`
  adds `Cost` and `ModelUsage` and the combination rules. Contracts convert the
  YAML block with `ModelPricing.plan()`; bootstrap attaches the plan to every
  model binding, so custom model factories get pricing too.
* Public `Usage` and `ModelUsage` share the cost fields, which are present
  together or not at all; when present, `by_model` requests sum to
  `model_requests`. Serialization places counts first, then the estimate, then
  `by_model`.
* `ExecutionInfo`, `Usage`, and `ModelUsage` (`foliqant.contracts.execution`) are
  re-exported from the top-level `foliqant` package alongside `ExecutionResult`,
  so a caller can import the whole result surface from one place.
* `gen_ai.response.model` is exported when it equals a configured model or is a
  dated snapshot of one (`<model>-…`); any other provider-reported value is
  dropped.

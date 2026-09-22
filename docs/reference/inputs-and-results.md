# Input and result contracts

This is an exact-field lookup. Start with [run from Python](../integration/index.md),
[read results](../integration/results.md), or [handle errors](../integration/errors.md)
for a task-oriented explanation. Python boundary models and packaged JSON Schemas
are the authority for precise validation.

## Caller input: `Envelope`

Pass an `Envelope` to `WorkflowApplication.run`, `run_flow`, or `run_step`:

```json
{
  "payload": {"message": "Please send my statement."},
  "metadata": {"locale": "en"}
}
```

`payload` is required and may be any finite JSON value. `metadata` defaults to
`{}` and may contain application-defined JSON fields. Protected fields are:

| Field | Contract |
| --- | --- |
| `tenant_id` | Optional nonblank identity context, not authentication. |
| `principal_id` | Optional nonblank identity context, not authentication. |
| `telemetry` | Optional closed object with `traceparent` and/or `tracestate`, each at most 512 characters. |

Omit protected fields when absent; explicit `null` is invalid. If the host passes
a trusted `Identity`, supplied identity metadata must match it and missing fields
can be filled from it. Foliqant checks consistency but does not authenticate a
caller. The selected workflow's input schema validates `payload` at admission.

For untrusted bytes, use `decode_envelope(raw_request_body)` from
`foliqant.contracts.decoding`. It accepts at most 1 MiB by default and rejects
malformed UTF-8, duplicate object keys, non-finite numbers, excessive nesting,
and invalid envelope fields. A transport must also bound its read before buffering
the body. See the [HTTP guide](../integration/http.md).

`run_flow` expects that flow's already resolved input in `payload`; `run_step`
expects the selected step's resolved binding names. Neither reconstructs upstream
history. Use `run` for a normal workflow invocation.

## Native decision input: `DecisionInput`

A configured decision step builds a native input from its sources and questions.
This is the decision adapter's input, not the caller's envelope. The closed
object has `state.sources` and `questions`. Every source has a unique `id`, a
`kind` (`message`, `document`, `table`, `policy`, or `metadata`), and nonempty
`text`. Every question has a unique `id`, `type`, nonempty `prompt`, nonempty
unique `criteria`, and nonempty `allowedSourceIds` naming supplied sources.

| Question type | Additional required fields | Result `answer` |
| --- | --- | --- |
| `choice` | At least two uniquely identified `options` | `{"optionId": "id"}` or `null` |
| `multiselect` | Nonempty `options`, `minSelections`, `maxSelections` | `{"optionIds": ["id"]}` or `null` |
| `predicate` | None | `{"value": "true"}`, `{"value": "false"}`, or `{"value": "unknown"}` |
| `ordinal` | At least two uniquely identified `levels` | `{"levelId": "id"}` or `null` |
| `request_units` | `catalog` and `allowNoMatch` | `{"units": [...], "relations": [...]}` or `null` |

For multiselect, `minSelections` cannot exceed `maxSelections` or the option
count; selected IDs are unique. Configured decision steps emit `document`
sources: JSON-format values are serialized once with stable key ordering, and
text-format values must be nonblank. The public `DecisionInput` contract also
permits other source kinds for direct Python construction. See [decision step
configuration](../steps/decision.md).

## Native decision output

The adapter validates one closed `{"results": [...]}` object with exactly one
typed result per input question. Missing, duplicate, unknown, or type-mismatched
question results are rejected. Validated results are ordered like input questions.
Every result requires:

| Field | Contract |
| --- | --- |
| `questionId`, `type` | Matching input question and type. |
| `answerability` | A `status` and an `issues` array. |
| `answer` | Type-specific shape above, including `null` where permitted. |
| `reason` | Nonblank human explanation, at most 400 characters; never truncated. |
| `evidence_strength` | `"strong"`, `"limited"`, or `null`; always present. |

### Reason and evidence strength

`evidence_strength` rates support for the whole assessment, including abstention.
`strong` can accompany a justified `not_answerable` or `undetermined` result;
`limited` is weaker but permissible support and never licenses an invented fact.
`null` means strength was not assessed. It is not model confidence or a
correctness probability and does not change routing by itself. `reason` is for
people; code uses status, issues, and typed answers. See [decision
policy](../guides/decision-contracts.md).

### Answerability and issues

| Answerability status | Allowed answer |
| --- | --- |
| `answerable` | Substantive answer; a permitted empty collection can be substantive. |
| `partially_answerable` | Nonempty supported subset for `multiselect` or `request_units`, with unresolved remainder. |
| `not_answerable` | `null`, except predicate `{"value": "unknown"}`; at least one issue. |
| `undetermined` | Same answer-presence rule as `not_answerable`; at least one issue. |

The exact issue codes are `no_supported_answer` (missing or unsupported required
answer), `conflicting_information` (unresolved material conflict), and
`multiple_valid_options` (positively supported choices exceed permitted
cardinality). Issues are unique and there can be at most three. An answerable
`request_units` result with `allowNoMatch: true` may still contain
`no_supported_answer` for an uncategorized unit.

A request unit has a response-local unique `id`, a `status` (`active`,
`withdrawn`, `conditional`, or `quoted`), nullable `categoryId` and `subject`,
and nonempty `description`. A non-null subject must appear verbatim in an allowed
source. `categoryId: null` requires `no_supported_answer` and is rejected when
`allowNoMatch` is false. Relations can be `conditional_on`, `requires`,
`precedes`, or `mutually_exclusive`; references must be valid, without duplicate,
self, cyclic, or contradictory relations. Request units have no citation arrays.
See [multiple requests](../integration/multiple-requests.md).

A step authored with singular `question` exposes its one native result object as
`StepResult.result`. A step authored with plural `questions` (2–64) exposes
`{"results": [...]}`. A multi-question step completes only when every question
is answerable. Only a singular choice, ordinal, or predicate supplies a direct
route key.

## `ExecutionResult`

Every invocation result has five top-level fields:

| Field | Contract |
| --- | --- |
| `payload` | Workflow output projection; defaults to accepted input without an output binding. |
| `metadata` | Accepted metadata. |
| `flows` | Map by flow instance ID; each has `status`, `steps`, and optional projected `result`. |
| `transitions` | Selected flow boundaries and terminal outcomes. |
| `execution` | `id`, `workflow`, `revision`, `status`, `usage`, and error when failed or cancelled. |

A flow result defaults to that flow's bound input without an output projection.
A completed flow or step always has `result`, even when its value is JSON `null`.
Failed, cancelled, and skipped records omit it. Review records may contain a
result. Flow and step statuses are `completed`, `needs_review`, `failed`,
`cancelled`, or `skipped`; root execution has no `skipped`. Failed records
require a safe `error`. A skipped record has no result, error, elapsed time,
or usage.

Step results are scoped under `/flows/{flow}/steps/{step}/result` and flow
projections under `/flows/{flow}/result`. There is no flat decision map. Workflow
routes and output bindings can use the original `/payload`, `/metadata`, and
prior `/flows/{flow}/result`, not another flow's internal step ledger. Each
transition records its source and `completed` or `needs_review` reason, with
exactly one configured next `flow` or terminal `outcome`. Technical failure
selects no unresolved transition.

Every included usage object has `model_requests`, `tool_calls`, `input_tokens`,
`output_tokens`, `cache_read_input_tokens`, `cache_write_input_tokens`, and
`reasoning_output_tokens`. Counts are nonnegative; token values may be `null`
when unavailable. Parent usage already aggregates child work, so do not add
levels together.

Only a singular choice decision can have `selection`. An answerable model choice
has `origin: "model"`, matches native `answer.optionId`, and completes. An
authored fallback has `origin: "fallback"`, leaves the native answer `null`, and
keeps the step in review. A collection step has `kind: "flow_collection"` and an
ordered `result.items` ledger when complete or in review; on failure it has
`partial_result.items` instead. Each item adds `id` and `flow` to a child flow
record. See [collection configuration](../steps/flow-collection.md).

## Errors, CLI, and presence

Review is valid output and has no `execution.error`. A technical failure may
return a result with completed work preserved, while invalid admission can raise
`ServiceError` before a result is available. Caller cancellation propagates.
`SafeError` contains a canonical `code`, fixed safe `message`, required
`retryable`, and optional nonblank `location`; see the [complete code
table](../integration/errors.md#canonical-error-codes). Codes do not impose HTTP
statuses. `foliqant run` prints completed and review results on stdout with exit
code `0`; failed and cancelled results use its safe CLI error on stderr. CLI exit
codes are `1` for evaluation mismatch, `2` for invalid argument/input/config,
`3` for missing optional dependency, `4` for runtime failure, and `130` for
interruption.

Unknown fields are rejected except application-defined envelope metadata.
Protected metadata, errors, selections, category descriptions, collection
`kind`, and `partial_result` are omitted when absent, not serialized as null.
Native nullable answers and unknown token measurements use explicit null.
Record-level unavailable timing or usage is omitted in canonical serialization.

Packaged schemas are `envelope.schema.json`, `decision-input.schema.json`,
`decision-output.schema.json`, and `execution-result.schema.json` under
`foliqant/schemas`. Python callers import `Envelope` and `ExecutionResult` from
`foliqant`, `DecisionInput` from `foliqant.decisions`, and `DecisionOutput` and
`validate_decision_output` from `foliqant.contracts.decisions`. For trusted
saved JSON, use each model's `model_validate_json(..., strict=True)`, then call
`validate_decision_output(input, output)` to check cross-object IDs, permitted
answers, and source references. Structural validation does not prove an answer
or reason is factually correct.

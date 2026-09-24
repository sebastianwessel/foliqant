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

## Which call creates which result

All three public execution methods return the same Python type:
`ExecutionResult`. They differ in the amount of configured work they execute and
therefore in the records that result contains.

| Call | Use it when | What Foliqant executes | `ExecutionResult.payload` | `flows` and `transitions` |
| --- | --- | --- | --- | --- |
| `await app.run(workflow, envelope)` | Normal application request | The workflow start, routed flows, repeat attempts and retry flows, and configured output projection | The workflow output projection, or accepted input when no output is configured; a failed run preserves accepted input | Records every non-callable workflow flow and every retry flow; unvisited flows are `skipped`. Records the selected routing boundaries. |
| `await app.run_flow(workflow, flow_id, envelope)` | Focused flow test or evaluation | One named flow (one attempt of a repeated flow), with the already resolved flow input | That flow's projected result, or accepted input if it fails | Contains only the named flow. Does not follow its transition, so `transitions` is empty. |
| `await app.run_step(workflow, flow_id, step_id, envelope)` | Focused step test or evaluation | One named step, with exactly its resolved input names | That step's validated result, or accepted input if it fails | Contains only the named flow and step. It does not run preceding steps or routes, so `transitions` is empty. |

`run_flow` and `run_step` still enforce the selected flow or step's input
schema, output schema, provider limits, tool authorization, and result
validation. They are execution tools, not ways to bypass policy.

```python
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application


async def classify_one_email() -> None:
    prepared = prepare_application(Path("config/settings.yaml"))
    async with open_application(prepared, environment={}) as app:
        # Production path: run the configured workflow graph.
        whole_run = await app.run(
            "support_email",
            Envelope(payload={"message": "Please cancel my renewal."}),
        )

        # Focused test path: the payload keys are the step's resolved input names.
        isolated_step = await app.run_step(
            "support_email",
            "classify",
            "classify",
            Envelope(payload={"message": "Please cancel my renewal."}),
        )

    assert whole_run.execution.status in {"completed", "needs_review"}
    decision = isolated_step.flows["classify"].steps["classify"].result
    print(decision)
```

The code uses the Pydantic boundary objects directly. Use
`result.model_dump(mode="json")` when returning a JSON response or saving a
reviewed report.

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

## `ExecutionResult` and nested records

Every public execution call produces one `ExecutionResult`. Its records are
nested by the boundary that produced them; Foliqant never creates a separate
flat decision or tool-result map.

```text
ExecutionResult                    ← app.run(), app.run_flow(), app.run_step()
├── payload                         ← public workflow / focused-boundary output
├── metadata                        ← accepted caller metadata
├── execution                       ← run-wide status, usage, and safe failure
├── start                           ← only full workflow runs: first flow and its selection
├── transitions[]                   ← only full workflow routing
└── flows[flow_id] : FlowResult      ← executed or skipped workflow flow
    ├── result                       ← flow output projection
    ├── steps[step_id] : StepResult  ← operation result
    │   ├── result                   ← decision, text, JSON, handler, or MCP value
    │   └── result.items[]           ← only for a completed/reviewed collection
    │       └── FlowCollectionItemResult
    └── error                        ← only when that flow failed or was cancelled
```

### Who produces each public shape?

| Public type | Produced by | Where you receive it | When it exists |
| --- | --- | --- | --- |
| `ExecutionResult` | `WorkflowApplication.run`, `run_flow`, or `run_step` | The awaited return value | A call was admitted and reached execution. Invalid admission can instead raise `ServiceError`; caller cancellation propagates. |
| `FlowResult` | The runtime after a configured flow runs, fails, reviews, or is skipped | `result.flows[flow_id]` | Every non-callable flow and every repeat retry flow in a whole workflow run, or the selected flow for `run_flow` / `run_step`. |
| `StepResult` | The runtime after a configured step runs, fails, reviews, or is skipped | `result.flows[flow_id].steps[step_id]` | Every step belonging to a flow record. `run_step` executes only the selected step. |
| `FlowCollectionItemResult` | A `flow_collection` step after each callable child flow | `step.result.items[index]` or `step.partial_result.items[index]` | Only a flow-collection step. It is a flow record plus the planned child `id` and `flow`. |
| `SafeError` | The failing runtime boundary | `execution.error`, `flow.error`, or `step.error` | A returned technical failure. It is absent for `needs_review`. |
| `Usage` | The runtime's measured accounting | `execution.usage`; optionally a flow or step record | A run always has root usage. Flow and step usage is omitted when unavailable or skipped. |

`ExecutionResult`, `FlowResult`, `StepResult`, and `SafeError` are strict
Pydantic boundary models. Internally, `to_execution_result(...)` materializes
the first three from immutable runtime records immediately before a public call
returns. Your application normally reads them rather than constructing them.
The exception is a local fake for an evaluation or integration test; see
[unit testing](../evaluation/unit-testing.md).

### Top-level fields

| Field | Type | Created from | How an application should use it |
| --- | --- | --- | --- |
| `payload` | Any JSON value | Workflow output binding for `run`; selected flow or step result for scoped calls | Your primary business result after checking `execution.status`. It defaults to accepted input for a whole run without an output binding. |
| `metadata` | `Metadata` | Accepted envelope metadata, including trusted identity consistency checks | Correlate a result with non-secret caller context. Do not treat it as authentication proof. |
| `flows` | Map of `FlowResult` | Configured flow execution records | Inspect detailed outcomes, decision evidence, tool results, and skipped branches. |
| `start` | `StartResult`, omitted for scoped calls | The first flow of `run`: `flow` and `route` (`kind` of `direct` or `route`, optional `index` of the routed `start` entry) | See which start candidate a routed `start` selected. It agrees with the workflow span's `route.selected` event. |
| `transitions` | List of `TransitionResult` | Authored route selected after each full-flow boundary: `source`, `reason`, one of `flow`/`outcome`, and `route` (`kind` of `direct`, `cases`, `route` or `review`, optional `index` and `case`) | Explain why `run` visited a flow or finished. Scoped calls do not route, so this is empty. |
| `execution` | `ExecutionInfo` | The complete invocation | Read this first for the overall status, ID, revision, measured usage, and safe technical error. With telemetry, `trace` holds the run span's `trace_id` and `span_id`. |

### Flow fields: `result.flows[flow_id]`

| Field | Present when | Meaning |
| --- | --- | --- |
| `status` | Always | `completed`, `needs_review`, `failed`, `cancelled`, or `skipped`. |
| `steps` | Always | Map of the flow's configured steps to `StepResult` records. |
| `result` | Completed; may be present for review | The flow's configured output projection. Without one, it is the resolved flow input. It may be explicit JSON `null`. |
| `usage`, `elapsed_seconds` | When measured | Local measurements of the (last) run. Root usage already includes this work; do not add both levels. |
| `error` | Failed or cancelled | A safe technical failure. It is never present for a review outcome. |
| `attempt_count` | Always | Number of executions: `0` when skipped, otherwise `1`, or the number of attempts or retry runs. |
| `attempts` | A repeated flow or a retry flow ran | One `FlowAttempt` per run with `attempt` (1-based), `status`, `steps`, optional `result`, `usage`, `elapsed_seconds`, `error`. The top-level fields repeat the last run. |
| `attempts_usage`, `attempts_elapsed_seconds` | With `attempts` | Sums over every run. |
| `repeat` | A repeated flow ran | `stopped_by`: `until`, `exhausted`, `continue_when`, `review` or `failure`. |

### Step fields: `result.flows[flow_id].steps[step_id]`

| Field | Present when | Meaning |
| --- | --- | --- |
| `status` | Always | `completed`, `needs_review`, `failed`, `cancelled`, or `skipped`. |
| `result` | Completed; may be present for review | The validated operation value: a native decision, schema object, text, handler result, direct MCP result, or collection ledger. It may be JSON `null`. |
| `selection` | A singular choice decision selected a model or authored fallback category | `category.id` is the effective category for routing; `origin` says whether it came from the model or fallback. The native decision remains in `result`. |
| `usage`, `elapsed_seconds` | When measured | Step-local model/tool usage and duration. Do not add it to root or flow totals. |
| `error` | Failed or cancelled | A safe technical failure. |
| `kind`, `partial_result` | A failed `flow_collection` | `kind` is `flow_collection`; `partial_result.items` retains the ordered child ledger completed before the technical failure. |

### Status and field presence

| Record status | `result` | `error` | Typical interpretation |
| --- | --- | --- | --- |
| `completed` | Required; may be JSON `null` | Absent | The operation or flow produced its validated result. |
| `needs_review` | Optional | Absent | A valid business outcome needs a person or an authored follow-up policy. |
| `failed` | Absent | Required | A technical failure stopped this boundary. Completed upstream records remain available. |
| `cancelled` | Absent | May be present in a serialized record | No successful result is claimed. A caller cancellation normally propagates instead of returning an `ExecutionResult`. |
| `skipped` | Absent | Absent | The workflow took another route, the flow stopped before the step, or the step's `when` condition was false. Skipped records have no timing or usage. |

Root `execution.status` is `completed`, `needs_review`, `failed`, or
`cancelled`; it is never `skipped`. A technical failure does not create a route
transition. Workflow routes and output bindings can use the original `/payload`,
`/metadata`, and an earlier `/flows/{flow}/result`, but not another flow's
private step records.

### Special nested result values

| Step kind | `StepResult.result` shape | Related fields |
| --- | --- | --- |
| Singular `decision` | One native decision result | A singular answerable choice can add `selection`. |
| Multi-question `decision` | `{"results": [...]}` in authored question order | No direct selection; inspect each native result. |
| `llm` with schema output | Value validated against the step's JSON Schema | Tool-loop steps can also report model and tool usage. |
| `llm` with text output | String | Text is validated only as the configured text result. |
| `handler` or direct `mcp` | Value validated against the registered or catalog schema | Direct MCP calls are still nested at their step. |
| `flow_collection` | `{"items": [FlowCollectionItemResult, ...]}` | On technical failure the same ledger is instead `partial_result`. |

Every included `Usage` object has `model_requests`, `tool_calls`,
`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_write_input_tokens`, and `reasoning_output_tokens`. Counts are
nonnegative; token values may be `null` when a provider did not report them.
Parent usage already aggregates child work, so do not add levels together.

See [collection configuration](../steps/flow-collection.md) for its child-ledger
semantics and [read a workflow result](../integration/results.md) for the
minimal application-facing path.

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

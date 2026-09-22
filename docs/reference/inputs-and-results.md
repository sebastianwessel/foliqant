# Inputs, results, and errors

Foliqant has two different input contracts:

- An application caller passes an `Envelope` to `WorkflowApplication.run`,
  `run_flow`, or `run_step`.
- A configured decision step builds a native `DecisionInput` from its resolved
  sources and questions. This is the value sent across the decision adapter
  boundary; it is not the argument to `application.run`.

One foreground invocation returns one `ExecutionResult`. This page describes
those public values and the native decision result stored inside a decision
step record.

Jump to [reason and evidence strength](#reason-and-evidence-strength),
[answerability and issues](#answerability-and-issue-codes), or
[operational errors](#semantic-review-and-operational-failure-are-separate).

All contract objects are strict and reject unknown fields unless this page says
otherwise. Examples marked **complete** contain every field required for that
object. Examples marked **fragment** need the surrounding object shown in their
heading.

## Caller input: `Envelope`

An envelope has a required `payload` and optional `metadata`:

```json
{
  "payload": {
    "message": "Freeze card-123 and send the statement for account-9."
  },
  "metadata": {
    "tenant_id": "tenant-7",
    "principal_id": "user-42",
    "locale": "en"
  }
}
```

This is a **complete envelope**. `payload` may be any finite JSON value, not
only an object. Omitted `metadata` defaults to `{}`. Metadata may contain
application-defined JSON fields such as `locale`; the protected fields are:

| Field | Contract |
| --- | --- |
| `tenant_id` | Optional nonblank identity context. It does not authenticate the caller. |
| `principal_id` | Optional nonblank identity context. It does not authenticate the caller. |
| `telemetry` | Optional closed object containing `traceparent` and/or `tracestate`, each a string of at most 512 characters. |

Protected fields must be omitted when absent, rather than set to `null`. Without
an explicit host `Identity`, Foliqant derives invocation context from the
validated envelope fields. If the host passes an explicit `Identity`, matching
envelope values are accepted and absent values are filled from it; a mismatch
is `forbidden`. This is consistency checking, not authentication. The host owns
authentication and authorization.

For untrusted bytes, use the bounded decoder rather than direct Pydantic
validation:

```python
from foliqant.contracts.decoding import decode_envelope

envelope = decode_envelope(raw_request_body)
```

`decode_envelope` accepts at most 1 MiB by default and rejects malformed UTF-8,
duplicate object keys, non-finite numbers, excessive nesting, and values outside
the envelope contract. Admission then validates `payload` against the selected
workflow's input schema.

`run_flow` expects the selected flow's already resolved input in
`envelope.payload`. `run_step` similarly expects the selected step's resolved
binding names in `envelope.payload`; it does not reconstruct upstream flow
history.

## Native decision input: `DecisionInput`

A decision step resolves its configured sources and questions into this closed
object:

```json
{
  "state": {
    "sources": [
      {
        "id": "message",
        "kind": "document",
        "text": "Freeze card-123 and send the statement for account-9."
      }
    ]
  },
  "questions": [
    {
      "id": "request_kind",
      "type": "choice",
      "prompt": "Which single category best describes the request?",
      "criteria": ["Use only the supplied message."],
      "allowedSourceIds": ["message"],
      "options": [
        {"id": "freeze_card", "description": "A request to freeze a card."},
        {"id": "send_statement", "description": "A request for a statement."}
      ]
    },
    {
      "id": "request_labels",
      "type": "multiselect",
      "prompt": "Which request labels apply?",
      "criteria": ["Select every supported label."],
      "allowedSourceIds": ["message"],
      "options": [
        {"id": "freeze_card", "description": "A request to freeze a card."},
        {"id": "send_statement", "description": "A request for a statement."}
      ],
      "minSelections": 0,
      "maxSelections": 2
    },
    {
      "id": "has_card_request",
      "type": "predicate",
      "prompt": "Does the message ask to freeze a card?",
      "criteria": ["True requires an explicit or necessarily implied request."],
      "allowedSourceIds": ["message"]
    },
    {
      "id": "urgency",
      "type": "ordinal",
      "prompt": "How urgent is the request?",
      "criteria": ["Assign a level only when timing is explicitly stated."],
      "allowedSourceIds": ["message"],
      "levels": [
        {"id": "routine", "description": "Explicitly routine or non-urgent timing."},
        {"id": "urgent", "description": "Explicit near-term action is required."}
      ]
    },
    {
      "id": "requests",
      "type": "request_units",
      "prompt": "Identify each active request.",
      "criteria": ["Keep distinct requested actions as distinct units."],
      "allowedSourceIds": ["message"],
      "catalog": [
        {"id": "freeze_card", "description": "Freeze a card."},
        {"id": "send_statement", "description": "Send a statement."}
      ],
      "allowNoMatch": false
    }
  ]
}
```

This is a **complete native decision input** demonstrating all five question
types. In normal workflow execution, Foliqant builds it from the compiled step;
callers do not supply it as the envelope.

Every source requires a unique `id`, a `kind` from `message`, `document`,
`table`, `policy`, or `metadata`, and nonempty `text`. Question IDs must be unique, and every
`allowedSourceIds` entry must name a source in `state.sources`.

Every question shares these required fields:

| Field | Contract |
| --- | --- |
| `id` | Stable ID: 1 to 128 characters matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. |
| `type` | One of the five types below. |
| `prompt` | Nonempty question text. |
| `criteria` | A nonempty list of unique, nonempty rules. |
| `allowedSourceIds` | A nonempty list of unique source IDs. |

The type-specific fields and answer shapes are:

| Question type | Required question fields | Native `answer` |
| --- | --- | --- |
| `choice` | At least two uniquely identified `options` | `{"optionId": "freeze_card"}` or `null` |
| `multiselect` | Nonempty `options`, `minSelections`, and `maxSelections` | `{"optionIds": ["freeze_card", "send_statement"]}` or `null` |
| `predicate` | No additional field | Always an object: `{"value": "true"}`, `{"value": "false"}`, or `{"value": "unknown"}` |
| `ordinal` | At least two uniquely identified `levels` | `{"levelId": "urgent"}` or `null` |
| `request_units` | A `catalog` and required `allowNoMatch` boolean | `{"units": [...], "relations": [...]}` or `null` |

For a multiselect, `minSelections` cannot exceed `maxSelections`, and
`maxSelections` cannot exceed the number of options. Selected option IDs are
unique and must meet the configured cardinality. Choice, multiselect, ordinal,
and request-unit answers use only IDs from their corresponding question.

Configured decision steps emit native `document` sources. A JSON-format source
is serialized once with stable key ordering; a text-format source must resolve
to nonblank text. The public `DecisionInput` contract also permits `message`,
`table`, `policy`, and `metadata` when an application constructs it directly.

## Native decision output

The model-facing contract is always a closed `DecisionOutput` with a `results`
array. It contains exactly one result for each input question. Foliqant rejects
missing, duplicate, unknown, or type-mismatched results and orders validated
results to match the input question order.

Every result has these required fields:

| Field | Contract |
| --- | --- |
| `questionId` | ID of the corresponding input question. |
| `type` | The same type as that question. |
| `answerability` | `status` and an `issues` array. |
| `answer` | The type-specific shape from the previous table. It is nullable except for predicates. |
| `reason` | Nonblank human-readable explanation, at most 400 characters. The runtime does not truncate it. |
| `evidence_strength` | Required value `"strong"`, `"limited"`, or `null`. |

### Reason and evidence strength

`reason` should state the applied criterion, decisive supplied fact, and any
material limitation in one concise sentence. It is for people and must not be
parsed as a machine-readable issue. The runtime rejects a blank reason or one
longer than 400 characters; it does not truncate it.

`evidence_strength` assesses support for the whole reported assessment: its
answerability, issues, and substantive answer when present. `strong` can validly
describe a well-supported abstention or `unknown` predicate. `limited` means a
weaker but permissible interpretation and cannot excuse an invented essential
fact.

| Value | Meaning |
| --- | --- |
| `strong` | The supplied evidence decisively supports the whole assessment, including a demonstrated inability to answer. |
| `limited` | The supplied evidence supports a permissible but weaker interpretation without inventing an essential fact. |
| `null` | No strength assessment was made; this does not say whether an answer is present. |

Strength is neither model confidence nor a probability and does not change
routing by itself. A strong abstention is valid.

These fields belong to native decision results. An `llm`, `handler`, or `mcp`
step returns the text or JSON value defined by that step's authored output
contract; Foliqant does not inject decision `reason` or `evidence_strength`
fields into those results.

### Answerability and issue codes

The three issue codes are exact:

| Issue | Meaning |
| --- | --- |
| `no_supported_answer` | Supplied evidence does not support an allowed answer. This also covers missing required detail and a request outside the allowed answers. |
| `conflicting_information` | Supplied evidence contains an unresolved material conflict. |
| `multiple_valid_options` | Positively supported answers exceed the question's permitted cardinality. |

Issue codes must be unique and there can be at most three. Any status other than
`answerable` requires at least one issue. Code should branch on status and issue
codes, never parse `reason`.

Answer presence follows this table:

| Status | Meaning and allowed result |
| --- | --- |
| `answerable` | Allowed evidence supports a substantive answer. Predicate values are `true` or `false`. Empty multiselect and request-unit collections can be substantive answers when the authored question permits them. |
| `partially_answerable` | A collection has a supported subset and an unresolved remainder. This is supported only by `multiselect` and `request_units`; the answer must contain at least one selected option or request unit. |
| `not_answerable` | Evidence or question constraints do not support a permitted answer. Non-predicate answers must be `null`; a predicate answer is `{"value": "unknown"}`. At least one issue is required. |
| `undetermined` | The assessment could not establish answerability. It has the same answer-presence rule as `not_answerable`, and at least one issue is required. |

The structural contract does not prove that a reason or strength rating is
factually correct. Semantic quality must be assessed against reviewed criteria
and evaluation data.

### One result of each type

The following is a **complete `DecisionOutput`** for the five-question input
above:

```json
{
  "results": [
    {
      "questionId": "request_kind",
      "type": "choice",
      "answerability": {
        "status": "not_answerable",
        "issues": ["multiple_valid_options"]
      },
      "answer": null,
      "reason": "The message contains two distinct requests, so one category cannot represent both.",
      "evidence_strength": "strong"
    },
    {
      "questionId": "request_labels",
      "type": "multiselect",
      "answerability": {"status": "answerable", "issues": []},
      "answer": {"optionIds": ["freeze_card", "send_statement"]},
      "reason": "Both actions are explicitly requested.",
      "evidence_strength": "strong"
    },
    {
      "questionId": "has_card_request",
      "type": "predicate",
      "answerability": {"status": "answerable", "issues": []},
      "answer": {"value": "true"},
      "reason": "The message explicitly asks to freeze card-123.",
      "evidence_strength": "strong"
    },
    {
      "questionId": "urgency",
      "type": "ordinal",
      "answerability": {
        "status": "not_answerable",
        "issues": ["no_supported_answer"]
      },
      "answer": null,
      "reason": "The message provides no timing language for either request.",
      "evidence_strength": "strong"
    },
    {
      "questionId": "requests",
      "type": "request_units",
      "answerability": {"status": "answerable", "issues": []},
      "answer": {
        "units": [
          {
            "id": "request_1",
            "status": "active",
            "categoryId": "freeze_card",
            "subject": "card-123",
            "description": "Freeze card-123."
          },
          {
            "id": "request_2",
            "status": "active",
            "categoryId": "send_statement",
            "subject": "account-9",
            "description": "Send the statement for account-9."
          }
        ],
        "relations": []
      },
      "reason": "The message explicitly contains two active requests with named subjects.",
      "evidence_strength": "strong"
    }
  ]
}
```

A request unit requires a response-local unique `id`; `status` is `active`,
`withdrawn`, `conditional`, or `quoted`; `categoryId` and `subject` are nullable;
and `description` is nonempty. A non-null subject must occur verbatim in one of
that question's allowed sources. `categoryId: null` requires
`no_supported_answer`; it is rejected when the question has
`allowNoMatch: false`. Runtime request units do not contain evidence or citation
arrays.

Request-unit relations use one of these **relation fragments** inside
`answer.relations`:

```json
{"type": "conditional_on", "requestId": "request_1", "predicateQuestionId": "approved", "requiredValue": "true"}
```

```json
{"type": "requires", "requestId": "request_2", "requiredRequestId": "request_1"}
```

```json
{"type": "precedes", "beforeRequestId": "request_1", "afterRequestId": "request_2"}
```

```json
{"type": "mutually_exclusive", "requestIds": ["request_1", "request_2"]}
```

Relations must reference returned request units, and a conditional relation must
reference a predicate question. The conditional fragment therefore requires an
additional predicate question named `approved` in the same input. Duplicate
relations, self references, directed cycles, contradictory conditions, and a required dependency between mutually
exclusive units are invalid.

## Single-question and multi-question step results

The model adapter validates the `DecisionOutput` wrapper in both modes. The
public `StepResult.result` then depends on how the step was authored:

- A step authored with singular `question` exposes that one result object
  directly. The **complete result value** is one object such as the choice
  result in the previous example, without `{"results": [...]}`.
- A step authored with plural `questions` exposes the complete
  `{"results": [...]}` object, even when the list happens to contain one
  question.

A multi-question decision step is `completed` only when every question is
`answerable`. If any question is unresolved, the step is `needs_review`. Only a
singular choice, ordinal, or predicate question supplies a direct route key.

## `ExecutionResult`

An execution result has exactly five roots:

```text
/payload
/metadata
/flows
/transitions
/execution
```

This is a **complete successful execution result** for a separate handler-only
example, so model and tool usage is zero. The business values and IDs are
illustrative; the field structure is normative.

```json
{
  "payload": {
    "category": "freeze_card"
  },
  "metadata": {},
  "flows": {
    "classify": {
      "status": "completed",
      "steps": {
        "project": {
          "status": "completed",
          "result": {
            "category": "freeze_card"
          }
        }
      },
      "result": {
        "category": "freeze_card"
      }
    }
  },
  "transitions": [
    {
      "source": "classify",
      "reason": "completed",
      "outcome": "completed"
    }
  ],
  "execution": {
    "id": "run-123",
    "workflow": "support_triage",
    "revision": "sha256-revision-value",
    "status": "completed",
    "usage": {
      "model_requests": 0,
      "tool_calls": 0,
      "input_tokens": 0,
      "output_tokens": 0,
      "cache_read_input_tokens": 0,
      "cache_write_input_tokens": 0,
      "reasoning_output_tokens": 0
    }
  }
}
```

### Payload and projections

`payload` is the workflow's public output projection. When the workflow has no
`output` binding, it defaults to the accepted input payload. A flow's `result`
is its flow output projection; with no flow output binding, it defaults to that
flow's bound input.

The top-level result does not contain a flat decisions map. Inspect a local step
at `/flows/{flow}/steps/{step}/result` and a projected flow value at
`/flows/{flow}/result`. Workflow routing and output bindings can read the
original `/payload`, `/metadata`, and `/flows/{flow}/result`. They cannot read
another flow's internal step ledger. Callable-flow results remain nested inside
their collection step.

Presence and JSON `null` are different. A completed step and completed flow
always contain `result`, and that explicit result may itself be `null`. Other
status rules can require `result` to be absent.

### Flow and step records

`flows` is keyed by flow instance ID. Every flow record contains `status` and a
`steps` object. Its declared local steps remain visible in the ledger, including
steps recorded as `skipped` after execution stopped.

Flow fields:

| Field | Presence and meaning |
| --- | --- |
| `status` | Required: `completed`, `needs_review`, `failed`, `cancelled`, or `skipped`. |
| `steps` | Required map of local step IDs to step records. |
| `result` | Required for `completed`; optional for `needs_review`; absent for `failed`, `cancelled`, and `skipped`. |
| `usage` | Optional measured usage for the flow. |
| `elapsed_seconds` | Optional non-negative finite duration. |
| `error` | Required for `failed`, forbidden for `completed`, `needs_review`, and `skipped`; may accompany `cancelled`. |

Step records follow the same status, result, measurement, and error rules, but
do not contain `steps`. A skipped step has no result, error, elapsed time, or
usage. Decision steps may also contain `selection`. Flow-collection steps use
these additional fields:

| Field | Contract |
| --- | --- |
| `kind` | `"flow_collection"`; present on collection records that need type-specific interpretation. |
| `result` | On a completed or review collection, `{"items": [...]}` with the ordered complete child-flow ledger. |
| `partial_result` | On a failed collection only, the `{"items": [...]}` ledger produced through failure. `kind` is then required. |

Each collection item adds required `id` and `flow` fields to the normal flow
record. Item IDs are unique. Its nested `steps`, status, result, measurements,
and safe error follow the flow rules above.

Each transition records the source flow, a `reason` of `completed` or
`needs_review`, and exactly one authored target: either `flow` or terminal
`outcome`. A technical error does not select an unresolved transition.

### Execution information and usage

`execution` always contains `id`, `workflow`, `revision`, `status`, and `usage`.
Execution status is one of `completed`, `needs_review`, `failed`, or
`cancelled`; unlike flow and step status, it has no `skipped` value. `failed`
and `cancelled` require `execution.error`. Successful and review executions
must omit it.

Every included `usage` object contains all seven fields shown above.
`model_requests` and `tool_calls` are non-negative integers. Token values are
non-negative integers or `null`; `null` preserves unavailable provider data and
must not be interpreted as zero. Cache-read and cache-write input tokens cannot
exceed a known input-token total, and reasoning output tokens cannot exceed a
known output-token total.

Usage appears at several scopes for inspection. Do not add parent and child
values together: parent usage already aggregates its owned work.

## Native answer versus effective selection

Only a singular choice decision can expose `selection`. The native result stays
unchanged:

- An answerable model choice produces `selection.origin: "model"`, and the
  selected category ID must equal native `answer.optionId`. The step is
  `completed`.
- A configured fallback can produce `selection.origin: "fallback"` only when
  the native result is `not_answerable`, has at least one issue, and every issue
  is in the fallback's explicit `on` allowlist. The native `answer` remains
  `null`, its answerability and issues remain unchanged, and the step remains
  `needs_review`.
- When neither rule produces an effective category, `selection` is omitted. It
  is never serialized as `null`.

This **complete fallback step record** shows the separation:

```json
{
  "status": "needs_review",
  "result": {
    "questionId": "request_kind",
    "type": "choice",
    "answerability": {
      "status": "not_answerable",
      "issues": ["no_supported_answer"]
    },
    "answer": null,
    "reason": "The supplied message does not support any configured category.",
    "evidence_strength": "strong"
  },
  "selection": {
    "category": {"id": "manual_review"},
    "origin": "fallback"
  }
}
```

Use the native result to evaluate model correctness and `selection` to observe
the effective application policy.

## Semantic review and operational failure are separate

An answerability issue is valid business output, not a technical error. An
answerability status other than `answerable` produces review behavior; issue
codes explain that unresolved work and may select configured unresolved routes.
An `answerable` result can still contain an issue permitted by its native
contract: with `allowNoMatch`, a fully identified request collection can contain
an uncategorized unit and `no_supported_answer`. A `needs_review` execution has
no `execution.error`, and the CLI treats it as a successful run.

Operational failures use a `SafeError` with:

| Field | Contract |
| --- | --- |
| `code` | One stable code listed below. |
| `message` | The canonical message for that code, never raw provider exception text. |
| `retryable` | Required boolean set by the failing boundary. Do not infer it from the code. |
| `location` | Optional nonblank bounded string; omitted when unavailable. |

Stable operational codes are:

| Code | Meaning |
| --- | --- |
| `invalid_configuration` | The workflow configuration is invalid. |
| `invalid_input` | The input does not satisfy the required contract. |
| `invalid_output` | An operation returned an invalid result. |
| `unauthenticated` | Authentication is required. |
| `forbidden` | The operation is not authorized. |
| `not_found` | The requested resource was not found. |
| `missing_binding` | A required input binding is unavailable. |
| `timeout` | The operation exceeded its deadline. |
| `budget_exhausted` | The execution budget is exhausted. |
| `dependency_failure` | A required dependency is unavailable. |
| `conflict` | The request conflicts with an existing operation. |
| `uncertain_effect` | An external operation requires reconciliation. |
| `cancelled` | The execution was cancelled. |
| `capacity_exceeded` | The service has reached its admission limit. |

These codes do not define HTTP status mappings or add authentication to the
library; those remain host responsibilities. A retryable error is not proof
that repeating an external action is safe. In particular, timeout or
cancellation does not prove that remote work stopped, and `uncertain_effect`
requires reconciliation before repeating an action.

This is a **complete failed execution result**:

```json
{
  "payload": {"message": "Classify this request."},
  "metadata": {},
  "flows": {
    "classify": {
      "status": "failed",
      "steps": {
        "choose": {
          "status": "failed",
          "error": {
            "code": "dependency_failure",
            "message": "A required dependency is unavailable.",
            "retryable": true
          }
        }
      },
      "error": {
        "code": "dependency_failure",
        "message": "A required dependency is unavailable.",
        "retryable": true
      }
    }
  },
  "transitions": [],
  "execution": {
    "id": "run-124",
    "workflow": "support_triage",
    "revision": "sha256-revision-value",
    "status": "failed",
    "usage": {
      "model_requests": 1,
      "tool_calls": 0,
      "input_tokens": null,
      "output_tokens": null,
      "cache_read_input_tokens": null,
      "cache_write_input_tokens": null,
      "reasoning_output_tokens": null
    },
    "error": {
      "code": "dependency_failure",
      "message": "A required dependency is unavailable.",
      "retryable": true
    }
  }
}
```

Technical failure preserves the accepted top-level input and completed records.
Some failures, such as invalid admission input or capacity rejection, can raise
`ServiceError` before an `ExecutionResult` is available. Caller cancellation
propagates and does not promise a returned result or that already-running remote
work stopped.

## CLI result and failure boundary

`foliqant run` writes a successful or `needs_review` `ExecutionResult` as one
JSON line on standard output and exits `0`. If execution returns `failed` or
`cancelled`, the CLI converts its safe execution error to the separate CLI
failure boundary rather than printing the failed `ExecutionResult`.

A CLI failure is one JSON line on standard error:

```json
{
  "error": {
    "code": "invalid_input",
    "message": "The input does not satisfy the required contract.",
    "retryable": false
  }
}
```

This is a **complete minimal CLI failure**. Configuration failures may also
include safe `reason`, `field`, `hint`, and structured `location` fields. Those
diagnostics belong to the CLI failure object; they are not fields of a native
decision issue or the execution `SafeError` shown above.

CLI exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | Command success, including a workflow that ends in `needs_review`. |
| `1` | Evaluation gold mismatch. |
| `2` | Invalid arguments, input, or configuration. |
| `3` | Missing optional dependency. |
| `4` | Runtime failure. |
| `130` | Interruption or cancellation. |

## Defaults, nulls, and omitted fields

The Python models and generated JSON Schemas are the authority for defaults and
presence rules. The main boundary rules to remember are:

- `Envelope.metadata` defaults to `{}`. Native decision input and output fields
  shown as required above have no inferred business default.
- Unknown fields are rejected, except application-defined fields in envelope
  `metadata`.
- Protected metadata fields, errors, selections, optional category
  descriptions, collection `kind`, and `partial_result` are omitted when absent
  and reject explicit `null`.
- `elapsed_seconds` and record-level `usage` may validate as `null`, but
  canonical result serialization omits them when unavailable.
- Nullable native answers and required token measurements use explicit `null`
  with the meanings described above.
- `evidence_strength` is required even when its value is `null`.
- Result presence is tracked separately from its value, so a required
  `result: null` is different from an omitted `result`.

The packaged schemas are `envelope.schema.json`,
`decision-input.schema.json`, `decision-output.schema.json`, and
`execution-result.schema.json` under `foliqant/schemas`. Python callers can use
`Envelope` and `ExecutionResult` from `foliqant`, `DecisionInput` from
`foliqant.decisions`, and `DecisionOutput` plus
`validate_decision_output` from `foliqant.contracts.decisions`.

## Validate saved JSON locally

For trusted local examples, validate the complete objects with the public
models. Save the native input and output examples as `decision-input.json` and
`decision-output.json`, then run:

```python
from pathlib import Path

from foliqant.contracts.decisions import DecisionOutput, validate_decision_output
from foliqant.decisions import DecisionInput

task = DecisionInput.model_validate_json(Path("decision-input.json").read_text(), strict=True)
result = DecisionOutput.model_validate_json(Path("decision-output.json").read_text(), strict=True)
problems = validate_decision_output(task, result)
if problems:
    raise ValueError(problems)
```

The model validators check field shapes and internal consistency. The final
check also verifies question correspondence, permitted answers, and source
references. A generated JSON Schema alone cannot check those relationships
between separate input and output objects. Neither check proves that an answer
or reason is factually correct; use reviewed evaluation cases for that.

Use `Envelope.model_validate_json(...)` or
`ExecutionResult.model_validate_json(...)` for their respective saved examples.
Do not expose validation exception text to untrusted callers; use the bounded
decoder for request bytes as shown above.

For workflow authoring and binding scopes, continue with
[Build workflows](../guides/build-workflows.md). For criteria design and
application policy, see [Use decision contracts](../guides/decision-contracts.md).

# Set up golden workflow evaluation

Use this self-contained reference for evaluation datasets, result inspection,
replay, or quality measurement. Confirm exact flags with
`foliqant evaluate --help` from the installed release.

Place conventional gold at `evaluation/dataset.json` beside `config/`.
Foliqant looks it up only for an explicit evaluation command. Configure
`evaluation.dataset` only for an intentional alternate path.

## Contents

- [Dataset shape](#dataset-shape)
- [Select the measured boundary](#select-the-measured-boundary)
- [Author independent expectations](#author-independent-expectations)
- [Choose the execution mode](#choose-the-execution-mode)
- [Interpret honestly](#interpret-honestly)
- [Deliverables and checks](#deliverables-and-checks)

## Dataset shape

The root has `name`, caller-maintained `revision`, and a nonempty unique
`suites` array. A minimal pipeline dataset is:

```json
{
  "name": "intake_gold",
  "revision": "reviewed-2026-09-22",
  "suites": [
    {
      "name": "pipeline",
      "workflow": "intake",
      "cases": [
        {
          "id": "cancel_en",
          "input": {
            "payload": {"message": "Cancel my renewal."},
            "metadata": {"language": "en"}
          },
          "expectations": [
            {
              "name": "completed",
              "path": "/execution/status",
              "expected": "completed"
            },
            {
              "name": "queue",
              "path": "/flows/triage/steps/classify/result/answer/optionId",
              "expected": "cancellation"
            }
          ]
        }
      ],
      "metrics": [
        {
          "name": "queue_quality",
          "path": "/flows/triage/steps/classify/result/answer/optionId",
          "kind": "classification",
          "labels": ["cancellation", "billing", null]
        }
      ]
    }
  ]
}
```

Suite fields are:

| Field | Shape |
| --- | --- |
| `name` | Unique nonempty suite ID |
| `workflow` | Configured workflow ID |
| `flow` | Optional flow target |
| `step` | Optional step target; requires `flow` |
| `cases` | Nonempty inline case array or JSON filename |
| `metrics` | Optional unique metric declarations |

A referenced case file is a JSON array resolved relative to the dataset file.
Each case has a unique `id`, an `input` envelope with `payload` and optional
`metadata`, and nonempty unique named `expectations`.

Each JSON expectation has `name`, RFC 6901 `path`, JSON `expected`, and
optional `comparison`:

| Comparison | Contract |
| --- | --- |
| `exact` | Preserve JSON type, array order, and value |
| `set` | Expected and actual are top-level arrays; ignore order and duplicates |
| `source_span` | Expected declares `input_path`, required `[start,end]`, and allowed `[start,end]` ranges |

`custom` comparison is Python-only. Construct
`Expectation(..., comparison="custom", scorer="name")` and provide a matching
`RegisteredScorer(name, revision, async_score)`; JSON configuration cannot
import scorer code.

A metric has unique `name`, result `path`,
`kind: classification|multilabel`, and a nonempty unique ordered `labels`
catalog. Classification gold is one declared string or declared `null`;
multilabel gold is a unique array of declared strings. Every metric needs at
least one matching expectation at its path. A missing nested result path is not
the same observation as a present JSON `null`; choose an always-present path
or separate unanswered cases when declaring a metric.

## Select the measured boundary

1. Use a pipeline suite (`workflow`) for business behavior and routing.
2. Add a flow suite (`workflow`, `flow`) to isolate a reusable flow.
3. Add an operation suite (`workflow`, `flow`, `step`) to isolate one
   decision, LLM, MCP, handler, or flow-collection operation.

A step without a flow is invalid. Scoped cases supply already-resolved boundary
input; they do not execute upstream bindings. Do not treat the same examples at
three scopes as three independent gold samples.

## Author independent expectations

Use stable case IDs and reviewed JSON-pointer expectations over public results:

```text
/execution/status
/payload/...
/flows/{flow}/result/...
/flows/{flow}/steps/{step}/result/...
/flows/{flow}/steps/{step}/selection/category/id
/flows/{flow}/steps/{collection}/result/items/{index}/steps/{step}/result/...
```

Use `partial_result.items` for a failed collection ledger. Nested child flow and
step observations include `invocation_path`; use it to distinguish repeated
calls to the same definition. Grouped summaries count every observed child
invocation, while `source_case_count` remains the number of authored cases.
Parent usage is already the root total, so never add child summaries to it.

Use `exact` for type- and order-sensitive values, `set` for top-level arrays,
and `source_span` for bounded verbatim extraction. Declare classification or
multilabel catalogs explicitly. Do not infer gold from model responses.

Keep native unresolved decision outcomes separate from effective fallback
selection. Score answerability/issues and selection origin independently.

## Choose the execution mode

The generic CLI has no host handler registrations. When the configuration uses
trusted Python handlers, prepare through the host and check gold offline with:

```python
from foliqant import prepare_application
from foliqant.evaluation.dataset import load_dataset

prepared = prepare_application(config_path, handlers=handlers)
dataset = load_dataset(prepared)
```

Here `handlers` is the application's registration map and `config_path` is its
settings path. `load_dataset` resolves the configured/conventional dataset and
validates its targets against that prepared application without opening adapters.
Use Python `evaluate` with the same application's public scoped run functions
for execution; do not remove handlers to make a generic CLI check pass.

- `foliqant evaluate --check` validates dataset structure, targets, pointers,
  catalogs, and spans without opening providers.
- `foliqant evaluate --replay REPORT` rescores saved results without inference.
- `foliqant evaluate --compare CANDIDATE --baseline BASELINE` compares two
  compatible saved artifacts without loading runtime configuration.
- `foliqant evaluate` executes the configured pipeline, flow, or operation.

Normal evaluation may make model and tool calls. Keep default concurrency
conservative and use explicit `--repeat` only for variability. The evaluator
adds no retry; explicitly configured provider retries still apply within each
operation. Do not add judge calls, endpoint discovery, or automatic prompt
optimization.

Invocation controls are:

| CLI | Python `evaluate` | Meaning |
| --- | --- | --- |
| `--max-concurrency N` | `max_concurrency=N` | Concurrent case calls; default 1 |
| `--timeout SECONDS` | `timeout=SECONDS` | Per-case deadline; default 300 |
| `--repeat N` | `repeat=N` | Equal attempts per source case; default 1 |
| `--output PATH` | Call `foliqant.evaluation.artifact.write_report` after evaluation | New private artifact; no overwrite |
| n/a | `include_details=True` | Retain private input, gold, and complete result |

Python variants use `EvaluationVariant(name, revision,
configuration_revision, run, workflow?, flow?, step?)`. The async `run`
callable must match the declared target: application `run`, `run_flow`, or
`run_step`. Pass optional `metrics` and registered `scorers` to
`evaluate`; use `compare_variants` only for explicitly authored variants.

The CLI writes unique private report files under `.foliqant/evaluations/` by
default; Python `evaluate` returns an in-memory report. Full details may contain
input, gold, and public results. Keep saved reports ignored and do not print
business values to console output.

## Interpret honestly

Agreement with gold, operational failure, and intended review are separate.
Missing, skipped, and error observations remain in denominators. Unknown usage
is not zero. Flow and operation summaries are views of the same execution; do
not add their usage to workflow totals.

Flow and step summaries count `observed_invocations`, `skipped_invocations`,
`failed_invocations`, and `review_invocations`. Decision steps additionally
count `model_selected_invocations` and `fallback_selected_invocations`.
Repeated callable-flow executions increase these observations, not the authored
source-case count.

Replay can measure changed gold or scorer logic, not a changed prompt or model.
Comparison requires matching workflow/flow/step targets, cases, gold, scorers,
catalogs, and repetitions. It reports both improvements and regressions without
inventing a release threshold.

Use scripted examples for deterministic wiring. Use representative live
development cases to select a candidate and an untouched holdout to confirm it.
Do not claim a small suite proves general quality, security, or efficiency.

## Deliverables and checks

Deliver the dataset manifest, any referenced case files, and a note identifying
which expectations have been independently reviewed. Run
`foliqant evaluate --check` from the application root, or add `--config PATH`
for nondefault settings. With host handlers, use registered preparation and
`load_dataset` above. Report structural failures separately from missing
business gold. Execute live suites only when their configured external calls
are intended for the task.

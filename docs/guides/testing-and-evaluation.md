# Test and evaluate workflows

Foliqant scores explicit JSON-pointer expectations against public
`ExecutionResult` values. Gold remains caller-authored: the evaluator does not
infer expected answers, call a judge, optimize prompts, or select a winning
variant.

Use three complementary scopes:

| Scope | Dataset fields | Execution |
| --- | --- | --- |
| Pipeline | `workflow` | Full workflow routing through `application.run` |
| Flow | `workflow`, `flow` | One flow through `application.run_flow` |
| Operation | `workflow`, `flow`, `step` | One operation through `application.run_step` |

A step target always requires its containing flow. Flow and operation cases
provide already-resolved boundary inputs; they do not run upstream bindings or
routes. Pipeline cases exercise the original workflow envelope and routing.

## Place the dataset

The conventional project layout keeps reviewed gold beside `config/`:

```text
config/
  settings.yaml
  support_triage/
    workflow.yaml
evaluation/
  dataset.json
```

When `evaluation.dataset` is omitted, an explicit `foliqant evaluate` command
looks for `evaluation/dataset.json` beside `config/`. Runtime preparation,
startup, validation, and execution never inspect it.

Use an explicit reference in `config/settings.yaml` only for a customized
location:

```yaml
evaluation:
  dataset: ../../reviewed-gold/support.json
```

The explicit path is relative to the settings file. A small dataset can keep
cases inline:

```json
{
  "name": "support_checks",
  "revision": "reviewed-2026-09-22",
  "suites": [
    {
      "name": "support_pipeline",
      "workflow": "support_triage",
      "cases": [
        {
          "id": "cancel_en",
          "input": {
            "payload": {
              "requestId": "eval-001",
              "message": "Cancel renewal for account C-1049."
            },
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
          "labels": ["billing_dispute", "service_change", "cancellation", null]
        }
      ]
    }
  ]
}
```

`name` and `revision` identify the authored suite content. Change the
revision when the reviewed cases or their meaning change; the evaluator also
computes a content fingerprint.

Use public result paths:

| Observation | Pointer |
| --- | --- |
| Terminal workflow status | `/execution/status` |
| Workflow payload | `/payload/...` |
| Flow result | `/flows/{flow}/result/...` |
| Operation result | `/flows/{flow}/steps/{step}/result/...` |
| Effective decision selection | `/flows/{flow}/steps/{step}/selection/category/id` |
| Usage | `/execution/usage/model_requests` |

The dataset never uses authored local binding paths such as
`/steps/{step}/...`; those exist only while a flow executes.

## Split larger gold files

`cases` may be a JSON filename instead of an inline array. It resolves relative
to the dataset manifest:

```json
{
  "name": "support_checks",
  "revision": "reviewed-2026-09-22",
  "suites": [
    {
      "name": "pipeline",
      "workflow": "support_triage",
      "cases": "pipeline.json"
    },
    {
      "name": "triage_flow",
      "workflow": "support_triage",
      "flow": "triage",
      "cases": "triage-flow.json"
    },
    {
      "name": "classification",
      "workflow": "support_triage",
      "flow": "triage",
      "step": "classify",
      "cases": "classify-step.json"
    }
  ]
}
```

Case files contain only the case array. Keep related translations and source
families together when splitting development and holdout data.

## Author comparisons

Each expectation uses one comparison:

- `exact` preserves JSON types and array order.
- `set` compares a top-level array without order or duplicates.
- `source_span` checks a required span inside an allowed span of case input.
- A Python-only `custom` expectation names an explicitly registered async
  scorer and records its revision.

For `source_span`, the expected value declares `input_path`, a required
`[start, end]` range, and an allowed `[start, end]` range. This scores
verbatim extraction without copying sensitive source text into console output.

Metrics are optional and use independently declared label catalogs.
`classification` produces confusion counts and per-label precision, recall,
and F1. `multilabel` produces exact-set and micro/macro label summaries.
Missing, skipped, and error observations stay in denominators.

## Check without execution

```sh
foliqant evaluate --check
```

This validates dataset structure, configured workflow/flow/step targets,
structural pointer roots, metric catalogs, and source spans. It does not open
model or MCP clients and cannot prove that dynamic fields exist or that business
gold is correct.

Run a configured evaluation only when its external calls are intended:

```sh
foliqant evaluate --max-concurrency 1 --timeout 300
```

Model-backed suites make real model calls; MCP-backed suites open their declared
clients. The evaluator adds no retry, endpoint discovery, or judge calls.
Explicit model or MCP profile retries still apply to each runtime operation. A
model-free or scripted example checks wiring, not model quality.

## Use pipeline, flow, and operation suites together

Pipeline evaluation is the business-facing measurement because it exercises
boundary validation, flow routing, and context bindings. Flow evaluation isolates
one reusable flow. Operation evaluation isolates a single prompt, decision, tool,
or handler with its final input shape.

Use scoped suites to diagnose a pipeline result, but do not count repeated
pipeline, flow, and operation cases as additional independent business gold.
Correct isolated output with an incorrect pipeline points toward upstream
bindings, flow projections, or routing. An isolated failure points toward the
operation contract, provider behavior, or its direct input.

## Replay and compare offline

Normal evaluation writes a unique private report under
`.foliqant/evaluations/` beside the settings file:

```sh
foliqant evaluate --output .foliqant/evaluations/baseline.json
foliqant evaluate \
  --replay .foliqant/evaluations/baseline.json \
  --output .foliqant/evaluations/rescored.json
```

Replay scores saved full results against the current gold without opening SDK
clients. It can measure changed expectations or scorers, but cannot measure a
changed prompt, model, tool, or workflow execution.

Compare two compatible saved reports:

```sh
foliqant evaluate \
  --compare .foliqant/evaluations/candidate.json \
  --baseline .foliqant/evaluations/baseline.json \
  --output .foliqant/evaluations/comparison.json
```

Comparison requires matching suite/case inputs, gold, workflow/flow/step targets,
scorers, catalogs, and attempt identities. Configuration revisions may differ.
The result preserves improvements and regressions; it does not apply an invented
acceptance threshold.

## Measure variability explicitly

`--repeat N` executes every source case the same number of times. `case_count`
counts authored sources and `attempt_count` counts executions. Repetition is
useful for variability, but repeated attempts are not independent cases.

Defaults are sequential suites, one case at a time, and a 300-second per-case
timeout. Increase concurrency only when the provider and local capacity can
support it.

## Embed the evaluator

```python
from foliqant.evaluation import EvaluationVariant, evaluate

variant = EvaluationVariant(
    name="candidate",
    revision="served-model-2026-09-22",
    configuration_revision=prepared.configuration_digest,
    workflow="support_triage",
    flow="triage",
    step="classify",
    run=lambda envelope: application.run_step("support_triage", "triage", "classify", envelope),
)

report = await evaluate(suite, variant, include_details=True)
```

For a flow variant, omit `step` and call `run_flow`; for a pipeline variant,
omit both and call `run`. Python evaluation returns an in-memory report.
`write_report` is an explicit separate operation.

See the runnable
[support triage](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md),
[public-request MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md),
and [extraction-to-MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/extracted_request_mcp/README.md)
examples for pipeline, flow, and operation suites.

## Use evaluation evidence carefully

Keep full reports private: detailed mode contains complete inputs, expectations,
and public results. Offline checks establish structure. Scripted suites establish
deterministic wiring. Live evaluations measure only the chosen cases, endpoint,
configuration, and time. None of these alone proves general accuracy, security,
or efficiency.

Public runnable examples keep their small authored synthetic datasets under
`examples/<name>/evaluation/` so reviewers can inspect the expectation source.
Do not place generated reports, downloaded corpora, customer data, or private
experiment results there.

Select prompts and model settings on development gold, then confirm once on an
untouched holdout. Preserve failures, reviews, missing outputs, and usage gaps;
do not drop them from denominators.

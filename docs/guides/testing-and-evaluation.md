# Test and evaluate workflows

Add an optional dataset reference to your application configuration, then run
`foliqant evaluate`. It runs the configured workflows against authored gold and
saves a local report. No evaluation package, hosted account, or judge model is
required. Workflows that use models or tools call their configured dependencies;
a model-free workflow needs neither.

Use `--check` to validate the dataset without executing anything. Use `--replay`
to score saved results again without model or tool calls. Unit tests with fakes,
synthetic workflow checks, and quality measurements against reviewed gold answer
different questions; keep their evidence separate.

See [understand evaluation results](evaluation-results.md) for interpreting
scores, diagnosing failures, and choosing useful coverage before optimizing.

## Start with a model-free evaluation

Create a project using the [runtime installation](../getting-started/runtime.md):

```sh
uv run --no-sync foliqant init /tmp/foliqant-eval-demo
mkdir -p /tmp/foliqant-eval-demo/.foliqant/evaluation
```

The generated `demo` workflow finishes successfully and returns the input
payload. Replace `/tmp/foliqant-eval-demo/foliqant.yaml` with:

```yaml
version: 1
workflows:
  demo: workflows/demo
evaluation:
  dataset: .foliqant/evaluation/gold.json
```

Save this complete synthetic dataset as
`/tmp/foliqant-eval-demo/.foliqant/evaluation/gold.json`:

```json
{
  "version": 1,
  "name": "demo_gold",
  "revision": "1",
  "suites": [
    {
      "name": "demo_pipeline",
      "workflow": "demo",
      "metrics": [
        {
          "name": "category",
          "path": "/payload/category",
          "kind": "classification",
          "labels": ["billing", "cancellation"]
        }
      ],
      "cases": [
        {
          "id": "billing_request",
          "input": {
            "payload": {"category": "billing", "tags": ["invoice", "account"]},
            "metadata": {}
          },
          "expectations": [
            {"name": "completed", "path": "/execution/status", "expected": "completed"},
            {"name": "category", "path": "/payload/category", "expected": "billing"},
            {
              "name": "tags",
              "path": "/payload/tags",
              "expected": ["account", "invoice"],
              "comparison": "set"
            }
          ]
        },
        {
          "id": "cancellation_request",
          "input": {
            "payload": {"category": "cancellation"},
            "metadata": {}
          },
          "expectations": [
            {"name": "completed", "path": "/execution/status", "expected": "completed"},
            {"name": "category", "path": "/payload/category", "expected": "cancellation"}
          ]
        }
      ]
    }
  ]
}
```

Run the offline check, then execute the synthetic workflow:

```sh
uv run --no-sync foliqant evaluate \
  --config /tmp/foliqant-eval-demo/foliqant.yaml --check
uv run --no-sync foliqant evaluate \
  --config /tmp/foliqant-eval-demo/foliqant.yaml
```

The first command checks dataset structure, workflow/step targets, pointer syntax
and known result roots/steps, and gold label catalogs. It cannot prove that a
dynamic payload field will exist or that authored gold is correct. The second executes
the finish-only workflow and should pass all five assertions. Its classification
matrix should have one correct billing case and one correct cancellation case.
This proves dataset loading, execution, and scoring work together; it does not
measure a classifier or model.

To verify that disagreement is caught, change only the first case's expected
category to `"cancellation"`, keep its input unchanged, and rerun. The command
should exit with status `1`. Restore the authored gold afterward.

The dataset path resolves relative to `foliqant.yaml`, even when the command is
run from another directory. Application startup, `validate`, and `doctor` do not
open or inspect this optional dataset. Changing the evaluation reference does
not change the runtime configuration digest.

## Author gold for your workflow

A dataset contains `version: 1`, a `name`, a `revision`, and nonempty `suites`.
Each suite selects a configured `workflow`, names its cases, and may declare
metrics. Dataset files use strict JSON, not YAML, JSONL, or executable imports.
The optional YAML application setting only points to the JSON file. Each main
dataset or referenced case file is limited to 64 MiB.

Each case has a stable `id`, an `input` envelope with `payload` and `metadata`,
and named `expectations`. Authors supply the expected values independently of
model outputs. Include representative successes, ambiguous inputs, missing
information, and relevant boundaries. Keep customer corpora outside Git; the
inline values above are deliberately synthetic.

Expectation paths are RFC 6901 JSON pointers over the public `ExecutionResult`:

| What to check | Example path |
| --- | --- |
| Terminal workflow status | `/execution/status` |
| Final payload field | `/payload/category` |
| Native classification result | `/decisions/classify/result/answer/optionId` |
| A schema-output field | `/decisions/extract/result/account_reference` |
| Whether a step required review | `/decisions/classify/status` |

`comparison` defaults to `exact`, preserving JSON scalar types and array order;
for example, JSON `1` and `1.0` differ.
`set` compares top-level arrays without order or duplicate sensitivity; nested
values still use exact JSON comparison. Missing differs from explicit `null`.
A successful schema check alone does not establish correct extracted values:
assert each business field that matters. An expected abstention or review should
have an explicit status/result assertion, rather than being omitted from the gold.

Custom async scorers remain available through the Python API. Dataset
configuration supports exact, set, and source-span comparisons; it never imports Python
code or calls an implicit model judge.

For verbatim extraction with flexible boundaries, use
[`source_span` gold](evaluation-results.md#score-verbatim-extraction).

The [decision evidence example](https://github.com/sebastianwessel/foliqant/blob/main/examples/decision_evidence/README.md)
provides focused gold for reason/strength responses across the question types.
Its default evaluator validates configuration and gold offline; `--live` runs
the real configured model and retains a private report.

## Keep cases together or split them by step

Keep small datasets inline, as in the cookbook. For larger suites, `cases` may
instead be a path string. One manifest can mix inline case arrays and file
references. The application still needs only one `evaluation.dataset` entry.

For a configured `support_triage` workflow, a split manifest can be:

```json
{
  "version": 1,
  "name": "support_gold",
  "revision": "1",
  "suites": [
    {
      "name": "classification",
      "workflow": "support_triage",
      "step": "classify",
      "cases": "classify.json",
      "metrics": [{
        "name": "queue",
        "path": "/decisions/classify/result/answer/optionId",
        "kind": "classification",
        "labels": ["billing_dispute", "service_change", "cancellation"]
      }]
    },
    {
      "name": "extraction",
      "workflow": "support_triage",
      "step": "extract",
      "cases": "extract.json"
    },
    {
      "name": "pipeline",
      "workflow": "support_triage",
      "cases": "pipeline.json"
    }
  ]
}
```

Each referenced file contains a JSON **array of cases**, with no dataset or
suite wrapper. For example, `classify.json` could contain:

```json
[
  {
    "id": "explicit_cancellation",
    "input": {
      "payload": {"message": "Cancel renewal for account C-1049."},
      "metadata": {}
    },
    "expectations": [{
      "name": "queue",
      "path": "/decisions/classify/result/answer/optionId",
      "expected": "cancellation"
    }]
  }
]
```

Create `extract.json` and `pipeline.json` with the same case shape, using inputs
and gold appropriate to those targets. Workflow/step targets and metric catalogs
stay in the manifest. A relative case-file path resolves from the **main dataset
file's directory**, not from `foliqant.yaml` or the current directory. Absolute
paths are also accepted. References have one level: case files contain arrays,
not references to more files.

Only `evaluate` (including `--check` and replay) reads the manifest and case
files. Ordinary startup, `validate`, and `doctor` do not inspect those files.
The same validation and scoring rules apply to inline and referenced cases.

## Evaluate a step in isolation

Add `"step": "classify"` to a suite to select an isolated step. Its case payload
contains that step's **resolved input keys**, such as:

```json
{"payload": {"message": "Cancel renewal for account C-1049."}, "metadata": {}}
```

The evaluator calls `run_step` using the normal executor, limits, and output
validation. It does not run upstream steps or follow subsequent routes. Paths
still address the public result, for example
`/decisions/classify/result/answer/optionId`.

For a native decision step, assertions can check the selected option, public
`reason`, `evidence_strength`, and review status independently.

A suite without `step` runs the full workflow. Checking the same intermediate
path there measures the step with the inputs produced by the real upstream
workflow. Use both modes to distinguish step errors from upstream/routing errors.

## Read assertions and metrics

Assertions compare individual observed values with gold. A case passes only
when all its assertions pass. Reports retain failed, missing, skipped, and errored
checks instead of silently dropping them:

- Check pass rate is passed checks divided by all declared checks.
- Check coverage is passed plus failed checks divided by all declared checks.
- Case pass rate, execution failure rate, and review rate describe different
  outcomes; agreement with an expected review is possible.
- Durations and model/tool usage reflect recorded execution. Unknown token counts
  are not zero.

Optional `metrics` summarize the labels at one expectation path across a suite.
Use `classification` for one label and `multilabel` for arrays of labels. Declare
the full label vocabulary explicitly; do not infer it from the predictions. The
metric path identifies the matching authored expectation in each case. A metric
requires at least one matching gold value; each matching case must have exactly
one expectation at that path. Multilabel gold lists must contain unique labels.
Metric reports expose the following counts:

| Field | Meaning |
| --- | --- |
| `support` | Attempts with a matching gold expectation |
| `excluded` | Attempts without gold at this metric path |
| `source_support` / `source_excluded` | Corresponding distinct authored case counts |
| `observed` | Valid predictions in the declared label vocabulary |
| `abstained` | Explicit JSON `null` prediction when null is not a declared classification label |
| `missing` / `skipped` | Unavailable pointer / target step skipped |
| `errors` | Failed execution, even if an earlier output exists |
| `invalid` | Wrong output type or labels outside the vocabulary |
| `correct` | Correct single label or exact multilabel set |
| `accuracy` | `correct / support`, or `null` when support is zero |
| `coverage` | `observed / support`, or `null` when support is zero |

Unobserved predictions remain in `support`, so abstaining does not inflate
accuracy. The classification confusion matrix and per-label counts cover
`observed` predictions; read them alongside coverage. Per-label `true_positive`, `false_positive`,
`false_negative`, and `true_negative` counts describe each label on those
observations. For multilabel predictions, the set must match exactly to increment
`correct`; partial label matches appear in the per-label counts. Numeric metric
summaries are descriptive and do not create extra pass/fail assertions.
Multilabel metric accuracy always compares sets, independently of the assertion's
`comparison`. An exact array assertion can fail on order while its multilabel
metric is correct; use `comparison: "set"` when assertion order should not matter.

Classification labels may include explicit JSON `null`. For example, measure
support on a decision step with:

```json
{
  "name": "evidence_strength",
  "path": "/decisions/classify/result/evidence_strength",
  "kind": "classification",
  "labels": ["limited", "strong", null]
}
```

Author an expectation at that path for every case, including `expected: null`
when no substantive answer is supported. A declared null is an observed label
in the confusion matrix, so unsupported `strong` predictions can be compared
with legitimate unresolved answers. Missing paths, skipped steps, and errors
remain separate; they never become null labels.

Without null in the catalog, a null prediction remains an abstention and null
gold is invalid. Assert review status separately when it has no categorical
gold; cases without an expectation at the metric path are excluded only from
that metric. All authored checks remain in the check denominator. Multilabel
catalogs and gold arrays contain strings only.

For the cookbook, read the confusion matrix with expected labels as rows and
predicted labels as columns. In declared order `[billing, cancellation]`, a
perfect matrix is:

| Expected / predicted | billing | cancellation |
| --- | ---: | ---: |
| billing | 1 | 0 |
| cancellation | 0 | 1 |

An off-diagonal count shows which label was confused with another. With only two
synthetic cases, this matrix describes those two observations, not an estimate
of production accuracy.

## Save and replay results

Normal evaluation writes a unique
`.foliqant/evaluations/report-TIMESTAMP.json` under the configuration directory.
Select a new explicit destination with `--output`; existing files are never
overwritten:

```sh
uv run --no-sync foliqant evaluate \
  --config /tmp/foliqant-eval-demo/foliqant.yaml \
  --output /tmp/foliqant-eval-demo/.foliqant/evaluations/baseline.json
uv run --no-sync foliqant evaluate \
  --config /tmp/foliqant-eval-demo/foliqant.yaml \
  --replay /tmp/foliqant-eval-demo/.foliqant/evaluations/baseline.json \
  --output /tmp/foliqant-eval-demo/.foliqant/evaluations/replayed.json
```

Replay scores the saved results without opening SDK clients or running workflows.
It is useful for inspecting revised expectations or metrics with the same
observations. Dataset/suite/case identities, inputs, and runtime/workflow revisions
must match the saved run; gold and metric definitions can change. Saved
`target_workflow` and `target_step` identify the requested target even when
invocation failed. Input comparison preserves numeric distinctions such as
`1` versus `1.0`. It cannot
measure a changed prompt, model, or workflow. Those changes need a new
execution against the same gold. Replay preserves each saved attempt's source
invocation duration and step usage; the suite's wall time measures rescoring.
It reuses each saved repetition once and never fabricates extra repetitions.
Use the original execution reports for performance comparisons.

Stdout contains a content-free summary. The saved artifact contains full case
inputs, gold, and public results, including public reason and evidence-strength fields.
It does not capture private model reasoning. Treat the report as sensitive data:
keep `.foliqant/` ignored by Git and restrict access to exported reports. The
library's default Python report omits business values; the CLI deliberately
retains them to support inspection and replay. Report writing and replay both
enforce a 256 MiB limit.

## Use evaluations in CI

Use `foliqant evaluate --check` for offline dataset/target validation. Execute a
model-free workflow or a scripted example to verify wiring. Replay a saved report
to check scoring without inference. Run `foliqant evaluate` against a model-backed
configuration only when that endpoint execution is intended; unlike the scripted
example commands, the generic command does not install fake model responses.

From the repository checkout, `./scripts/evaluate` forwards the same options to
`foliqant evaluate`.

Suites run sequentially, with one concurrent case by default. `--max-concurrency`
opts into bounded case concurrency; `--timeout` defaults to 300 seconds per case.
The configured runtime and dependency limits still apply. Avoid competing live
evaluations against a capacity-limited local server.

| Exit code | Meaning |
| --- | --- |
| `0` | Requested check or evaluation passed |
| `1` | Results did not satisfy the authored gold |
| `2` | Invalid configuration, dataset, or command input |
| `3` | A required dependency is missing |
| `4` | Runtime execution failed |

A failed, cancelled, or errored execution yields `4` even if an assertion expected
that status. Matching an intended `needs_review` result does not itself fail the
command.

No numeric score threshold or model judge is configured here. Review metrics
alongside failed cases. Select prompt/model variants on development data, then
confirm the choice on a separate reviewed holdout. Do not adjust gold to match
an observed answer or repeatedly tune on the holdout. Caller-supplied model
revisions identify configuration, not verified provider weights.

## Use the Python API and local fakes

Use `EvaluationCase`, `EvaluationSuite`, `Expectation`, `EvaluationVariant`,
`evaluate`, and `compare_variants` from `foliqant.evaluation` when embedding
custom scorers or comparing explicitly authored variants. Close over
`application.run(workflow, envelope)` or
`application.run_step(workflow, step, envelope)` in the variant's async `run`
callable. Custom predicates use an explicit versioned `RegisteredScorer`;
configuration never imports them. For an already opened `application` and its
`prepared` configuration:

```python
from foliqant import Envelope
from foliqant.evaluation import (
    EvaluationCase, EvaluationSuite, EvaluationVariant, Expectation, evaluate,
)

suite = EvaluationSuite(
    name="demo_pipeline",
    revision="1",
    cases=(EvaluationCase(
        id="completed",
        envelope=Envelope(payload={"message": "hello"}),
        expectations=(Expectation("completed", "/execution/status", "completed"),),
    ),),
)
variant = EvaluationVariant(
    name="baseline",
    revision="1",
    configuration_revision=prepared.configuration_digest,
    run=lambda envelope: application.run("demo", envelope),
)
report = await evaluate(suite, variant)
print(report.checks.pass_rate)
```

Python evaluation returns an in-memory report and does not write a file. Pass
`include_details=True` only when you need private input/gold/result snapshots;
the caller owns their storage. The CLI selects this option for its replayable
artifact. `compare_variants` runs explicit variants sequentially against one
suite; it does not generate prompts or choose a winner automatically.

Inject a PydanticAI `FunctionModel` or a small `StepExecutor` to test routing,
validation, review behavior, and failures offline. The support example's
[`test_support_triage_example.py`](https://github.com/sebastianwessel/foliqant/blob/main/tests/test_support_triage_example.py)
exercises both native decisions and schema output without network access.

After installing development extras, run the existing synthetic example suites:

```sh
uv run --no-sync python -m examples.support_triage.evaluate
uv run --no-sync python -m examples.public_request_mcp.evaluate
uv run --no-sync python -m examples.extracted_request_mcp.evaluate
uv run --no-sync python -m examples.http_workflow.evaluate
```

Support triage checks full pipelines and isolated classification/extraction.
Its sixteen authored inputs cover all three queue categories, missing information,
out-of-catalog requests, multiple active intents, contradictory instructions,
explicit corrections, category words without a request, a withdrawn request, and
a missing referent. Eleven inputs are English and five are German. Extraction
gold checks account versus invoice references, absent values, and unchanged
deadline wording. The HTTP example
reuses these same inputs; the MCP example adds two synthetic request lookups.
The extraction-to-MCP example adds a selected-field binding between steps.
Repeated pipelines and isolated steps do not create additional independent gold.
Its default scripted model verifies wiring. MCP uses a real local stdio server
without a model. HTTP uses an in-process ASGI client. Model-backed examples accept
`--live` to use the explicitly configured model; those small suites remain smoke
checks, not a reviewed quality benchmark.

For training-time held-out datasets, calibration, and artifact audits, use the
separate [model evaluation guide](evaluate-and-audit.md).

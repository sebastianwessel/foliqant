# Run evaluations

Foliqant discovers `evaluation/dataset.json` beside the `config/` directory by
default. If `config/settings.yaml` declares `evaluation.dataset`, that literal
path is used instead. Commands that accept configuration default to
`config/settings.yaml`; use `--config` when needed.

## Check gold offline

Start with:

```sh
foliqant evaluate --check
```

This loads the manifest and case files, validates strict JSON contracts,
configured workflow/flow/step targets, known result roots, metric catalogs, and
source-span ranges. It does not resolve credentials or open model, MCP, or
handler clients. Dynamic output fields and business correctness remain runtime
and reviewer concerns.

## Execute configured suites

Run live suites only when their configured external calls are intended:

```sh
foliqant evaluate \
  --max-concurrency 1 \
  --timeout 300 \
  --output .foliqant/evaluations/baseline.json
```

Suites execute sequentially. Within a suite, the default is one case worker and
a 300-second deadline per case, including custom scoring. `--max-concurrency`
accepts 1 through 64; it changes evaluator scheduling, not provider or runtime
admission limits. `--timeout` must be positive.

`--repeat N` runs every source case N times in case-major order:

```sh
foliqant evaluate --repeat 3 --max-concurrency 1
```

`case_count` is the number of authored cases; `attempt_count` includes repeats.
Repetition measures variation but does not create independent gold.

The CLI writes a new owner-readable report and never overwrites an existing
file. Without `--output`, it creates a unique report below
`.foliqant/evaluations/` beside the settings file. Stdout contains only a safe
summary and path; the report contains private detail.

## Embed the evaluator

Use the same application lifecycle in Python. The following fragment belongs
inside an async `open_application` block where `prepared`, `application`,
`suite`, `metrics`, and `scorers` are already defined. The step-scoped variant
receives already-resolved `classify` input:

```python
from foliqant.evaluation import EvaluationVariant, evaluate

variant = EvaluationVariant(
    name="candidate",
    revision="prompt-2026-09-22",
    run=lambda envelope: application.run_step("support_triage", "triage", "classify", envelope),
    configuration_revision=prepared.configuration_digest,
    workflow="support_triage",
    flow="triage",
    step="classify",
)

report = await evaluate(
    suite,
    variant,
    metrics=metrics,
    scorers=scorers,
    include_details=True,
    max_concurrency=1,
    timeout=300,
)
```

For flow evaluation, omit `step` and call `application.run_flow`. For workflow
evaluation, omit both `flow` and `step` and call `application.run`. Python
evaluation returns an in-memory immutable report and performs no file write.
See [Unit-test integrations](unit-testing.md#test-through-the-public-lifecycle)
for a complete local application lifecycle.

## Replay saved execution offline

Replay applies current gold to saved complete results without constructing
clients or running inference:

```sh
foliqant evaluate \
  --replay .foliqant/evaluations/baseline.json \
  --output .foliqant/evaluations/rescored.json
```

Replay requires the same dataset name, suite and case identities and order,
inputs, configuration and workflow revisions, execution target, and attempt
identities. Gold revision may change. All-error runs can replay; recorded errors
remain errors and never trigger a retry.

Replay can answer “How would revised expectations score these saved outputs?”
It cannot test a changed prompt, model, tool, handler, binding, or route. Saved
case latency and usage remain measurements from the original execution.

## Compare candidate and baseline

Compare compatible detailed reports without loading configuration or clients:

```sh
foliqant evaluate \
  --compare .foliqant/evaluations/candidate.json \
  --baseline .foliqant/evaluations/baseline.json \
  --output .foliqant/evaluations/comparison.json
```

Inputs, gold, scorer revisions, metric catalogs, targets, and complete attempts
must match. Configuration and variant revisions may differ. Comparison reports
improved, regressed, mixed, and unchanged attempts plus aggregate deltas. Its
result is descriptive and applies no release threshold. Replay timing cannot
establish a latency improvement.

## Use evaluation in CI

Run structural checks for private or unavailable live providers:

```sh
foliqant validate
foliqant evaluate --check
```

Add model-free scripted or handler suites when they prove local wiring. Run live
model or MCP evaluation only in an explicitly authorized job with the intended
credentials, network access, private gold, and report retention policy.

CLI exit codes are `0` for passing checks, `1` for gold disagreement, `2` for
invalid input or configuration, `3` for a missing optional dependency, `4` for
runtime failure, and `130` for interruption. Runtime failures return `4` even
when a gold expectation anticipated the failure.

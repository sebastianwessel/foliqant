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
  --timeout 960 \
  --output .foliqant/evaluations/baseline.json
```

Suites execute sequentially. Within a suite, the default is one case worker and
a per-case deadline, including custom scoring, of the configured
`execution.run_timeout` plus 60 seconds for scoring (960 seconds with the
default `run_timeout` of 900), so the evaluator never cuts a run short. A case
that exceeds its deadline is an execution error with `run_timeout`.
`--max-concurrency` accepts 1 through 64; it changes evaluator scheduling, not
provider or runtime admission limits. `--timeout` must be positive.

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

## Resume a long run

A live run over hundreds of cases can take hours. `--checkpoint` names a private
journal: every attempt whose execution ends `completed` or `needs_review` is
appended (and synced) the moment it finishes. Run the same command again with
the same checkpoint to continue after an interruption or a crash:

```sh
foliqant evaluate \
  --max-concurrency 1 \
  --checkpoint .foliqant/evaluations/baseline.checkpoint.jsonl \
  --progress
```

A recorded attempt is reused, instead of calling the workflow, when its suite,
variant, configuration revision and target match and its case ID, repetition
and input are identical. It is scored against the current gold, so a fully
recorded run also rescores revised gold without inference. Failed, cancelled,
timed-out and errored attempts are not recorded and run again. A checkpoint of
another configuration revision is rejected (exit code 2) instead of mixing
results: start a new checkpoint for a changed prompt or model. The report counts
reused attempts in `resumed_attempts` and marks them `resumed`; their latency and
usage are the recorded measurements. The journal contains private inputs and
complete results; keep it with the other private reports.

`--progress` writes one line per finished attempt to stderr with counts and
timing only (suite number, attempts done, resumed, failed, elapsed and an
estimate of the time left). It never prints case IDs or values.

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
    timeout=960,
)
```

Pass `checkpoint=EvaluationCheckpoint(path)` to resume and
`progress=callback` to receive an `EvaluationProgress` after every finished
attempt (`completed`, `total`, `resumed`, `failed`, `elapsed_seconds`,
`remaining_seconds`). One checkpoint file can hold several suites;
`checkpoint.recorded_cases(suite_name, variant)` lists the case IDs it can resume.

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
improved, regressed, mixed, and unchanged attempts plus aggregate deltas, each
rate delta with a paired bootstrap interval (see
[Compare and diagnose](results.md#compare-and-diagnose)). Its result is
descriptive and applies no release threshold. Replay timing cannot establish a
latency improvement.

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
invalid arguments, input or configuration, `3` for a missing optional
dependency, `4` for runtime failure, and `130` for interruption. An evaluation
in which any case failed returns `4`, even when a gold expectation anticipated
the failure: a failed case is an execution error, never a mismatch. The summary
counts such cases in `failures_by_code` and `failures_by_reason`.

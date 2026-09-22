# Typed decisions and evidence strength

This focused example asks five different questions about one customer message:
single-category triage, multiple labels, a predicate, priority, and request units.
Triage, labels, priority, and request extraction concern card-freeze and statement
requests. The dispute question measures a separate dimension.
It runs through the normal in-memory application using the same local Qwen model
as [support triage](../support_triage/README.md). No fine-tuned model is required.

The distinction matters for a message such as “Freeze my card and send my
statement.” Both actions are well supported. Single-category triage abstains
with `multiple_valid_options` and null strength; multiselect returns both actions
with strong support. One request does not become less supported because another
request exists.

An empty allowed collection and a false predicate are substantive answers, so
they still have a reason and non-null strength. An unknown predicate abstains.
The priority question explicitly permits interpreting relative timing: “sooner
rather than later” supports a limited urgency estimate, but does not establish a
deadline. A factual deadline-extraction question must not invent one.

`limited` and `strong` assess support under the question's criteria. They are not
probabilities or guarantees. Criteria that require an explicit fact cannot be
bypassed by returning a limited guess. For collections, strength describes the
weakest returned item; answerability separately describes completeness.

## Inspect and run

Read [workflow.yaml](workflow.yaml) for all five question definitions and
[foliqant.yaml](foliqant.yaml) for the model and execution configuration.
Configure the root `.env` as described in support triage. The evaluation module
loads it explicitly; it never discovers models or adds hidden retries.

From the repository root, check configuration and authored gold without inference:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate
```

This reports `offline_check`. It does not fabricate model responses or claim
model quality. Live evaluation exits unsuccessfully when a gold check fails or the
model returns an inconsistent result. Strict validation rejects such responses;
it does not repair them or retry until a passing answer appears. To measure the four development cases with one request at a time:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate --live
```

Measure two additional validation cases:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate --live --validation
```

Results include every typed answer, reason, strength, issue, route, and available
usage measurement. Five confusion matrices use explicit `[limited, strong, null]`
catalogs. Null is an expected abstention outcome here; absent fields and failed
executions remain separate. Inspect the private report for explanations and any
extra or incorrectly described request units, not just the assertion pass count.
These tiny synthetic suites are examples, not financial accuracy estimates.
The additional cases have been inspected during example development; they are
regression checks, not an untouched benchmark.

`--output` selects a new private report path without overwriting. The existing
JSON evaluation interface can consume the same gold:

```sh
uv run --no-sync python -m examples.decision_evidence.evaluate \
  --write-dataset .foliqant/evaluation/decision-evidence-r2.json
uv run --no-sync foliqant evaluate \
  --config examples/decision_evidence/foliqant.yaml --check
```

To embed the workflow, use `prepare_application`, `open_application`, and
`app.run("decision_evidence", Envelope(payload={"message": "..."}))` as shown in
the [runtime guide](../../docs/getting-started/runtime.md). The graph remains
caller-defined: any unresolved question follows `review`; otherwise it follows
`done`. Strength does not introduce a hidden routing threshold.

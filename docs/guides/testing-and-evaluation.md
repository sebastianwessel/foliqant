# Test and evaluate workflows

Use three different checks: compile workflow configuration offline, test your
integration with local fakes, and evaluate selected variants against explicit
golden cases. A passing unit test does not qualify a model, and a model evaluation
does not test your inbound authentication or persistence layer.

## Test without a model endpoint

Inject a PydanticAI `FunctionModel` or a small `StepExecutor` into the runtime.
Return contract-shaped results and assert routing, output validation, review
behavior, and safe failures. The support example's
[`test_support_triage_example.py`](https://github.com/sebastianwessel/foliqant/blob/main/tests/test_support_triage_example.py)
runs a native decision and a schema-output step without network access.

Run the repository checks from the root project:

```sh
uv run --no-sync pytest
uv run --no-sync mypy src
uv run --no-sync ruff check src tests examples
uv run --no-sync python scripts/generate_schemas.py --check
```

Live model tests require an explicit marker or flag and exact endpoint/model
configuration. Default tests must never discover or call a model.

## Define golden cases in code

Evaluation cases are immutable Python values. Keeping a small synthetic suite in
code avoids committing customer data or a mutable generated dataset:

```python
from foliqant.contracts.envelope import Envelope
from foliqant.evaluation import EvaluationCase, EvaluationSuite, Expectation

suite = EvaluationSuite(
    name="support_triage",
    revision="1",
    cases=(
        EvaluationCase(
            id="explicit_cancellation",
            envelope=Envelope(payload={
                "requestId": "eval-001",
                "message": "Cancel renewal for account C-1049.",
            }),
            expectations=(
                Expectation("completed", "/execution/status", "completed"),
                Expectation(
                    "queue",
                    "/decisions/classify/result/answer/optionId",
                    "cancellation",
                ),
            ),
        ),
    ),
)
```

Paths are RFC 6901 pointers over the public `ExecutionResult`. Exact comparison
preserves JSON scalar types and array order. `comparison="set"` accepts a tuple
and compares only the top-level array while ignoring order and duplicates.
Custom comparison requires an explicitly named, versioned async
`RegisteredScorer`; there is no built-in model judge.

## Run one or more variants

Close over a prepared application's `run` or `run_step` method:

```python
from foliqant.evaluation import EvaluationVariant, evaluate

variant = EvaluationVariant(
    name="local_qwen",
    revision="exact-configured-model-id",
    configuration_revision=prepared.configuration_digest,
    run=lambda envelope: application.run("support_triage", envelope),
)
report = await evaluate(suite, variant, max_concurrency=1, timeout=300)
print(report.to_dict())
```

Use an isolated step variant when you need to distinguish one prompt or tool
from upstream errors. In this mode, the evaluation envelope payload contains the
resolved input keys for that step rather than the whole workflow input:

```python
step_variant = EvaluationVariant(
    name="local_qwen_classify",
    revision="exact-configured-model-id",
    configuration_revision=prepared.configuration_digest,
    run=lambda envelope: application.run_step(
        "support_triage", "classify", envelope
    ),
)
step_report = await evaluate(step_suite, step_variant, max_concurrency=1)
```

For the `classify` step, a case payload would therefore be
`{"message": "Cancel renewal for account C-1049."}`. The result still uses the
public execution shape, so assertions can target
`/decisions/classify/result/answer/optionId`.

`compare_variants` evaluates several explicitly supplied variants against the
same suite. The evaluator does not discover endpoints, retry calls, save files,
or log business values. Each invocation receives a fresh input snapshot.

```python
from foliqant.evaluation import compare_variants

reports = await compare_variants(
    held_out_suite,
    (baseline_variant, candidate_variant),
    max_concurrency=1,
)
for report in reports:
    print(report.variant_name, report.checks.pass_rate, report.review_rate)
```

This compares configurations; it is not an automatic prompt optimizer. Select
changes against a development suite, then confirm the choice once on a separate
reviewed holdout. Real holdouts belong in an access-controlled data workspace,
outside Git, and should be loaded into `EvaluationCase` objects at runtime.

The report records suite and configuration revisions, observed workflow
revisions, case/step status, elapsed time, and measured usage. It includes:

- `report.checks.pass_rate`: passed assertions divided by all assertions;
- `report.checks.coverage`: passed plus failed assertions divided by all
  assertions, excluding unavailable observations from the numerator;
- `report.case_pass_rate`, `failure_rate`, and `review_rate`;
- per-case and per-step measurements.

Missing, skipped, and errored checks remain in the pass-rate denominator. Reports
contain IDs, paths, outcomes, and measurements, but omit inputs and expected or
actual business values. Caller-supplied model revisions identify configuration;
they do not prove the provider served particular weights.

## Evaluate each example

From the repository root after installing the development extras:

```sh
uv run --no-sync python -m examples.support_triage.evaluate
uv run --no-sync python -m examples.public_request_mcp.evaluate
uv run --no-sync python -m examples.http_workflow.evaluate
```

Support triage evaluates three pipeline cases, classification in isolation, and
extraction in isolation. The cases cover cancellation, a billing dispute and
insufficient information. The default injects a scripted `FunctionModel` through
`RuntimePlugins`; it measures routing and validation wiring, not model quality.
The MCP suite runs the real local stdio server, with no model. The HTTP suite
reuses the support gold through an in-process ASGI client without opening a port.
Each command exits nonzero if an assertion fails.

To measure the explicitly configured local model, add `--live` to the support or
HTTP evaluation command. Calls remain sequential. Do not run both evaluations
or data generation concurrently against a capacity-limited local server.

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.evaluate --live
```

Keep the authored expected results separate from scripted outputs. Repository
tests deliberately change a gold value and verify that the example exits with a
failure. Small synthetic suites are smoke checks, not a quality benchmark or a
substitute for a reviewed holdout. Store private suites and reports under the
ignored `.foliqant/evaluations/` directory or an explicit private workspace.

For training-time held-out datasets, calibration, threshold selection, and
artifact audit, use the separate [model evaluation guide](evaluate-and-audit.md).

# Prompt layout and security experiments

`prompt_layout.py` is an opt-in research harness, not a production configuration
feature. It reuses the public workflow executor, evaluator, golden datasets and
scorers. It neither creates training data nor adds cross-step conversation history.
No inference runs unless `--live` is present.

## Fixed comparisons

Each invocation runs one variant in its own process, using full workflow execution
only. Do not launch variants concurrently against the same model server.

| Variant | Intervention |
| --- | --- |
| `baseline` | Current production rendering and hardening, unchanged |
| `policy_control` | Remove only the appended untrusted-input policies; keep business instructions, compiler-authored criteria, decision output contract and provider output schema |
| `questions_first` | Move the decision JSON object's `questions` key before `state`; preserve all content, arrays, source order, strings and security instructions |
| `common_first` | Move the complete common instruction blocks before business instructions; retain their contents and leave user JSON ordering unchanged |

The policy control retains task-specific security guidance where authored. It is
therefore a narrow ablation, not a deliberately unrestricted or historically
representative vulnerable system. The two ordering changes are separate variants;
the harness never combines them or removes duplicated instructions.

Private renderer patches exist only within a guarded context manager and are
restored on success, error or cancellation. Production has no configuration knob
to disable its input policy. The sole runtime refactoring is a private rendering
helper whose baseline bytes match the preceding inline serialization.

## Prepare a four-case pilot

Run from the repository root. `MODEL_ID` and `MODEL_BASE_URL` may come from the root
`.env` through `examples.common`; explicit process values take precedence. The
harness maps those values to the selected example's declared environment references.
No environment file or endpoint identity is printed.

```sh
MODEL_ID=offline MODEL_BASE_URL=http://127.0.0.1:1/v1 \
  uv run --no-sync python -m research.experiments.prompt_layout \
  --dataset security --variant baseline --selection pilot
```

This offline check validates targets, controls and source hashes without opening
clients or creating output files. It uses a placeholder endpoint intentionally.
For live execution, supply the actual local Qwen model and endpoint, and a new
private output directory:

```sh
uv run --no-sync python -m research.experiments.prompt_layout \
  --dataset security --variant baseline --selection pilot --live \
  --directory .foliqant/experiments/prompt-layout/security-baseline-pilot
```

Choose `--dataset security` or `evidence`, and `--selection pilot` or `full`:

- Security pilot: clean/attacked pairs for `stolen_wallet` (EN) and
  `double_merchant_charge` (DE), four cases and normally eight requests. This covers
  source override and XML attachment attacks; it does not cover every attack family.
- Security full: all 20 clean/attacked inputs from 10 families, normally 36 requests
  because four review inputs stop before confirmation.
- Evidence pilot: `two_requests`, `two_requests_de`, `tentative_document`, and
  `tentative_document_de`, four cases and four requests. The two language pairs
  share families and are not independent samples.
- Evidence full: all 15 cases in the `typed_support` workflow suite. Flow, isolated
  step, separate predicate and reserved validation suites are excluded.

Case IDs and order are explicit and frozen in the manifest. There is no arbitrary
first-N truncation. Pilot results are smoke measurements, not statistical evidence.
Inspect the baseline pilot before running challengers or the full paired suite.

## Bounds, freezing and interruption

The selected example must already use native output, one local compatible model,
concurrency one, low reasoning effort, temperature 0.1 and max output 8192 tokens.
The harness rejects drift from these controls before opening clients. Each model
request has a 300-second deadline; workflows have 610 seconds and evaluator cases
620 seconds. It never changes server settings or launches the server.

Before the first request, a new owner-readable output directory receives:

- `manifest.json`: variant, exact case IDs, suite fingerprint, static instruction
  hashes, renderer hashes, source/config/gold hashes, dependency versions, and
  hashed model/endpoint identities;
- `snapshot/`: exact runtime source, relevant example configuration, gold manifest and its exact case-file references,
  experiment/scorer helpers, lockfile and project metadata;
- after evaluation, `report.json`: the full standard evaluator report, including
  private inputs, gold, returned assessments, usage and cache counters;
- `completed.json`: a receipt binding manifest and report hashes and final status.

Dynamic downstream prompts depend on model outputs and cannot be known before the
first request. Their input bindings, templates, renderer and source cases are
frozen; full downstream inputs/results remain in the standard report. The manifest
does not identify loaded weight hashes, tokenizer/server revisions or server cache
state: capture those separately when interpreting real measurements.

Keep runtime source, selected example configuration, gold and experiment Python
unchanged during a run. The harness checks source bytes before every case. Progress
on stderr contains only completed/total counts and the timeout-stop flag. The
standard final output contains counts, status and the private directory path.

After a timeout, no further model requests are issued; remaining cases stay in the
report as errors, preserving the denominator. A timeout may leave server generation
running despite client cancellation. Check backend health before another command.
Cancellation or interruption leaves an incomplete directory; no automatic resume
or overwrite is supported. Use a new directory after diagnosis.

`--skip-completed` only skips an already completed report when current manifest,
report and every saved source byte match exactly. It never resumes a partial run.
A failed completed report remains a failure when skipped. There is no provider
response cache or hidden answer reuse.

## Interpretation and verification

Compare clean and attacked cases together and prioritize answer correctness,
review decisions and evidence strength before latency or token counts. The shared
reports retain provider cache counters when supplied; unknown counters remain
unknown. Questions-first or common-first rendering is not proof of a cache hit,
and a cache hit is not proof of a quality improvement. Variant order and a warmed
backend can confound timings; this harness does not claim a cold-cache control.

All cases are authored synthetic examples. No results from this harness establish
production prompt-injection resistance, independently reviewed gold, or general
model accuracy. Exact-field checks do not semantically score free-text reasons.

Offline regression command:

```sh
uv run --no-sync pytest -q tests/test_prompt_layout_experiment.py tests/test_model_executor.py
uv run --no-sync mypy research/experiments src/foliqant/adapters/models/executor.py
```

Tests use deterministic local doubles to verify variant isolation, exact JSON
preservation and unchanged message roles, snapshot publication before requests,
timeout stopping, private permissions, immutable reports and strict completed skips.
They do not contact a model endpoint.

# Paired prompt-security evaluation

This runnable example tests whether a decision and a later extraction step keep
customer evidence separate from embedded instructions and earlier model claims.
It uses the public application and evaluation APIs, with no separate workflow
runner. It does not perform banking actions.

The committed [`evaluation/dataset.json`](evaluation/dataset.json) is the editable
source of truth for suite targets, inputs and expected outputs. Workflow and flow
suites share [cases/workflow.json](evaluation/cases/workflow.json); isolated-step
cases remain inline in the manifest. Together they contain **10 synthetic
business families: five English and five German**, each with one clean and one
attacked input. The families were authored separately, not generated as independent
translations. They are not customer data, held-out samples, or human-adjudicated
gold. No model creates their expected answers.

The pairs cover customer requests to freeze a card, obtain a statement, update an
address and dispute a charge. Attacks include source overrides, fake XML and role
delimiters, and poisoned earlier model results. Two families include benign quoted
imperatives that must not become active requests. A missing-referent family and a
simultaneous contradictory-instructions family must produce review, retaining the
decision assessment and skipping confirmation.

## Run offline

From the repository root:

```sh
uv run --no-sync python -m examples.security_evaluation.evaluate --scope all
uv run --no-sync foliqant evaluate --config examples/security_evaluation/config/settings.yaml --check
```

The default has **no model connection**. `offline.py` uses explicit scripted
responses from `fixtures.py`; these validate wiring, JSON preservation, routing
and scoring, not security quality. Altering the JSON gold changes scoring without
changing the scripted answers. The regression tests include such a negative
control.

## Opt into local inference

Set `MODEL_ID` to the model served by your local OpenAI-compatible endpoint and
`MODEL_BASE_URL` to its `/v1` URL. The example reuses `examples.common` to read the
repository root `.env`; explicit process values take precedence. A local Qwen
server supporting native structured output is the intended provider. Credentials,
if your server needs them, belong in local settings, never in gold or Git.

```sh
MODEL_ID='your-local-qwen-model' MODEL_BASE_URL='http://127.0.0.1:1234/v1' \
  uv run --no-sync python -m examples.security_evaluation.evaluate --live --scope workflow
```

Both provider and evaluation concurrency are one. `--repeat 2` repeats each case
without changing its gold; repetitions are not new independent cases. The command
halts further requests after a timeout, while recording remaining cases as errors.
Check backend health and that earlier generation stopped before starting another
live command. There are no automatic retries. A request can take up to 300 seconds;
a whole two-step execution can take up to 610 seconds.

Use `--scope workflow` (default), `flow`, `assess`, `confirm`, or `all`:

| Scope | Target cases | Purpose |
| --- | ---: | --- |
| workflow | 20 | Full decision, review gate and confirmation |
| flow | 20 | Same flow with its explicit input envelope |
| assess | 20 | Isolated decision; only the selected original message is supplied |
| confirm | 16 | Isolated confirmation with a fixed authored upstream assessment |

`all` means **76 target cases drawn from the same 20 paired inputs**, not 76
independent security examples. With one repetition, successful execution makes
108 model requests. Workflow alone makes 36. Attachment/prior attacks do not
change isolated decision input because those channels are explicitly not sources;
those pairs test isolation, not independent attack attempts at that step.

The decision receives only `original_message`. Confirmation receives that original
message, the complete derived assessment, an attachment and the prior model claim,
all encoded as JSON user-message data. Its instructions require checking the
assessment against the original source. Expected fields include action, an exact
source reference and `evidence_origin: original_message`. The origin field is a
checkable assertion, not proof of a model's actual reasoning.

## Inspect or edit results

Reports default to ignored `.foliqant/evaluations/` and include private input/output
details, per-check failures and classification matrices. `--output` accepts a new
private report path; existing files are never overwritten. `--write-dataset`
exports a private copy of the committed dataset without inference. Edit the
manifest or its referenced case JSON directly to change gold; `fixtures.py` is only an independent
wiring double and is not used to regenerate expectations.

The command exits nonzero when checks fail. Inspect paired clean and attacked
results together: a clean-case failure cannot be attributed to the attack. The
automatic checks cover dispositions, answerability, issue sets, evidence strength,
selected action and exact extracted references. They do not semantically judge
free-text reasons or establish general prompt-injection resistance. Small synthetic
results, provider token usage, cache hits, and latency are not production security
acceptance or statistical evidence of generalization.

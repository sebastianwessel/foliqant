# Support triage with local Qwen

This example turns a synthetic customer email into two bounded operations. A
runtime decision selects the queue from the supplied message, then a schema-output
step extracts the current requested action as a concise verbatim source span,
the full deadline wording, and the account reference. The
workflow keeps unresolved classifications in review and can attach an explicit
`misc` fallback without changing the original answer.

Install the runtime with the OpenAI-compatible adapter:

```sh
uv sync --locked --extra openai
```

The example deliberately reuses the local Qwen settings already used by model
curation. Define these non-secret endpoint and model keys in the repository `.env`:

```dotenv
FOLIQANT_CURATION_ENDPOINT_URL=http://127.0.0.1:8000/v1
FOLIQANT_CURATION_MODEL=incoai/Qwen3.8-27B-Splash
```

The example’s `foliqant.yaml` binds those values with `$NAME` references.
Reasoning, token limit, temperature and timeouts live in that YAML profile; edit
the profile to change shared behavior. The extraction step overrides only
`max_tokens` to 4096; other options inherit the profile. Curation has its own recipe.

The model ID is mandatory. Foliqant does not discover a served model or choose a
fallback. The committed script performs no call unless `--live` is present:

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.run --live
```

Success prints one execution result. The extracted fields are in `payload`; the
validated queue answer, concise reason, and evidence strength are in `decisions.classify`. This is an
in-process, in-memory run. It provides no HTTP server, authentication, storage,
retry queue, or model-quality guarantee. The default test uses a local
`FunctionModel` and never contacts the configured endpoint.

## Evaluate the pipeline and individual steps

```sh
uv run --no-sync python -m examples.support_triage.evaluate
```

The default uses scripted `FunctionModel` responses without contacting a model.
It checks sixteen independently authored synthetic pipeline cases: all three queue
labels, missing actions, clear out-of-catalog requests, two simultaneous active
queues, unresolved conflicting instructions, an explicit correction, category
words without a request, a withdrawn request, and a missing referent. Eleven
inputs are English and five are German; category keys remain English. It checks
classification on all sixteen inputs and extraction on the six single-action inputs.
Extraction retains source-language action wording and deadline operators such as
`by` and `bis`. Expected answers live in `evaluate.py`; scripted outputs in
`offline.py` only exercise wiring and validation. A negative-control test proves
mismatched gold fails.

To measure the configured Qwen model sequentially on the same cases:

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.evaluate --live
```

Both commands return nonzero on failed expectations. Each run saves a new private
report by default and prints its path. Reports contain outcomes, latency, usage
and revisions, without input/expected/actual business values in console output.
These small synthetic suites are smoke checks, not accuracy claims. Store real
reviewed cases and reports under ignored `.foliqant/evaluations/` and preserve
an untouched holdout when optimizing prompts.

Use `--repeat 3` to run three independent attempts per authored case. Repetition
can expose variation, but it does not create more distinct gold cases or establish
model quality.

Export the authored cases in the shared evaluation dataset format, then validate
that file without opening a model client:

```sh
uv run --no-sync python -m examples.support_triage.evaluate \
  --write-dataset .foliqant/evaluation/support-triage-r9.json
uv run --no-sync foliqant evaluate --config examples/support_triage/foliqant.yaml --check
```

Choose a report path for the scripted run and rescore its saved outputs:

```sh
uv run --no-sync python -m examples.support_triage.evaluate \
  --output .foliqant/evaluation/support-report.json
uv run --no-sync foliqant evaluate --config examples/support_triage/foliqant.yaml \
  --replay .foliqant/evaluation/support-report.json \
  --output .foliqant/evaluation/support-rescored.json
```

Both exports require a new path. `--write-dataset` performs no inference. The
configured dataset is loaded only for evaluation; ordinary workflow startup does
not require the file. Console reports omit case/check details. The private
artifact retains inputs, expected and actual values, model reasons within
returned results, and safe mismatch reasons. Queue classification reports include
an ordered confusion matrix; the ten review cases have no queue gold and are
counted as excluded. Separate effective-category and origin metrics check six
model selections and eight `misc` fallbacks; conflict/multiple-intent cases have no
selection. Missing-action and known out-of-catalog cases remain separate English
and German gold scenarios, but both use `no_supported_answer` and the same review
route. Isolated reports identify `classify` or `extract` explicitly.

The evidence-strength metric includes explicit `null` alongside `limited` and
`strong`. All supported classifications in this example have explicit requests
and strong support; this suite does not test a limited classification. See
[typed decisions and evidence](../decision_evidence/README.md) for a permissible
limited interpretation, multiple labels, false predicates, and empty collections.
A strength rating is a model assessment, not a correctness guarantee.

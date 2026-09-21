# Support triage with local Qwen

This example turns a synthetic customer email into two bounded operations. A
native decision selects the queue from quoted evidence, then a schema-output
step extracts the requested action, deadline, and account reference. The
workflow routes an unanswerable classification to review.

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
that one profile to change runtime behavior. Curation has its own recipe.

The model ID is mandatory. Foliqant does not discover a served model or choose a
fallback. The committed script performs no call unless `--live` is present:

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.run --live
```

Success prints one execution result. The extracted fields are in `payload`; the
validated queue answer and its evidence are in `decisions.classify`. This is an
in-process, in-memory run. It provides no HTTP server, authentication, storage,
retry queue, or model-quality guarantee. The default test uses a local
`FunctionModel` and never contacts the configured endpoint.

## Evaluate the pipeline and individual steps

```sh
uv run --no-sync python -m examples.support_triage.evaluate
```

The default uses scripted `FunctionModel` responses without contacting a model.
It checks three pipeline cases (cancellation, billing dispute, missing details),
the classification step separately, and extraction separately. Expected answers
are authored in `evaluate.py`; the scripted outputs in `offline.py` only exercise
wiring and validation. A negative-control test proves mismatched gold fails.

To measure the configured Qwen model sequentially on the same cases:

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.evaluate --live
```

Both commands return nonzero on failed expectations. Reports contain outcomes,
latency, usage and revisions, without input/expected/actual business values.
These small synthetic suites are smoke checks, not accuracy claims. Store real
reviewed cases and reports under ignored `.foliqant/evaluations/` and preserve
an untouched holdout when optimizing prompts.

Export the authored cases in the shared evaluation dataset format, then validate
that file without opening a model client:

```sh
uv run --no-sync python -m examples.support_triage.evaluate \
  --write-dataset .foliqant/evaluation/support-triage.json
uv run --no-sync foliqant evaluate --config examples/support_triage/foliqant.yaml --check
```

Save a complete private report from the scripted run and rescore its saved outputs:

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
artifact retains inputs, expected and actual values, model explanations within
returned results, and safe mismatch reasons. Queue classification reports include
an ordered confusion matrix; the unclear case has no queue gold and is counted
as excluded. Isolated reports identify `classify` or `extract` explicitly.

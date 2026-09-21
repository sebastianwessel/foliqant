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
curation. Define these non-secret keys in the repository `.env`:

```dotenv
FOLIQANT_CURATION_ENDPOINT_URL=http://127.0.0.1:8000/v1
FOLIQANT_CURATION_MODEL=incoai/Qwen3.8-27B-Splash
FOLIQANT_CURATION_REASONING_EFFORT=low
FOLIQANT_CURATION_MAX_TOKENS=8192
FOLIQANT_CURATION_TEMPERATURE=0.1
FOLIQANT_CURATION_TIMEOUT_SECONDS=300
```

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

To run two synthetic golden cases sequentially against the same configured
model, use:

```sh
PYDANTIC_AI_NO_BANNER=1 \
  uv run --no-sync python -m examples.support_triage.evaluate --live
```

The cases live in Python code rather than a checked-in dataset file. The report
contains case IDs, paths, outcomes, timing, usage, and revisions; it omits input,
expected, and actual business values.

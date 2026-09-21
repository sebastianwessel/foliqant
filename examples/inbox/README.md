# Model-enabled inbox example

This minimal embedded application compiles two Markdown decision steps and uses
the configured model through the service's bounded PydanticAI adapter. Native
decision validation checks the returned choice or predicate, answerability, and
quoted evidence before deterministic routing. Unresolved decisions and blocked
access go to review. The runner is embedded and nondurable; this example does not
claim restart recovery, durable budgets, authentication, or model qualification.

Prepare a production-style service environment with only the required provider
extra, then copy and edit the deployment profile:

```bash
uv sync --project service --locked --no-dev --extra openai
cp examples/inbox/profiles.example.json examples/inbox/profiles.json
```

Set the exact model ID and base URL in `profiles.json`. If the endpoint requires
a key, set `api_key_env` to its environment variable name and put the value in
the process environment or a root `.env` copied from
`examples/inbox/.env.example`. The process environment takes precedence. Pass
the copied and edited file. `profiles.json` is ignored, and secrets never belong
directly in a profile.

Running without `--profiles` prints usage and performs no model call. Once the
edited profile points to a running, JSON-Schema-capable server, run:

```bash
uv run --project service --no-sync python examples/inbox/run.py \
  --profiles examples/inbox/profiles.json
```

Success prints one JSON execution result. Its `execution.status` is `completed`
or `needs_review`, `decisions` contains the validated native decision results,
and `metadata` contains the host-supplied demonstration identity. Failures print
only a fixed safe JSON error to stderr. Dynamic IDs, revisions, decisions, and
usage values depend on the run; the top-level shapes are:

```json
{"payload": {}, "metadata": {}, "decisions": {}, "execution": {"id": "...", "workflow": "inbox_triage", "revision": "...", "status": "completed", "usage": {}}}
{"error": {"code": "invalid_configuration", "message": "The workflow configuration is invalid."}}
```

The committed tests use a `FunctionModel`; they never contact this endpoint or
perform real inference.

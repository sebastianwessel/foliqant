# Deploy and operate an embedded workflow

Deploy Foliqant as part of the Python application that calls it. It is an
in-memory library, not a standalone queue or HTTP service. The host controls
incoming transports, authentication, authorization, persistence, idempotency,
and process supervision.

## Package the configuration and dependencies

Use Python 3.12 and install the reviewed wheel plus only the adapter extras
your configuration needs (`openai`, `anthropic`, `azure`, `google`, `bedrock`, `mcp`, and/or
`telemetry`). The repository's tested set is locked in `uv.lock`; see
[installation](../getting-started/runtime.md). Ship `config/settings.yaml`,
workflow definitions, local schemas, and prompt files together as one reviewed
artifact. Paths resolve beneath the configuration root, so include their
relative layout. Do not put credentials in that artifact.

Set required environment variables in the host environment or adjacent
`config/.env`. Explicit `$NAME` references in deployment fields resolve when
the application opens; process values take precedence over `.env`. Preparation
can validate structure offline, while missing required environment values fail
at startup. [Configure environment](../configuration/environment.md) explains
the supported fields and resolution rules.

## Open once and shut down cleanly

At startup, call `prepare_application(config_path, handlers=..., strict=True)`;
on `CompilationError`, log each entry of `error.problems` (code, location,
field, message, hint) and exit non-zero. Then enter
`open_application(prepared, environment=os.environ, plugins=...)` once per
process. Handler **contracts** are declared in `settings.yaml` and reviewed with
the configuration; handler **implementations** and tool authorizers are
registered in application code, because configuration cannot import Python
functions. `open_application` fails with `missing_handler_registration` when a
declared handler has no registration, so a deployment cannot start half-wired.
Run `foliqant validate --strict` in CI to fail the build on warnings, and
`foliqant explain --format mermaid --all --output docs/workflows.md --check` to
keep the generated graph documentation current. Hold the async
context while requests are accepted and leave it during service shutdown.
The context drains owned work before closing clients. A forced process kill
loses unfinished in-memory executions, so the host needs durable coordination
if recovery after a crash matters.

Configured admission capacity, deadlines, step visits, model/tool attempts, and
collection item caps bound work *within one process*. They are not global rate
limits or service-level guarantees. Scale-out hosts need their own shared
admission policy if that matters. See [Configure limits](../configuration/limits.md).

## Observe safely

The result carries a run ID, configuration revision, per-flow records, elapsed
times, and measured usage. Token measurements can be `null` when a provider did
not report them. Do not add parent and child usage values together. Optional
telemetry emits restricted labels; keep payloads, prompts, identities,
credentials, raw exceptions, and customer evaluation data out of logs. Store
full results or evaluation reports only under your application's data policy.
See [observability](observability.md) for telemetry setup,
[runtime configuration](../reference/runtime-configuration.md) for exact fields,
and [Handle errors](errors.md) for reconciliation and retry decisions.

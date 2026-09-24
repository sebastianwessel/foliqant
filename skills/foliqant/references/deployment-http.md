# Deployment, CLI, and HTTP example reference

Use the installed `foliqant.contracts.deployment` models,
`foliqant.contracts.schemas.runtime_schemas()`, and current CLI help when
checking configuration. The package owns an in-memory application API; HTTP is a
host concern outside package transport and core.

## Contents

- [Configuration and preparation](#configuration-and-preparation)
- [CLI](#cli)
- [Integrate the Python lifecycle](#integrate-the-python-lifecycle)
- [Register business functions](#register-business-functions)
- [Interpret results and failures](#interpret-results-and-failures)
- [Identity and tool permission](#identity-and-tool-permission)
- [HTTP example](#http-example)
- [Package and deploy](#package-and-deploy)
- [Deliverables and checks](#deliverables-and-checks)

## Configuration and preparation

The default path is `config/settings.yaml`. When `workflows` is omitted,
preparation discovers only immediate nonhidden
`config/*/workflow.yaml` files. Explicit workflow maps remain available.
Workflow, flow, step, and schema paths stay within their allowed configuration
directories. The optional evaluation dataset path is a literal path relative
to the settings file.

`prepare_application` compiles offline without reading `.env`, resolving
credentials, importing configured code, or opening SDK clients.
`open_application` reads the selected configuration directory's `.env`,
overlays the supplied environment, resolves marked fields, and opens owned
adapters.

Environment expansion applies only to marked deployment fields. Never expand
workflow instructions, prompt templates, bindings, schemas, documents, or
customer values. Inline credentials are forbidden; use complete environment
references.

Conventional workflow, flow, and step lookup resolves definitions only.
Declared flow transitions and ordered step lists determine execution. Missing or
ambiguous conventional files fail compilation. Explicit paths and inline
definitions are supported for intentional customization.

## CLI

`foliqant init DEST` creates a runtime workflow project without overwriting a
path.
`validate`, `explain`, `doctor`, `run`, and configured `evaluate`
default to `config/settings.yaml`; `--config PATH` selects an exact
alternative without parent-directory search.

`validate`, `explain`, `doctor`, and `evaluate --check` are offline and list
compiler `diagnostics`. `validate --strict` fails on any warning; use it in CI.
`explain --format mermaid|dot --workflow ID` prints the graph (flows, routes
with their conditions and operands, dashed review edges, dotted
collection/retry calls, repeat self-loops such as `repeat ≤ 2 until status
equals found`) as text for reviews. Generate docs with
`foliqant explain --format mermaid --all --output docs/workflows.md`: one
Markdown section per workflow with its start, output, diagram and diagnostics.
Commit the file and keep it current in CI with the same command plus
`--check` (exit `1` when stale). `run` reads one bounded envelope and returns
one foreground result.

Output streams: standard output carries exactly one JSON object (the result or
the failure status with `error`, `problems` and `diagnostics`) or the graph
text; standard error carries readable lines, one per configuration problem:
`<file>:<line>:<column>: <code> at <field>: <message> (hint: ...)`. Exit codes:
`0` success (also `needs_review`), `1` gold mismatch or stale `--check`
output, `2` invalid arguments, input or configuration (warnings too under
`--strict`), `3` missing optional dependency, `4` runtime failure, `130`
interruption.
`evaluate --replay` and `--compare` operate on saved artifacts without
opening providers. Normal evaluation runs its configured pipeline, flow, or
operation targets.

Caught errors use fixed safe messages and optional sanitized locations. Never
expose authored values, credentials, prompts, or raw exceptions.

Host startup pattern: call `prepare_application(path, handlers=..., strict=True)`
once at startup, before accepting work. On `CompilationError`, log every entry
of `error.problems` field by field (`code`, `level`, `location.path`,
`location.line`, `location.column`, `field`, `message`, `hint`; `str(error)`
renders one line each) and exit non-zero. `open_application` refuses a strict
preparation that carries warnings, so a later code path cannot bypass it.

Handlers are declared under `handlers` in `settings.yaml`, so the generic CLI
compiles, explains and checks workflows with handlers without host Python.
`run` and ordinary `evaluate` execute handlers and therefore fail with
`missing_handler_registration`; use the host's entry point, which passes the
registrations to `prepare_application(path, handlers=handlers)`. Never permit
configuration-driven imports to work around it.

## Integrate the Python lifecycle

For a one-shot use case, this is the complete lifetime pattern. It assumes the
configured workflow is `support_intake` and uses no custom handlers:

```python
import asyncio
import os
from pathlib import Path

from foliqant import Envelope, ExecutionResult, open_application, prepare_application


async def process_message(message: str) -> ExecutionResult:
    prepared = prepare_application(Path("config/settings.yaml"))
    async with open_application(prepared, environment=os.environ) as app:
        return await app.run("support_intake", Envelope(payload={"message": message}))


if __name__ == "__main__":
    result = asyncio.run(process_message("Please explain the charge on my invoice."))
    # Hand the complete result to the calling application; avoid logging its data.
```

For a server, move preparation and the `open_application` context into startup
and shutdown. Store the open application in server state and await
`app.run(workflow_id, envelope)` per request. Do not call the one-shot function
above per HTTP request: it would reopen clients. Runtime invocation state is
isolated; shared host handlers and dependencies must also avoid mutable
request-specific instance state. Await I/O, preserve cancellation, and keep
blocking SDK/CPU work off the event loop.

## Register business functions

Configuration selects declared functions by name. Declare the contract in
`settings.yaml`:

```yaml
handlers:
  finalize:
    input_schema:
      type: object
      properties:
        review_required:
          type: boolean
      required:
        - review_required
      additionalProperties: false
    output_schema:
      type: object
      properties:
        disposition:
          enum:
            - ready
            - review
      required:
        - disposition
      additionalProperties: false
    effect: read
```

The host registers the typed async callable before activation. This
deterministic handler builds a business result and marks review explicitly,
with the reason that selects an issue-specific review route:

```python
from pathlib import Path

from foliqant import prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json
from foliqant.ports.execution import StepContext


async def finalize(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    review = inputs["review_required"] is True
    payload = {"disposition": "review" if review else "ready"}
    issues = ("no_supported_answer",) if review else ()
    return StepOutcome(freeze_json(payload), needs_review=review, unresolved_issues=issues)


handlers = {"finalize": HandlerRegistration(finalize)}
prepared = prepare_application(Path("config/settings.yaml"), handlers=handlers)
```

Registration schemas are optional; when a host generates them from its models,
passing them makes preparation verify that code and declaration agree
(`handler_contract_mismatch` otherwise). Its step binds a boolean selected by
your business policy:

```yaml
type: handler
handler: finalize
input:
  review_required:
    pointer: /payload/review_required
```

Project `/steps/finalize/result` as the flow output when this step is named
`finalize`; project that flow result as the workflow output. Without those
projections, input remains the default output. For a multi-request process,
replace this simple boolean policy with an explicit check of the complete plan
and collection ledger. Handler return schemas and `needs_review` are independent:
a payload saying `review` alone does not set the execution status. Do not write
handlers that only compute a routing key or pick a branch result: use `route`,
`cases`, `when` and `first_of` in configuration.

## Interpret results and failures

| Boundary/outcome | What the host receives | Required handling |
| --- | --- | --- |
| Offline compilation | `PreparedApplication` or `CompilationError` | Fix configuration and registrations before opening adapters. |
| Invalid admission input, unavailable capacity, or another pre-run failure | `ServiceError` can be raised before a result exists | Map its canonical code and safe message into the host protocol. |
| Successful work | `ExecutionResult`, `execution.status == "completed"` | Consume `payload`; retain `flows` when the use case needs evidence or intermediate results. |
| Business review | Result with `needs_review` status | Apply the explicit review policy; this is not a provider outage or retry request. |
| Admitted technical failure | Result with `failed` status and `execution.error` | Preserve the full result, including any partial collection ledger. Do not report a benign default payload as success. |
| Caller cancellation | Cancellation propagates; a result is not guaranteed | Preserve cancellation and let host policy decide reconciliation. |

Inspect `execution.status` before business payload. Serialize a complete result
with `result.model_dump(mode="json")`. `app.run_flow` and `app.run_step` also
return `ExecutionResult`, but accept already-resolved boundary input and run an
isolated scope; they are useful for testing, not implicit workflow composition.

Catch `ServiceError` at the host boundary; use `error.code.value`, its safe
`str(error)`, and `error.retryable`. For returned failures, use
`result.execution.error`. Do not infer retry permission from error text or
automatically rerun a workflow: timeout/cancellation does not prove remote work
stopped. Configured provider retry policy is narrower than whole-run retry.

## Identity and tool permission

Optional tenant and principal IDs are invocation context. The host authenticates
and authorizes callers before invoking Foliqant. MCP access remains separately
protected by compiled allowlists, declared read-only effects, schema validation,
and an optional host `ToolAuthorizer`.

An MCP profile's optional `auth` value names a trusted credential hook. Supply
the same key through `RuntimePlugins.mcp_credentials`; the value is an
`McpCredentialProvider`, not a token from configuration. HTTP sessions request
fresh caller-scoped authorization from that provider. Stdio profiles cannot name
an auth hook.

## HTTP example

The HTTP example may decode one envelope, invoke the same foreground application
call, and return its `ExecutionResult`. The host owns authentication,
authorization, rate control, idempotency, and disconnect handling.

Do not add accepted receipts, detached work, result lookup, cancellation
endpoints, or durability claims. Use the generated envelope and execution-result
schemas rather than transport-specific duplicate DTOs.

For an async HTTP host (for example, Starlette installed as an application
dependency), wire these concrete boundaries:

1. Enter `open_application` in the server lifespan with the prepared host
   registrations; store the application in server state.
2. Check content type and bound streamed request bytes and read time before
   decoding. Use `MAX_ENVELOPE_BYTES` and `decode_envelope` from
   `foliqant.contracts.decoding` for an envelope-shaped JSON API.
3. For a business-shaped API, validate the host request and explicitly construct
   `Envelope(payload=...)`; do not pass the HTTP request or all headers as input.
4. Apply the host's chosen identity policy. An unauthenticated demo should reject
   supplied tenant/principal metadata rather than treating it as verified.
5. Await `app.run` and serialize its complete result. Choose and document HTTP
   status mappings for both raised errors and returned failed results. A valid
   `needs_review` result can remain a successful HTTP response.
6. Leave the lifespan on shutdown. Define disconnect handling at the host boundary;
   it does not imply the model or tool has stopped remotely.

## Package and deploy

- Ship the Python application, reviewed package dependency plus selected adapter
  extras, and the complete relative config/prompt/schema tree together. For a
  container, use an explicit working directory or absolute settings path; the CLI
  does not search parent directories. Supply the host's actual start command.
- Keep secrets in the deployment environment or adjacent local `.env`, never in
  the image/config artifact. Set required profile values and install the matching
  provider/MCP extras. Evaluation datasets and reports need not ship with runtime.
- Compile with actual handler registrations during build/tests; open adapters
  once per process at startup. Only signal readiness after that startup succeeds.
  Opening does not prove provider credentials or remote availability; do not add
  model inference or `/models` discovery as an implicit health probe.
- Admission and configured budgets are per process. Extra replicas multiply
  capacity; host-level shared limits are an application decision. Tune run and
  provider deadlines together using observed workload rather than increasing one
  timeout or retry count blindly.
- Stop accepting requests, drain owned work, and close the async context on
  shutdown. In-flight state is lost on forced exit. Only design external durable
  coordination if the use case requests it; it is not part of this core.
- Use optional telemetry with safe labels and known/unknown usage preserved.
  Full results contain business data; retain them only under the application's
  policy. A projected payload is insufficient to diagnose partial child failures.
- Telemetry checklist: when the host already runs OpenTelemetry, pass its
  provider with `RuntimePlugins(tracer_provider=...)` (never together with
  `install_global_telemetry=True`) so runtime spans join host traces without a
  second exporter; otherwise pass the inbound W3C carrier as `transport_trace`; log
  `result.execution.id` and `result.execution.trace` with host records; forward
  `StepContext.trace` on handler outbound calls; configure
  `configure_logging` for JSON logs with `trace_id`/`span_id`; alert on
  `run_failed`, unexpected `repeat_stopped` (`exhausted`) rates and
  `condition_type_mismatch`; enable `telemetry.conditions` only while
  debugging routes.

## Deliverables and checks

For a host integration, deliver its request-to-envelope mapping, one awaited
application call, result serialization, and any identity policy required by the
use case. Validate with `foliqant validate --strict` and review
`foliqant explain --format mermaid`; open the application with registered
`prepare_application` in tests. Test the host with scripted
adapters or handler-only workflows for success, review, raised errors, returned
failures, and shutdown. Provide the config/environment locations, dependency and
start commands, and actual test outcomes. State which operational controls the
host supplies; do not claim that the in-memory library supplies them.

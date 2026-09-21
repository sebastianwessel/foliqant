# In-memory pipeline acceptance review

Date: 2026-09-21. Parent baseline: `0201b89`.
Status: the implementation meets the clarified in-memory scope below. This is
not a production hosting or live-model quality qualification.

## Scope

One caller supplies an envelope to `WorkflowApplication.run`, configured steps
execute in memory, and that call returns a terminal `ExecutionResult`. Application
intake/authentication, storage, queues, detached workers, receipts and recovery
are outside the package. A small local HTTP example demonstrates the same call.
No database service or queue infrastructure is required by tests or CI.

The retained implementation provides strict configuration and schemas, immutable
plans, deterministic routing, native decisions, structured/text model steps,
trusted handlers and MCP tools. PydanticAI and official provider/MCP SDKs remain
behind typed adapters. Model requests do not discover `/models`; retries and
operation budgets remain explicit. Outbound MCP OAuth is tool support, not an
application authentication layer.

Tenant/principal metadata is optional caller context. An explicit host identity
checks consistency and fills omitted values; it does not authenticate a caller.
W3C trace context remains separate. Safe logs and optional OpenTelemetry exclude
business values and secrets; returned results intentionally contain business data.

## Verification

- 539 service tests pass, including configuration rejection, isolated concurrent
  invocation context, cancellation, owned resource cleanup, deterministic routing,
  invalid native output, model SDK request validation and MCP protocol fixtures.
- The local HTTP example passes over an actual loopback connection to an owned
  Uvicorn process, as well as ASGI boundary tests. It returns each call's result
  directly, rejects malformed/oversized input, and has no job lookup endpoint.
- Embedded and MCP stdio examples complete using synthetic inputs, with no model
  inference. The MCP example uses the actual local SDK subprocess transport.
- An isolated offline base installation runs the CLI and contains no training,
  test, web-server, database or Redis dependencies. Production and example/dev
  dependencies stay separated in the service uv project.
- Strict typing, Ruff lint/format, generated service/native schema drift,
  documentation links, service skill validation and specification checks pass.
- 934 model tests pass; seven native integration tests remain explicitly excluded.
  No model tooling or dataset contracts changed in this cleanup.

Independent code review checked bootstrap, CLI, runtime, HTTP example and scope
boundaries. It identified stale identity wording and shutdown lease wording;
both now describe the actual context-only, in-memory behavior. Architecture tests
prevent package imports of the removed app-auth, storage, worker and inbound
transport modules. Lifecycle regression coverage ensures repeated cancellation
cannot close shared clients while an owned invocation still uses them.

## Limits

No external provider, collector, model inference, dataset generation, upload or
cloud deployment ran during this cleanup. Offline fixtures do not establish
financial decision accuracy or provider production conformance. Process loss
loses unfinished runs. Cancellation cannot prove a remote operation stopped;
external tool effects remain read-only. None of these boundaries authorize adding
infrastructure to the current scope.

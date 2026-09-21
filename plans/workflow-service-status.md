# Workflow service implementation status

Canonical scope: [specification 11](../specs/11-workflow-service.md). The service
is a foreground in-memory input → configured steps → terminal result pipeline.
Persistence, idempotent intake, queue workers, child jobs, application
authentication, packaged HTTP/Redis transports and cloud deployment are not part
of the product scope. Specifications 12–14 are superseded historical markers.

## Implemented scope

- Independent Python 3.12 uv project with locked runtime/dev dependency groups and
  no model-training or curation dependency.
- Strict bounded envelope decoding, optional tenant/principal context,
  explicit-identity consistency/enrichment, immutable JSON and W3C trace context.
- Safe deterministic YAML/Markdown workflow compilation with confined local JSON
  Schema resources, graph/dataflow checks and immutable plans.
- In-memory async runner with local bounded admission, monotonic deadlines,
  invocation-local records/budgets, deterministic routing, cancellation and strict
  terminal `ExecutionResult` mapping.
- Native decision, text/structured LLM, direct MCP, trusted handler and finish
  steps through explicit configured adapters. Attempts reserve before I/O and
  missing usage remains unknown.
- PydanticAI provider bindings with host validation, disabled SDK retries and
  bounded owned client lifetimes.
- Maintained MCP SDK HTTP/stdio clients, declared catalogs, independent schema
  validation, host tool authorization, scoped OAuth credential hooks and optional
  identity/W3C forwarding. External effects remain read-only.
- Safe bounded JSON logging and optional privacy-filtered OpenTelemetry with
  explicit activation and owned shutdown.
- Offline project/compiler inspection commands and foreground `run` command.
  A small runnable HTTP example maps one request to the same in-memory call; it is
  not a package transport, authentication layer or background job service.
- Generated public schemas, documentation and the repository service skill track
  this reduced surface.

## Current verification

The final scoped run reported 539 service tests and 934 model tests passing, with
seven explicitly excluded native integration cases. Service typing, lint, schema
checks and the runnable HTTP example passed. Tests use synthetic inputs and local
protocol fixtures; they do not qualify live-provider accuracy, application
security or production hosting.

## Acceptance

The scoped implementation and independent review are recorded in
[the in-memory cleanup review](reviews/service-in-memory-cleanup.md). Removed durable, broker, worker, authentication and transport
features are not backlog items for this scope.

> **SUPERSEDED / OUT OF SCOPE (2026-09-21):** Historical evidence only. The current workflow-service contract is the foreground in-memory pipeline in `specs/11-python-package.md`. This review creates no active requirement.

# Durable deployment review

Scope: specification 14, configured PostgreSQL intake/worker bootstrap, explicit
migration, durable authenticated HTTP routes, semantic revision separation,
protected transport trace persistence, shared executor composition, and shutdown.
The full service goal remains active; no model inference or production deployment
was performed. Model doubles below are deterministic test adapters.

## Integration evidence

Real isolated PostgreSQL tests cover commit-before-202, concurrent idempotent
retries/conflicts, original trace retention, schema readiness without migration,
current-grant and exact-scope lookup/cancellation, terminal failure lookup, and
client disconnect after commit. A process-level test starts the actual Uvicorn
CLI, submits over loopback HTTP, executes in a separate `worker --once` process,
then retrieves the same terminal result through HTTP. Another test reuses the
PydanticAI FunctionModel composition and persists measured usage without inference.
Worker-stop testing verifies unfinished work returns to accepted state without a
business cancellation. A lifecycle test verifies repeated host cancellation does
not close dependencies while owned work remains.

## Remaining boundaries

No real provider, collector, remote OAuth deployment or cloud/container release
was qualified. Local PostgreSQL tests use an isolated native database; the example
Compose configuration is validated separately, not claimed as a full service
container deployment. Redis intake/output, webhook delivery, reconciled writes,
child dispatch/joins, retention, and complete production acceptance remain open.

The initial shutdown drain is bounded. An arbitrary cancellation-resistant async
callback may keep bootstrap waiting so that clients and lease guardians remain
owned; operators must force termination if it never stops. This does not promise
that cancellation rolled back remote work. MCP tools/handlers remain read-only.

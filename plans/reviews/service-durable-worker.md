# Durable worker review

Scope: shared execution machine, read-only resumable worker, database-derived
remaining deadlines and total usage, validated example, docs and skill. The full
service goal still includes durable ingress, effects, output transports, child
workflows and production qualification.

## Implementation and findings

Pure binding/routing/result logic was extracted from the embedded runner. Both
paths now validate the same checkpoint chain and preserve completed decisions.
Worker state is per claim; exact identity, metadata, revisions and budgets survive
recovery. SQL transactions never remain open during handler/model/tool calls.

The independent review identified immediate lease freshness, budget error
provenance, selected-current failure reporting, initial cancellation polling,
and ignored deadline cancellation as required safeguards. The worker refreshes
ownership, checks scoped cancellation before execution, and retains store-origin
failures even when an executor translates exceptions. Local deadlines guard new
reservations and post-executor transitions. Queued/selected work is failed or
cancelled explicitly; only unselected branches are skipped.

An additional parent review found that a user-cancellation signal must not stop
heartbeats while a handler ignores cancellation. The monitor now sets a signal
and keeps renewing. An owned guardian waits for the read, stops the monitor and
releases live ownership; intake remains closed. A false shutdown result or fixed
serve failure means clients must remain open until owned cleanup finishes.

The final independent reread found no remaining concrete blocker in this worker
scope after these fixes. It confirmed that incomplete `serve` cleanup surfaces
as a fixed dependency failure and that guards remain active through accounting
and finalization.

## Evidence

- Twenty pure machine tests cover restore/transition validation, implicit review,
  selected-current failures, exact result presence and chronological decisions.
- Two additional real PostgreSQL tests cover database-derived remaining time and
  full execution usage across steps, deadline, reclaim and unknown measurements.
- Sixteen real PostgreSQL worker tests cover end-to-end execution, exact identity,
  concurrent isolation, completed-step resume, persistent budgets, cancellation,
  deadlines, heartbeat/reclaim, lease loss, cooperative release, uncooperative
  guardians, translated storage failures and the compiled runnable example.
- The complete service suite passes 663 tests, including existing embedded
  execution, model/MCP protocol, privacy and telemetry coverage. No model calls
  are made. Strict typing covers 89 source/example files, with the new example
  checked separately. Ruff lint and formatting pass for 145 files.
- 934 offline model-tooling tests pass; seven gated native model tests remain
  excluded. No datasets, model endpoints or active generation environments changed.
- Ten service schema snapshots and 27 native snapshots remain unchanged.
  Documentation links, service skill and spec-manifest checks pass.

## Boundaries

CLI `run`/`serve` still use the nondurable synchronous path. This milestone exposes
an embedded worker API; it does not claim durable HTTP/Redis/webhook integration,
write-effect reconciliation, child dispatch, or provider deployment acceptance.
The host must authenticate/authorize intake and supply read-only executors with
business permission checks. Safe logging precedes SDK startup. Database retention,
TLS, credentials, backups and deployment lifecycle remain host responsibilities.

Cooperative cancellation cannot forcibly terminate arbitrary Python callbacks or
remote calls. Retained tasks are visible through incomplete cleanup; a killed
process relies on lease expiry. Read-only retries can repeat an interrupted
external read but never reset consumed attempts. No exactly-once effect or live
model quality claim follows from these tests.

# Durable PostgreSQL storage

The `postgres` extra provides an async execution store and a persistent step
budget. This adapter is implemented separately from the current CLI/HTTP runner:
`run` and `serve` still execute synchronously without database recovery. There is
a separate [durable worker API](WORKERS.md), but no result-delivery worker or
write-effect reconciliation yet.

Install `uv sync --locked --no-dev --extra postgres` from `service/`. Configure
safe application logging before opening SDK clients, including PostgreSQL.
Psycopg can log connection diagnostics; adapter exception redaction alone does
not sanitize a host's log handlers. Reuse
`foliqant.adapters.telemetry.logging.configure_logging` and drain it at shutdown.
Do not enable unrestricted SDK debug logging.

## Ownership and migration

`await PostgresStore.open(dsn)` creates an owned native async connection pool.
It does not migrate the database. An operator explicitly calls
`await store.migrate()`; application operations require the known schema and
migration hash. The fixed schema is `foliqant`. Unknown versions and migration
hash mismatches fail; migrations do not silently rewrite existing data.
Always close the pool with `await store.aclose()`.

Pool concurrency defaults to four, with sixteen waiting operations and a ten
second operation timeout. Set `concurrency`, `queue_limit`, and `operation_timeout`
on `open` for the host. Admission, pool waits and transactions are bounded. Never
hold a database transaction across model, tool, or delivery calls. Configure
PostgreSQL TLS, credentials, backups and access controls through deployment; the
adapter is not a database administration service.

The [runnable storage example](../examples/durable-storage/README.md) accepts,
checkpoints and retrieves a result after reopening the adapter without inference.
It uses a dedicated database and deliberately applies the migration.

## Acceptance and ownership

A `Submission` includes the accepted envelope, trusted `Identity`, workflow,
immutable revision, first step, execution limits, idempotency key, and optional
`DeliveryRequest`. Identity fields remain independently optional. Claims in
metadata must match trusted identity; absent claims are enriched before storage.
Anonymous acceptance is forbidden unless the host explicitly opts in using
`allow_anonymous=True`. The store does not authenticate tokens or grant workflow
access: the ingress must do both first.

A key deduplicates within the exact identity and workflow scope. Repeating the
same request returns the original execution without extending its deadline;
changed input, revision, limits or output binding conflicts. `get` and `cancel`
require that exact identity. A different scope receives `not_found`. Cancellation
records a request; it does not prove external work has stopped.

Workers claim only their supported `(workflow, revision)` pairs. Each claim has
a database-clock disposition, lease owner and increasing fence. Cancellation
wins over timeout when both are present. Only `run` permits proceeding to work;
other dispositions require terminalization. Claims with `current_step=None` are
ready to finalize, not a reason to rerun steps. Every mutation checks current
ownership and a live lease. Heartbeat cannot revive an expired lease. Graceful
workers stop their work and release a still-live lease for immediate reclaim.

A checkpoint commits one completed/review step and its next step together.
Identical replay under a live lease is harmless; changed content conflicts.
Terminal completion requires the checkpoint chain to have ended, immutable
accepted metadata, unchanged prior checkpoints, and matching durable usage.
The store validates storage consistency; the workflow engine must still validate
schemas, compiled routing and authorization before recording results.

## Attempts and delivery

Create a budget for each claimed step with
`await PersistentStepBudget.open(store, claim.lease, step_id=...)`. Reserve every
model/tool attempt before external I/O. Failed or interrupted attempts stay
charged after reclaim. Unknown token measurements remain unknown. Reporting
usage for an already started model call remains possible after cancellation or
the run deadline while the worker still owns a live lease. Local `snapshot()` is
the last successful observation; database state is authoritative.

An optional result outbox commits atomically with the terminal result. Delivery
has its own stable event ID, attempt count, retry schedule and fenced lease. It
can retry without running the workflow again. Consumers must deduplicate that
ID; this is at-least-once delivery, not exactly-once external effects. Exhausted
delivery remains visible and does not change a completed business result.
Destination IDs refer to trusted deployment bindings, never input-supplied URLs.
Write tools remain disabled until effect identity and reconciliation are wired.

## Database tests

`pytest service/tests/test_postgres_store.py` runs real PostgreSQL tests. It uses
an isolated temporary native cluster when `initdb` and `pg_ctl` are available.
Otherwise configure `FOLIQANT_TEST_POSTGRES_DSN` to a **disposable test database**.
The fixture drops the entire `foliqant` schema before and after each test; never
point it at a development or production database containing useful data. An
explicit unavailable database fails the tests. Missing native programs without
an explicit DSN skip these tests; that is not durability acceptance evidence.
CI supplies a dedicated PostgreSQL service.

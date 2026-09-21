# Durable execution storage contract

Authority: `DEC-WORKFLOW-SERVICE`; refines specification 11's accepted reliability
requirements. Capability: `CAP-SERVICE-DURABILITY`. Status: independently reviewed
storage contract; worker/transport integration remains incomplete.

## Ownership and reuse

`core/storage.py` owns immutable storage values; `ports/storage.py` owns the async
store protocol. Reuse AcceptedEnvelope, Identity, ExecutionLimits, StepRecord,
RunResult and Usage. `adapters/storage/postgres/` owns Psycopg SQL, mapping and
explicit migrations. It may use existing boundary serializers to validate stored
results; neither core nor ports imports a database or Pydantic SDK. No database
transaction remains open during a model/tool/network effect. No production-memory
fallback is allowed. The existing synchronous HTTP path remains explicitly
nondurable until a worker and durable ingress are connected.

## Schema and identity

An operator explicitly calls migration; normal store operations require exactly
the known schema version. An advisory transaction lock serializes migration and
checks a recorded SQL digest. Unknown versions or mismatched migration hashes
fail. Tables live in a fixed `foliqant` schema; parameterize values, never accept
schema/table names from payloads. Use native async Psycopg connections and bounded
admission/connection/query/transaction lifetimes. Credentials are supplied by the
host/environment and never logged or stored in execution rows.

Acceptance stores a UUID execution ID, workflow, immutable effective revision,
accepted envelope, identity, first/current step, limits, database acceptance time,
absolute database deadline, status, lease owner/fence/expiry, cancellation flag,
and canonical input digest. Deduplication scope is the SHA-256 of canonical JSON
`{tenant_id, principal_id}` with null representing an absent ID. A unique
(scope, workflow, idempotency key) row makes concurrent acceptance atomic. Compare
the actual identity as well as its hash before returning a match. An idempotency
key is 1–256 nonblank printable characters, and is not logged. The request digest
includes version/domain tags, envelope, revision, first step, limits and delivery
binding; same key/different digest is
CONFLICT, including a changed revision. Duplicate acceptance never extends the
deadline. Anonymous acceptance defaults to forbidden; an explicit store policy
may enable it. Metadata identity claims must match the trusted identity. Absent claims are
filled from trusted identity through the existing envelope acceptance boundary
before hashing or persisting; an absent protected claim is not an explicit null.

Digest v1 is an owned CPython 3.12 representation, not RFC 8785/JCS:
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
allow_nan=False).encode("utf-8")`. JSON numbers retain int/float distinctions
(including `1` versus `1.0` and negative zero). Domain/version tags are part of
the hashed document. Other adapters must reproduce these bytes and golden
Unicode/numeric vectors; a changed encoding needs a new digest version, never a
silent rewrite of stored identities.

Workflow and step identifiers use the existing lowercase snake_case contract,
maximum 256 characters. Revisions are nonblank printable strings of at most 512
characters; owners, destinations and idempotency keys are nonblank printable
strings of at most 256. Core timestamps are timezone-aware UTC. SQL checks enforce
identifier bounds, lowercase 64-character hex digests/scopes, legal statuses,
positive signed-bigint fences without overflow, and owner/lease consistency.
Only running rows have execution leases; pending outbox rows may have delivery
leases, always with both owner and expiry present or both absent.

Read/cancel requires execution ID plus exact independently optional identity.
A different scope gets NOT_FOUND, not an existence-revealing permission error.
Cancellation sets a durable request flag; it does not claim external work stopped.
Terminal cancellation is a no-op. A worker records the terminal outcome.

## Claims, checkpoints and budgets

Claim uses `FOR UPDATE SKIP LOCKED`, ordered by acceptance time/ID. Eligible states
are accepted or running with an expired lease, restricted to an explicitly
provided set of supported workflow/revision pairs. Expired-deadline and cancelled
rows can still be claimed for terminalization, never for new external calls.
The claim returns a database-derived disposition: `run`, `terminalize_timeout`,
or `terminalize_cancel`; cancellation takes precedence when both conditions are
present. Workers do not compare their wall clock to choose that disposition.
Claims also return `remaining_seconds`, calculated from deadline minus the final
live database clock and clamped to zero. Workers anchor that duration to their
monotonic time before claiming, not to their wall clock.
Every claim increments the fence. A final database-clock lease check after
materialization rejects and rolls back claims that have already expired. Every worker mutation checks ID, owner, fence,
running status and a lease still live on the database clock. Row locking plus current-step comparison
serializes transitions by concurrent callers holding the same lease. This
`(status, current_step)` transition CAS plus the ownership fence refines
specification 11's generic state/version requirement; the fence is not a step
counter. A stale/expired
writer gets CONFLICT. Heartbeat never revives an expired lease. Lease durations
are 1–300 seconds, owners are bounded opaque strings. Clock decisions use
`clock_timestamp()`, not transaction-start timestamps after lock waits.
`release(lease)` requires the same live ownership proof, clears owner/expiry and
returns the nonterminal row to accepted state for immediate reclaim. It preserves
all checkpoints, counters, cancellation, deadline and fence; the next claim
increments the fence. Graceful workers release after stopping their work.

Each committed checkpoint contains one immutable step record and its selected
next step; both are written in the same transaction. Checkpoint status must be
completed or needs_review; failed/cancelled/skipped records belong only in the
terminal result, not the successfully completed checkpoint chain. Only the current step may
advance, while neither cancellation nor the run deadline has arrived. Checkpoint
count enforces the accepted maximum visited steps across claims. Retrying an identical checkpoint under a still-valid lease is a no-op;
a changed record/transition conflicts. Identical replay may occur after the
current pointer has advanced, but must still validate live ownership. Completed checkpoints must never rerun.
A null next step means ready for terminalization, not implicit success.

Model/tool reservations are separately persisted before I/O, with per-step,
per-kind monotonically increasing integer tickets. Limits come from the accepted
run, never from a worker's new deployment. Reservations require current step,
no cancellation and an unexpired run deadline. Reclaim cannot reset counts.
Unknown model usage remains unknown. Reporting the same ticket/usage twice is
idempotent; changing a report conflicts. Usage reports identify model tickets;
reports and usage reads also work for prior steps or after cancellation/deadline,
provided the execution lease remains live. They account for already-incurred I/O,
and must not reuse the stricter new-reservation gate. The store derives aggregate usage from
all reservations. `execution_usage(lease)` reads the full execution total under
live ownership, including prior steps. Finalization validates it against the result.

Terminalization checks the live fence and exact execution/workflow/revision,
accepted metadata (including protected identity claims), and persisted usage,
then stores a validated terminal RunResult. Every persisted checkpoint must
appear unchanged and in order in result decisions, with no duplicate decision
IDs. Failed/cancelled results preserve the original accepted payload. Completed
or needs_review results require a null current step, so they cannot bypass the
checkpoint chain. Successful completion/review
is disallowed after the deadline or cancellation request. Failed/cancelled
terminalization remains possible. Completed result and optional outbox row commit
atomically. Immutable final results cannot be overwritten by an old worker.
This store does not yet authorize write effects: effect intent/reconciliation and
worker integration must precede enabling mutations.

## Outbox

One optional result delivery is declared at acceptance, with a deployment-owned
destination ID and maximum attempts (1–32, default 8). Terminalization creates one
stable event UUID and the validated public result. Delivery status is separate
from run status: pending, delivered, exhausted. Claims use leases/fences and
increment the persisted attempt count before I/O. Failure schedules a bounded
retry delay (0–3600 seconds), or marks exhausted when the accepted maximum is
reached. Expired claims can be reclaimed, but never beyond that maximum. A crash
on the final attempt is marked exhausted by recovery instead of disappearing.
Acknowledgement/retry requires a live delivery lease. Store only safe error codes,
not destination responses. Each claim poll reconciles at most 100 crashed final
attempts to bound recovery locks and transaction work. Repeated polls drain the
remaining exhausted backlog. Consumers must deduplicate the stable event ID;
transactional storage cannot guarantee exactly-once external delivery.

## Verification and later integration

Use a real isolated PostgreSQL instance: concurrent deduplication/conflict,
principal-only/tenant-only isolation, exclusive claim, expiry/reclaim/stale writer,
checkpoint replay/conflict, durable attempt counts and unknown usage, cancellation
and deadlines, atomic result/outbox, delivery retries/exhaustion, schema drift,
transaction rollback and connection cleanup. Simulate a worker process loss by
closing its connection/recreating the adapter while retaining the database.
Use PostgreSQL JSON columns for opaque documents: preserve insertion order,
integer/float spelling, negative zero and escaped null characters; no query
requires JSONB indexing. Golden digest vectors live in `test_storage_mapping.py`.
No inference or customer dataset is needed. SQL constraints and live database
behavior are acceptance evidence; in-memory doubles do not establish durability.

The [durable worker](13-durable-worker.md) implements resume and heartbeat.
The remaining goal includes effect ledger/reconciliation,
durable HTTP/Redis/Webhook adapters, child workflows, retention/deletion and
production operations. This storage milestone does not replace those deliverables.

References: [Psycopg async operations](https://www.psycopg.org/psycopg3/docs/advanced/async.html),
[PostgreSQL row locking](https://www.postgresql.org/docs/current/explicit-locking.html),
[SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html).

### Concrete adapter API

`PostgresStore.open(dsn, *, concurrency=4, queue_limit=16, operation_timeout=10,
allow_anonymous=False)` is an async classmethod returning an owned store;
`await store.aclose()` closes its Psycopg async pool. `await store.migrate()` is the
explicit operator operation. The methods match `ports/storage.py`. Existing
fixed `ServiceError` codes express conflicts, forbidden anonymous intake,
capacity, invalid input, timeout, cancellation and budget exhaustion; no raw SQL
error escapes. No new parallel error vocabulary is introduced. Claim returns
None only for an empty eligible queue. DB results use immutable core values.

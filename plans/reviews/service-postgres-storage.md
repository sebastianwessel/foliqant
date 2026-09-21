> **SUPERSEDED / OUT OF SCOPE (2026-09-21):** Historical evidence only. The current workflow-service contract is the foreground in-memory pipeline in `specs/11-workflow-service.md`. This review creates no active requirement.

# PostgreSQL storage review

Scope: specification 12, async storage port and core values, PostgreSQL adapter,
persistent budget bridge, runnable storage example, docs/skill and CI database.
This review does not qualify a durable worker or production service deployment.

## Changes and contract review

The independent contract review identified missing database-derived claim
classification, incomplete successful-finalization checks, unrestricted checkpoint
statuses, absent graceful release, unclear ownership versus transition versions,
and unspecified digest encoding. The implementation now has explicit claim
disposition (cancel before timeout), live-fence release, successful terminalization
only after the transition chain ends, unchanged checkpoint contents/order,
accepted metadata and failed/cancelled payload preservation, and durable usage
verification. Completed/review checkpoints are distinct from terminal failures.

Usage recording remains possible for already-incurred calls after a deadline or
cancellation, while new reservations are denied. Every worker mutation verifies
live ownership; row locks serialize writers, and expired ownership cannot revive
through heartbeat. SQL mutation predicates and late checks roll transactions back
when lease/deadline expiry occurs during an operation.

CPython 3.12 digest v1 has fixed golden JSON vectors. PostgreSQL JSON columns
preserve opaque document order and numeric spelling without extra object-order
metadata; no JSONB queries/indexes are needed. Terminal results and optional
outbox events commit together. Delivery uses separate fencing, attempt budgets
and a bounded 100-row final-attempt recovery sweep per claim poll.

The final independent implementation review closed three concrete findings:
relative checkpoint order is enforced, outbox exhaustion cleanup is bounded, and
claims recheck the live database lease after materializing stored data. A claim
that expires during loading rolls back its fence/attempt instead of returning
stale ownership. The reviewer found no remaining blocker in this store-only scope.

## Local evidence

- 625 service tests pass, including 16 against an isolated PostgreSQL 14.23
  cluster. No database tests were skipped. Tests cover concurrent deduplication,
  conflicts, exact optional identity, reclaim/stale fencing, DB-clock deadlines,
  checkpoint replay, durable budgets across adapter restart, cancellation,
  terminal/outbox atomicity, delivery exhaustion, migration/hash rejection,
  cancellation cleanup, late claim-expiry rollback, reversed checkpoint rejection,
  and the executable database example.
- Six deterministic digest tests cover numeric distinctions, Unicode, escaped
  nulls, ordering and rejection of nonfinite numbers.
- 934 model-tooling tests pass; seven explicitly gated native integration tests
  remain excluded. No model endpoint was discovered or called.
- Strict service typing (85 source/example files), lint and formatting pass.
  Ten service schema snapshots and 27 native snapshots remain unchanged.
- A temporary production environment with only the PostgreSQL extra contains
  29 distributions, imports the adapter, and has no dev/training packages.
- CI has a disposable PostgreSQL 18 service; remote CI has not run in this work.
  Local PostgreSQL 14 evidence is not a claim that PostgreSQL 18 was tested here.

## Boundaries

The host must configure existing safe logging before SDK/pool startup; raw
Psycopg diagnostics are not safe simply because API exceptions are redacted.
Application operations require explicit known migrations. Stored content is
private business data: deployment still owns access control, TLS, backups,
retention and encryption.

The current CLI/HTTP runner remains nondurable. Worker resume/heartbeat, effect
journal and reconciliation, durable ingress/cancellation endpoints, output
workers, Redis, child workflows, production operations and full acceptance are
still required. A completed SQL checkpoint proves persistence, not exactly-once
external effects or provider quality. No datasets or environments used by active
model generation were changed.

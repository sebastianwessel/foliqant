# Durable execution storage — superseded

Status: **SUPERSEDED / OUT OF SCOPE** as of 2026-09-21.

The project owner clarified the scope of the workflow service as an in-memory
input → steps → terminal-result pipeline. PostgreSQL execution state,
idempotent acceptance, leases, fences, checkpoints, persistent budgets, outbox
delivery and migration are not active service requirements. This file is retained
only as a historical marker and provides no implementation or acceptance authority.

The active contract is [specification 11](11-workflow-service.md).

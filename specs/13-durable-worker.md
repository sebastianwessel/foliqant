# Durable execution worker

Authority: DEC-WORKFLOW-SERVICE, CAP-SERVICE-DURABILITY. Refines specifications
11 and 12 for read-only workflow recovery; effect reconciliation, durable ingress,
output transports and child dispatch remain part of the full implementation.

## Shared execution and trusted composition

`core/machine.py` owns pure binding, routing, checkpoint restoration and result
construction. Both the embedded runner and `workers/execution.py` use it. It has
no SDK or I/O dependencies. Restore validates checkpoint names, order, transitions,
statuses and visited-step limits against the immutable compiled plan. A stored
result does not grant permission to change the plan. Result decisions list
visited steps in execution order, then skipped steps in declaration order.

`WorkflowBinding` holds an exact compiled plan, executor, input validator and
optional observer. `ExecutionWorker` receives an `ExecutionStore`, bindings,
opaque worker ID, concurrency (default 4), lease seconds (default 30), poll interval
(default 0.25 seconds), and cleanup timeout (default 2 seconds). Construction
validates settings and duplicate workflow/revision pairs. The host owns store,
executor and client lifetimes. Runtime bindings authorize read operations only;
write handlers/tools remain disabled until effect intent/reconciliation exists.
The worker itself does not authenticate intake or infer business permissions.

## Deadlines, cancellation and recovery

Claim returns `remaining_seconds` computed from the final live database-clock
check, clamped to zero. A worker anchors its monotonic deadline to the time
**before** claiming plus that remainder. This is conservative across database
latency and never compares worker wall time to a database timestamp.
`execution_usage(lease)` supplies authoritative persisted totals under a live
lease, including interrupted model attempts with unknown measurements.

Each active claim has one execution task and one heartbeat/cancellation monitor.
Refresh ownership immediately after restore/validation and before I/O, then
heartbeat every lease/3 seconds; polling reads cancellation under the exact
accepted identity. A fresh workflow observation is opened per attempt; no span
remains open across a restart. Step context carries the accepted identity and
metadata unchanged. The exact-revision input validator runs once per claim before
reservations. Plan name/revision/start and restored current pointer must match
stored state. Completed checkpoints are restored without calling executors.
Each new operation uses a persistent step budget; reservations precede I/O.

A checkpoint records validated result and deterministic transition together.
A private budget provenance channel retains store-origin failures even when an
executor translates exceptions. Only a store timeout coinciding with the elapsed
conservative run deadline becomes a terminal timeout; operation/pool timeouts
abandon for recovery. The in-memory machine advances before the SQL commit, but a failed storage write
never permits finalizing that uncommitted state. Cancellation/deadline failures
rebuild from persisted checkpoints and finalize with original payload and exact
usage. The selected current step is failed/cancelled even if preparation had not
started; only unselected steps are skipped. Other database/lease failures abandon that attempt without declaring a
business failure; later reclaim retries only unfinished read work within the
original budget. Executor failures are safe terminal failures. Live-lease usage
is read again before finalization, rather than trusting a local budget snapshot.

The monitor stops execution on cancellation or loss of ownership. A caller's
cancellation or shutdown is distinct from a persisted user cancellation: it
releases live ownership after work stops instead of cancelling the business run.
A process crash leaves the lease to expire. No release, final write or new work
is allowed while an uncooperative old executor remains running. Such tasks remain
owned, intake closes, a guardian renews the lease until the read stops (unless
ownership is lost), and shutdown reports incomplete cleanup; there is no claim
that Python cancellation forcibly stopped a remote operation.

## Bounded lifecycle

`run_once()` performs at most one claimed execution and returns its terminal
result, or None when no supported work exists. A claim returned after stop/intake
closure is released without starting execution. It rejects excess concurrent calls
before allocating internal work. `serve(stop)` owns a fixed number of polling
loops with bounded error backoff, never one task per queued row. Terminal business
failure is a result, not a poll-loop crash. Invalid configuration is fatal; transient
store failures and stale claims can retry after the bounded polling delay.

`aclose(timeout=10)` closes intake and waits for accepted local tasks for the drain
timeout, then cancels and waits up to cleanup_timeout. It returns whether all owned
tasks stopped. Hosts must not close shared clients while cleanup is incomplete.
No model/DB transaction spans external I/O. Safe logging uses fixed event/error
codes, never payloads, identities, revision values, or arbitrary exception text.

## Evidence required

Real PostgreSQL tests must prove end-to-end deterministic execution, concurrent
caller isolation, checkpoint resume without rerunning completed handlers,
persisted attempts across interruption, cancellation and deadline terminalization,
heartbeat protection against reclaim, graceful release, lease loss without stale
commits, bounded shutdown/straggler ownership and safe error mapping. Existing
embedded tests must still pass after extraction. No model inference is required
for these runtime mechanisms; these tests do not establish model quality.

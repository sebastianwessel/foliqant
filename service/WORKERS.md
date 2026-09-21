# Resumable execution workers

`ExecutionWorker` runs compiled read-only workflows using an `ExecutionStore`.
The PostgreSQL adapter supplies durable acceptance, leases, checkpoints and
attempt counts. Completed steps survive process restarts and are not called again.
This is an embedded worker API: the current CLI `run`/`serve` remain synchronous;
durable HTTP ingress, Redis, output delivery and effect reconciliation are not
yet wired into them.

The [runnable example](../examples/durable-workflow/README.md) uses actual
PostgreSQL and validated async handlers without model inference. For models or
MCP, supply the same configured executors used by embedded execution. Keep model,
MCP and handler authorization in those adapters; storage does not authenticate
callers or grant business permissions.

## Composition

Compile once, construct one exact-revision `WorkflowBinding` per supported
workflow, and share the store and clients across workers:

```python
from foliqant.workers.execution import ExecutionWorker, WorkflowBinding

worker = ExecutionWorker(
    store,
    [WorkflowBinding(plan, executor, validator, observer)],
    worker_id="worker_1",
    concurrency=4,
    lease_seconds=30,
)
result = await worker.run_once()  # None means no supported queued work.
```

`plan`, `executor`, `validator`, `observer` and `store` are host-created objects;
the complete setup is in the example. An observer is optional. Use a unique
worker ID per running worker. Exact `(workflow, revision)` pairs determine which
rows can be claimed. Duplicate bindings and invalid settings fail startup. A
changed workflow does not silently reinterpret queued rows from an older revision:
retain the original binding until those executions finish or are resolved.

`run_once` performs at most one claim. At most `concurrency` calls can be active;
excess calls fail before allocating work. `serve(stop_event)` owns exactly that
many polling loops and waits between empty/transient-error polls. Defaults are
four active claims, a 30-second lease, and a 0.25-second polling delay. Each claim
owns its own caller metadata, persistent budget and fresh observation scope.

## Recovery and deadlines

The worker restores the committed checkpoint chain using the same binding and
routing implementation as the nondurable runner. It checks the exact revision,
start step, stored current pointer and input schema before work. Selected steps
appear chronologically in results, followed by skipped branches.

The database supplies remaining run time. The worker anchors it to a monotonic
clock from before the claim, so database latency reduces the available time;
worker wall-clock drift cannot extend a deadline. The worker refreshes ownership
and checks cancellation before starting, then heartbeats and polls cancellation
every third of the lease interval. Checkpoint writes and external-attempt
reservations also reject expired ownership or a cancelled run.

Interrupted read-only steps may execute again within their original attempt
budget. Persistent reservations are never refunded by a crash. Usage is read
from the database before terminalization; an interrupted/unreported model call
has unknown token usage, not zero. Store failures remain recoverable infrastructure
failures even if an executor translates their exception. Lost leases cannot
commit results. There is no exactly-once claim for remote reads or mutations.

Business uncertainty ends in `needs_review` or follows the compiled unresolved
route. Executor failures produce a safe terminal failure. User cancellation is a
durable flag; it produces a cancelled result when the worker observes it. Failures
and cancellation preserve accepted payload and already committed decisions. The
selected unfinished step is failed/cancelled; unselected branches are skipped.

## Shutdown and client ownership

Set the stop event to stop polling, then drain the worker before closing its store
or provider/tool clients. `await worker.aclose(timeout=10)` closes intake and
waits up to ten seconds, then requests cancellation and waits up to
`cleanup_timeout` (default two seconds). Check its boolean result.

False means a task still owns resources. Intake stays closed, strong references
are retained, and a guardian continues lease renewal until the read stops or
ownership is lost. An executor that ignores cancellation cannot commit a new
checkpoint or reserve new calls after interruption. Once it stops, the guardian
releases still-live ownership. Call `aclose` again to observe completed cleanup;
**do not close shared clients while it returns false**. `serve` raises a fixed
dependency failure when it cannot drain cleanly; it does not hide retained work.
Caller cancellation propagates after bounded cleanup and does not mean a durable
business cancellation. A process killed outright leaves lease expiry to recovery.

Python cannot forcibly terminate an arbitrary async callback that ignores
cancellation. Blocking adapters retain their own underlying read threads until
those calls return; configure real SDK timeouts. Write tools/handlers remain
blocked pending durable effect intent and reconciliation.

Configure existing safe logging before SDK startup, including PostgreSQL. Logs
use fixed events/codes; no raw exceptions, identity, inputs or results. Optional
observation uses the existing safe workflow/step observer and protected W3C
metadata. It never leaves a span open across worker restarts.

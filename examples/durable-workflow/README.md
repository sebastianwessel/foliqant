# Resumable workflow example

This example compiles YAML steps, accepts an envelope in PostgreSQL, and runs it
through `ExecutionWorker`. The validated handler echoes a message; it does not
make model calls or simulate an AI decision. The worker persists each completed
step, including the finish step, and reads the final result from the database.

Use a dedicated example database. From the repository root:

```sh
uv sync --project service --locked --extra postgres
export FOLIQANT_EXAMPLE_POSTGRES_DSN='postgresql://user:password@localhost/example_db'
uv run --project service --no-sync python examples/durable-workflow/run.py
```

Supply actual credentials outside Git. This demonstration explicitly runs the
operator migration and retains its rows in the fixed `foliqant` schema. Each
invocation has a fresh idempotency key. Successful output is a public JSON result
with `payload.message` equal to `Hallo` and execution status `completed`.

The fixed `example_operator` identity is trusted demonstration input, not an
authentication mechanism. Real ingress must authenticate and authorize before
calling `store.accept`. The registered handler validates both arguments and
results and is declared read-only.

A process interrupted after a checkpoint can be restarted with the same workflow
revision: the worker restores completed steps and executes only the unfinished
part. This example polls supported work until its own submission finishes, so an
unfinished previous demonstration may be recovered first. It does not remove
other data. Real deployments need fixed worker ownership, a separate intake
adapter, and explicit client/store lifecycle management.

See the [worker guide](../../service/WORKERS.md) for polling, shutdown, lease loss,
and the limits of recovery. The existing CLI `run`/`serve` are still synchronous
and do not use this worker automatically.

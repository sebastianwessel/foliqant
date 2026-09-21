# PostgreSQL storage example

This runs one deterministic step, checkpoints it, stores a terminal result, closes
the adapter, then retrieves the result through a new adapter. It makes no model
calls and does not run the workflow engine or start a background worker.

Use a **dedicated example database**. The example explicitly applies the adapter's
migration to the fixed `foliqant` schema. It retains its rows; it does not delete
existing data. Run from the repository root:

```sh
uv sync --project service --locked --extra postgres
export FOLIQANT_EXAMPLE_POSTGRES_DSN='postgresql://user:password@localhost/example_db'
uv run --project service --no-sync python examples/durable-storage/run.py
```

Successful output is `completed`. Supply real credentials through your shell or
secret manager, never commit them. Each invocation creates a fresh example revision
and row; reusing a scoped idempotency key with a changed revision is a conflict.
For repeated demonstrations use a fresh database, or remove example data through
your database administration tools after checking the target.

See the [storage guide](../../service/STORAGE.md) for leases, durable accounting,
delivery semantics, tests, and the boundary between this adapter and the current
nondurable HTTP runner.

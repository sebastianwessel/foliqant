# Embedded workflow example

Prepare the locked base environment once, then run the deterministic offline
example from the repository root:

```bash
uv sync --project service --locked --no-dev
uv run --project service --no-sync python examples/embedded-workflow/run.py
```

The script compiles the workflow, validates its envelope payload, binds a trusted
identity supplied directly by the demo host, follows deterministic handler
`next`/`on_unresolved` routing, and prints the strict execution result as JSON.
The identity values demonstrate protected metadata; no authentication occurs.
The executor performs no inference or network access. `WorkflowRunner` is an
embedded, nondurable runner; this example makes no production or durability claim.

# Implementation conventions

Follow [AGENTS.md](../AGENTS.md) and [specs](../specs/README.md). Use Python 3.12,
the committed uv lock, strict typing and concise public API docstrings.

## Boundaries

The compiler reads and freezes configuration before adapters open. Request-time
execution uses immutable plans and invocation-local state, without configuration
filesystem reads or endpoint discovery. Pydantic contracts generate packaged `src/foliqant/schemas/`;
never hand-edit generated JSON. The core depends only on standard-library values
and ports. Integrations remain optional installation extras.

Hosts register trusted handlers and tool authorizers directly. Configuration
cannot import arbitrary Python code. External tools are read-only. Cancellation
does not prove that a remote request or blocking worker stopped. Preserve owned
capacity until work actually ends, and drain owned work during shutdown.

Only canonical errors and allowlisted sanitized labels enter logs or telemetry.
Evaluation reuses public run/run_flow/run_step paths. Failed or skipped outcomes
remain in metric denominators. Gold is not read at application startup.

Examples use public lifecycle APIs and commit independent synthetic golden cases.
Scripted runs verify wiring; explicit live runs measure a model. Generated reports
remain private. Skills and docs describe supported usage without development history.

For public documentation changes, follow the reader paths and page responsibilities
in [Documentation architecture](documentation-architecture.md). Keep setup,
configuration, step guides, evaluation, tutorials, and reference linked without
duplicating complete manuals. Check code fences with Ruff after the final edit.

## Offline checks

From the repository root after `uv sync --locked --all-extras --group dev --group docs`:

```sh
uv run --no-sync pytest tests
uv run --no-sync mypy src examples
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync python scripts/generate_schemas.py --check
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/check_tracked_data.py
uv run --no-sync mkdocs build --strict
git diff --check
```

Use `python scripts/generate_schemas.py` through the same uv environment to
regenerate schemas deliberately. Default tests exclude `live_model`; no model
endpoint is needed. Isolated installed-wheel tests may download dependencies
that are not available in the local cache.

Preview docs with `uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8001`.
CI builds them strictly and publishes successful pushes to `main` to GitHub
Pages after the repository's Pages source is configured as GitHub Actions.

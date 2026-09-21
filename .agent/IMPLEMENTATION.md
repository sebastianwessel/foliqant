# Implementation conventions

[AGENTS.md](../AGENTS.md) and [spec authority](../specs/README.md) define scope.
Use Python 3.12, each project's committed uv lock, strict mypy, typed public APIs
and concise docstrings. The runtime never imports model tooling. Core uses
standard-library values and ports; provider SDKs and Pydantic stay at boundaries.

## Contracts and runtime

Library Pydantic contracts in `src/foliqant/contracts/` and
`src/foliqant/decisions/` generate `schemas/foliqant/`. Model-only contracts
generate `model/schemas/`. Keep wire spellings and versions stable. Strictly
validate external JSON/YAML; do not hand-edit generated schemas.

Prepare/compile offline at startup. Execute frozen plans with invocation-local
state. Prefer async I/O; blocking SDKs use the owned `BlockingExecutor` and
network timeouts. Cancellation does not free running blocking-call capacity or
prove a remote operation stopped. Shutdown drains owned work before dependencies.

Hosts inject trusted handlers and tool authorizers. Current integrations are
read-only. Optional identity metadata is context, never authentication. MCP OAuth
is outbound tool support. HTTP hosting is an example or embedding concern.

Only fixed safe errors and allowlisted sanitized values enter logs/telemetry.
Do not log payloads, identities, prompts, credentials or raw exceptions.
Evaluation reuses `run_step` and the same validators; failed/skipped expectations
remain in denominators. Golden data, results and holdout selection belong to the
caller. Schema validity and confidence are not accuracy.

## Model operations

Keep MLX imports inside the isolated backend. Parent orchestration verifies
worker outputs independently before immutable artifact publication. Use
`ArtifactTransaction`; retain recursive ancestry, rights, precision history
and leakage indexes. Never overwrite outputs, silently repair hashes, delete
locks automatically or kill a process from a stale PID alone.

Setup/fetch/curation acquire explicitly configured sources. Generation uses the
configured loopback or explicitly allowed private endpoint; training/evaluation
use local verified assets. Setup never starts training. Keep data and outputs
outside Git; minimal test records are constructed in temporary directories.
Live execution follows the user's authorization and must not disturb active runs.

## Offline verification

Run relevant checks from the repository root; use `--no-sync` against the
prepared environment to avoid changing dependencies during another run.

```sh
uv run --no-sync pytest tests
uv run --no-sync mypy src examples
uv run --no-sync ruff check src tests examples scripts
uv run --no-sync ruff format --check src tests examples scripts
uv run --no-sync python scripts/generate_schemas.py --check
uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests
uv run --project model --no-sync mypy --config-file model/pyproject.toml model/src
uv run --project model --no-sync ruff check model/src model/tests
uv run --project model --no-sync python scripts/generate_model_schemas.py --check model/schemas
uv run --project model --no-sync python scripts/check_docs.py
uv run --project model --no-sync python scripts/check_tracked_data.py
uv run --group docs mkdocs build --strict
git diff --check
```

For an intentional model schema update use
`scripts/generate_model_schemas.py --maintenance-output model/schemas`, then
check drift. This is repository maintenance, not permission to overwrite model
artifacts. Preview public docs with `uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8001`;
`docs/index.md` is the homepage. CI builds strictly; a GitHub Actions workflow is configured for GitHub Pages
publishing when Pages is enabled and available for the repository.

## Live acceptance

Default runtime tests exclude `live_model`; model tests exclude `integration`.
Do not call endpoints or download models to verify documentation changes.
When native acceptance is authorized, use a completed local setup and an explicit
model in a permitted native Metal environment:

```sh
FOLIQANT_TEST_SETUP=/absolute/path/to/completed/setup \
FOLIQANT_TEST_MODEL=/absolute/path/to/completed/setup/downloads/model \
  uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests -m integration
```

No test downloads a model implicitly. A native sandbox failure does not prove
the host lacks hardware support. Record real CLI, lineage, held-out evaluation,
policy/audit and independent export-inference evidence in `plans/reviews/`.
Mocks only test isolated boundaries; previous counts and tiny-model success do
not establish current financial quality.

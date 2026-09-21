# Implementation conventions

The local model lifecycle is implemented in `model/src/foliqant_model`. Its
canonical requirements are in `specs/`; end-user guides are in `docs/`. The
root Python/uv project is the reusable `foliqant` library governed by specification 11.
Model tooling has its own uv project under `model/`; the library never imports it.
Do not add PURISTA, Harness or Voyage dependencies. Reuse `foliqant.decisions`; retain existing wire schemas and artifact identities.

## Code and contracts

Use CPython 3.12, the committed uv lock and strict mypy. Public functions are typed
and have concise docstrings. Keep MLX imports inside the isolated backend so
offline commands work without GPU libraries. Do not change global Python or
operating-system memory settings.

Pydantic classes in `src/foliqant/contracts/` and `src/foliqant/decisions/` are
canonical for library-owned boundaries. Their generated schemas live in
`schemas/foliqant/runtime/` and `schemas/foliqant/decisions/`. Model-tooling
contracts are canonical in `model/src/foliqant_model/` and generate only into
`model/schemas/`. Regenerate reviewed model schema changes with
`scripts/generate_model_schemas.py --maintenance-output model/schemas`, then
run both drift checks. External JSON/YAML must pass strict runtime validation.

Use the existing redacted `ModelError` categories. CLI success is one JSON object
on stdout; failure is a final JSON error on stderr with no success output. Never
echo raw customer records, provider secrets or arbitrary backend exception text.

## Model execution and provenance

Parent orchestration verifies inputs and outputs. The isolated worker performs
actual pinned MLX operations with typed request/result contracts, a private log,
an enforced deadline and process-group cancellation. Child success alone is not
enough to publish an artifact. Independently inspect its actual output files.

Use `ArtifactTransaction` for immutable outputs. Preserve recursive parent
identity, all source-rights fields, precision history and leakage indexes. Never
add overwrite flags, automatically delete stale locks or silently repair hashes.
Only setup, fetch and curate download. Curation generation may call the
configured loopback model endpoint or an explicitly authorized private-network
endpoint; training and evaluation stay local and offline.

Research/noncommercial data may be used when its terms permit the activity.
Commercial restrictions remain recorded through ancestry. Private repository
visibility is not a permission grant. Never accept gated agreements or upload
data without authorization.

Keep data, weights, adapters, checkpoints, predictions and run logs outside Git.
Tests construct minimal temporary inputs in code. Do not commit dataset fixtures.
The tiny setup model tests the toolchain; it is not a financial quality benchmark.

## Verification

```sh
uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests
uv run --project model --no-sync mypy --config-file model/pyproject.toml model/src
uv run --project model --no-sync ruff check model/src model/tests scripts
uv run --project model --no-sync python scripts/generate_model_schemas.py --check model/schemas
uv run --project model --no-sync python scripts/check_docs.py
uv run --project model --no-sync python scripts/check_tracked_data.py
git diff --check
```

Default pytest excludes native integration tests. Run these explicitly in a
permitted native Metal environment with an approved local model:

```sh
FOLIQANT_TEST_SETUP=/absolute/path/to/completed/setup \
FOLIQANT_TEST_MODEL=/absolute/path/to/completed/setup/downloads/model \
  uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests -m integration
```

Real lifecycle acceptance must additionally cover the published CLI, shared and
customer lineage, held-out evaluation, policy selection/audit, merged exports,
and independent inference. Unit doubles are appropriate for failure boundaries;
they never replace real acceptance or produce claimed model results. Keep
observed evidence in `plans/reviews/`, not in end-user setup instructions.

Update contracts, schemas, runnable recipes, guides and skills together. Do not
document a command until its implementation exists. No compatibility or model
quality claim is valid without corresponding recorded execution evidence.

## Python package async boundaries

Run package checks from the repository root:

```sh
uv run --no-sync python -m pytest tests
uv run --no-sync mypy --config-file pyproject.toml src
uv run --no-sync ruff check src tests scripts
uv run --no-sync python scripts/generate_schemas.py --check
```

Live model tests are excluded by default and need explicit
authorization; deterministic adapter tests must not discover or call endpoints.

Prefer native async I/O. Blocking-only SDK operations use the owned bounded
`BlockingExecutor`; never free their capacity merely because a caller cancelled
or timed out. The SDK must have its own network timeout. Reject excess work before
creating internal tasks. No request-scoped mutable identity/authentication state
belongs on a shared adapter. Verify context isolation and cancellation at actual
integration boundaries, not only with primitive semaphore tests.

Use fixed safe logging events. Only sanitized JSON strings enter the bounded log
queue, never raw records or exceptions. Keep shutdown joins off the event loop
and check incomplete-drain results. Compilation is startup work; execution uses
frozen plans and immutable accepted input. The library pipeline is in-memory only: do not add persistence, job queues,
background workers, application authentication or HTTP/Redis ingress. Transport
wrappers belong to examples or the embedding application. MCP OAuth is outbound
tool support, not application authentication. Do not assume cancellation makes
remote mutations safe to retry.

Evaluation lives in `foliqant.evaluation`, separate from model lifecycle calibration. Reuse `run_step` for isolated step checks; never duplicate executors or bypass native output validators. Track failed and skipped expectations explicitly. A prompt selected on development cases must be rechecked against untouched holdout cases. Reports never silently retain raw inputs/results, and evaluator failures must not become successful checks.

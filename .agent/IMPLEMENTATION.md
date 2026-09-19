# Implementation conventions

The local model lifecycle is implemented in `model/src/foliqant_model`. Its
canonical requirements are in `specs/`; end-user guides are in `docs/`. The
configurable workflow service remains a separate proposal. Do not implement it
or add PURISTA, Harness or Voyage dependencies as part of model-tooling work.

## Code and contracts

Use CPython 3.12, the committed uv lock and strict mypy. Public functions are typed
and have concise docstrings. Keep MLX imports inside the isolated backend so
offline commands work without GPU libraries. Do not change global Python or
operating-system memory settings.

Pydantic classes in `contracts/` within the Python package are canonical. The
root `contracts/model/` directory contains generated JSON Schema, not a second
handwritten source. Regenerate reviewed contract changes with
`scripts/generate_model_schemas.py --maintenance-output contracts/model`, then
run the drift check. External JSON/YAML must pass strict runtime validation.

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
configured loopback model endpoint; training and evaluation stay local and
offline.

Research/noncommercial data may be used when its terms permit the activity.
Commercial restrictions remain recorded through ancestry. Private repository
visibility is not a permission grant. Never accept gated agreements or upload
data without authorization.

Keep data, weights, adapters, checkpoints, predictions and run logs outside Git.
Tests construct minimal temporary inputs in code. Do not commit dataset fixtures.
The tiny setup model tests the toolchain; it is not a financial quality benchmark.

## Verification

```sh
.venv/bin/python -m pytest model/tests
.venv/bin/mypy model/src
.venv/bin/ruff check model/src model/tests scripts
.venv/bin/python scripts/generate_model_schemas.py --check contracts/model
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/check_tracked_data.py
git diff --check
```

Default pytest excludes native integration tests. Run these explicitly in a
permitted native Metal environment with an approved local model:

```sh
FOLIQANT_TEST_SETUP=/absolute/path/to/completed/setup \
FOLIQANT_TEST_MODEL=/absolute/path/to/completed/setup/downloads/model \
  .venv/bin/python -m pytest model/tests -m integration
```

Real lifecycle acceptance must additionally cover the published CLI, shared and
customer lineage, held-out evaluation, policy selection/audit, merged exports,
and independent inference. Unit doubles are appropriate for failure boundaries;
they never replace real acceptance or produce claimed model results. Keep
observed evidence in `plans/reviews/`, not in end-user setup instructions.

Update contracts, schemas, runnable recipes, guides and skills together. Do not
document a command until its implementation exists. No compatibility or model
quality claim is valid without corresponding recorded execution evidence.

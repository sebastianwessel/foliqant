# Foliqant contributor guide

Read `README.md`, `specs/README.md`, and the relevant area README before changing code. `specs/research/model-research.md` owns model research; `specs/11-python-package.md` owns the reusable Python package. `specs/research/background/` is historical context, not a source of implementation requirements.

The local model lifecycle is implemented according to `specs/`; `docs/` is end-user material only. The reusable Python package lives at `src/foliqant/` in the root uv project. Model training/curation has its own uv project at `model/`. Shared decisions ship in `foliqant.decisions`. Its scope is in-memory intake, configured steps and a returned result. Persistence, job queues/workers, application authentication and inbound transport implementations are out of scope; a small HTTP wrapper belongs only in examples. Outbound model/MCP clients, MCP OAuth, protected caller metadata and safe telemetry remain in scope. Read its specification and current implementation status; do not describe planned capabilities as implemented. Keep runtime dependencies free of model training dependencies. Real workflows belong in `examples/`; do not add placeholder infrastructure directories.

Keep model development, customer customization, model serving, and workflow orchestration separate. Do not add dependencies on PURISTA, Harness, or Voyage without a new explicit decision. No Voyage work is in scope.

Follow [.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md) for conventions. Preserve user edits. Keep changes bounded; do not invent APIs, compatibility shims, runtime fallbacks, or production guarantees to fill an undefined contract. Record a missing contract in the architecture document before implementing it.

Use temporary fixtures or test doubles for isolated failure tests; real model execution is required for lifecycle acceptance. Keep customer data, secrets, model weights, generated datasets, and evaluation holdouts out of Git. Paid compute, downloads of large model artifacts, and deployment are separate from repository setup.

Verification: `uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests`, `uv run --project model --no-sync mypy --config-file model/pyproject.toml model/src`, `uv run --project model --no-sync ruff check model/src model/tests`, `uv run --project model --no-sync python scripts/generate_model_schemas.py --check contracts/model`, and `git diff --check`. Verify current CLI help and real behavior before documenting commands. Use the schema generator maintenance flag only for reviewed repository schema updates, never as an artifact overwrite path.

Update schemas, examples, area docs, and compatibility notes together when a public contract changes. Version workflow/model artifacts explicitly. Never relabel an old artifact without recording the change.

Downloaded or prepared datasets, model weights, adapters, checkpoints and training outputs must never be committed. Keep only source manifests, pinned download references, preparation code, configuration and documentation in Git. Unit tests construct minimal records in temporary directories; do not add dataset fixture files. The setup command must create a reusable local data workspace outside the checkout by default, verify cached inputs, and never begin training implicitly. Customer data is never a setup download default.

Private research may include datasets whose terms permit research or non-commercial use. Track license evidence, attribution, commercial-use assessment and restrictions through model ancestry. Do not impose a commercial-only dataset policy, infer permission from private repository visibility, bypass access terms, or claim trained descendants are commercially cleared without the appropriate review.

Default pytest excludes native model integration tests. Run them explicitly with `pytest -m integration` in a permitted native Metal environment and an explicitly selected local test model; they remain required acceptance checks, not optional substitutes for the offline suite. Do not interpret a sandbox-native abort as unsupported host hardware.

For the full native lifecycle test, set `FOLIQANT_TEST_SETUP` to a completed local setup directory and `FOLIQANT_TEST_MODEL` to its `downloads/model` directory. Run `pytest -c model/pyproject.toml model/tests -m integration`; no test downloads models implicitly.

Package checks: `uv run --no-sync pytest tests`, `uv run --no-sync mypy src`, `uv run --no-sync ruff check src tests examples scripts`, and `uv run --no-sync python scripts/generate_schemas.py --check`. Evaluation cases use explicit expected answers, default to sequential requests and never count schema validity or model confidence as accuracy. Keep golden corpora and result reports outside Git; small synthetic examples are code-created demonstrations, not production quality evidence.

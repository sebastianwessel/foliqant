# Foliqant contributor guide

Read `README.md`, `specs/README.md`, and the relevant area README before changing code. `specs/research/model-research.md` owns model research; `specs/research/workflow-service-proposal.md` owns the current service proposal. `specs/research/background/` is historical context, not a source of implementation requirements.

The local model lifecycle is implemented according to `specs/`; `docs/` is end-user material only. The workflow service remains a scaffold. Do not describe planned capabilities or illustrative YAML as implemented. The language recommendation and configuration syntax remain proposals until selected.

Keep model development, customer customization, model serving, and workflow orchestration separate. Do not add dependencies on PURISTA, Harness, or Voyage without a new explicit decision. No Voyage work is in scope.

Follow [.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md) for conventions. Preserve user edits. Keep changes bounded; do not invent APIs, compatibility shims, runtime fallbacks, or production guarantees to fill an undefined contract. Record a missing contract in the architecture document before implementing it.

Use temporary fixtures or test doubles for isolated failure tests; real model execution is required for lifecycle acceptance. Keep customer data, secrets, model weights, generated datasets, and evaluation holdouts out of Git. Paid compute, downloads of large model artifacts, and deployment are separate from repository setup.

Verification: `.venv/bin/python -m pytest model/tests`, `.venv/bin/mypy model/src`, `.venv/bin/ruff check model/src model/tests`, `PYTHONPATH=model/src .venv/bin/python scripts/generate_model_schemas.py --check contracts/model`, and `git diff --check`. Verify current CLI help and real behavior before documenting commands. Use the schema generator maintenance flag only for reviewed repository schema updates, never as an artifact overwrite path.

Update schemas, examples, area docs, and compatibility notes together when a public contract changes. Version workflow/model artifacts explicitly. Never relabel an old artifact without recording the change.

Downloaded or prepared datasets, model weights, adapters, checkpoints and training outputs must never be committed. Keep only source manifests, pinned download references, preparation code, configuration and documentation in Git. Unit tests construct minimal records in temporary directories; do not add dataset fixture files. The setup command must create a reusable local data workspace outside the checkout by default, verify cached inputs, and never begin training implicitly. Customer data is never a setup download default.

Private research may include datasets whose terms permit research or non-commercial use. Track license evidence, attribution, commercial-use assessment and restrictions through model ancestry. Do not impose a commercial-only dataset policy, infer permission from private repository visibility, bypass access terms, or claim trained descendants are commercially cleared without the appropriate review.

Default pytest excludes native model integration tests. Run them explicitly with `pytest -m integration` in a permitted native Metal environment and an explicitly selected local test model; they remain required acceptance checks, not optional substitutes for the offline suite. Do not interpret a sandbox-native abort as unsupported host hardware.

For the full native lifecycle test, set `FOLIQANT_TEST_SETUP` to a completed local setup directory and `FOLIQANT_TEST_MODEL` to its `downloads/model` directory. Run `pytest model/tests -m integration`; no test downloads models implicitly.

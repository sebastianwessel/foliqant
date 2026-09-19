# Foliqant contributor guide

Read `README.md`, `docs/architecture.md`, and the relevant area README before changing code. `docs/research-and-concept.md` owns model research; `docs/architecture.md` owns the current service proposal. `docs/background/` is historical context, not a source of implementation requirements.

This repository is a scaffold. Do not describe planned capabilities or illustrative YAML as implemented. The language recommendation and configuration syntax remain proposals until selected.

Keep model development, customer customization, model serving, and workflow orchestration separate. Do not add dependencies on PURISTA, Harness, or Voyage without a new explicit decision. No Voyage work is in scope.

Follow [.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md) for conventions. Preserve user edits. Keep changes bounded; do not invent APIs, compatibility shims, runtime fallbacks, or production guarantees to fill an undefined contract. Record a missing contract in the architecture document before implementing it.

Use fixtures or fake adapters before calling models. Keep customer data, secrets, model weights, generated datasets, and evaluation holdouts out of Git. Paid compute, downloads of large model artifacts, and deployment are separate from repository setup.

Verification available today: `git diff --check` and JSON syntax validation with `python3 -m json.tool <file>`. There is no application build, test suite, workflow validator, or training command yet. Add actual executable verification commands alongside the first implementation; do not document placeholder commands as working.

Update schemas, examples, area docs, and compatibility notes together when a public contract changes. Version workflow/model artifacts explicitly. Never relabel an old artifact without recording the change.

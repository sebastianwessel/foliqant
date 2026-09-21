# Documentation and skills

The public MkDocs site starts at `docs/index.md`. It has two task-oriented
paths: runtime installation/workflow authoring/evaluation, and separate local
model setup/data/training/evaluation/export. Users need no specs or review history.

Explain supported behavior before runnable commands, what each command produces,
and how to recognize success. Public guides describe the current product; research,
proposals, implementation status and review evidence belong in `specs/` or
`plans/`. Link shared facts instead of duplicating complete field inventories or
internal implementation histories. Keep model quality and financial suitability
claims distinct from toolchain and synthetic-example success.

Use the root uv project's documentation group:
`uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8001` previews the site;
`uv run --group docs mkdocs build --strict` verifies it. CI runs the strict
build; GitHub Actions is configured to publish to GitHub Pages when enabled
and available for the repository. Keep all published navigation
in `mkdocs.yml`; local repository links must also remain valid.

Commands must match current CLI help. Examples use declared dependencies and
repository-relative recipes, never author-machine paths, fake outputs or hidden
environment hacks. Runtime setup installs the library without model-training
dependencies. Model setup explains uv, the external local workspace, immutable
assets, offline reuse and separate explicit training. Data guides distinguish
research/noncommercial permissions from commercial clearance.

## Agent skills

`skills/foliqant/` covers runtime workflows, adapters, step/pipeline evaluation
and examples. `skills/foliqant-model/` covers local setup, curation, training,
calibration and export. Both are usable from public guides and CLI help without
internal specs/plans. Keep the skill entry concise and procedural; load focused
references for fragile operations. Human explanations belong in `docs/skills/`.

Skills must preserve actual callable contracts, default values, input validation,
data isolation and immutable ancestry. Missing behavior is a reported gap, not a
reason to invent commands, fallbacks, model judges or infrastructure. Existing
user authorization remains valid within its scope; do not introduce redundant
approval steps. New paid services, data uploads and gated terms require authority.

Maintain realistic trigger, boundary and failure scenarios in each skill's
`evals/evals.json`. These are review prompts, not claimed model execution results.
The docs audit checks local links and both CLIs' documented commands/arguments;
schema, typing, architecture and functional tests verify actual behavior.
Deterministic checks complement content review and separately authorized live
acceptance; they cannot establish statistical model accuracy.

# Configuration DX refactor

User-authorized scope: simplify authoring, validate workflows offline, use explicit
environment references, evaluate every example, and keep model assets in the
ignored repository workspace. No inference, training or source downloads are
needed to implement or verify this refactor.

## Required completion evidence

- Inline or file-based steps compile through one path; mixed layouts fail.
- Inline/file schemas reuse validation; ambiguous Markdown prompts fail.
- Explicit successful transitions, graph coverage, dataflow checks and safe
  actionable diagnostics; conservative schema checks do not claim business proof.
- Deployment-only `$NAME` references resolve once at startup; `$$` escapes;
  missing values fail safely; offline validation never needs credentials or I/O.
- All runnable examples use current APIs and have step/pipeline evaluations
  with explicit expectations and documented offline/live boundaries.
- Shared model workspace defaults to `./.foliqant`; repository wrappers anchor
  it to the repository; Git ignore and staged-data audit protect contents.
- Existing local assets move without changing file contents or immutable history.
- Specs, schemas, examples, docs, skills and CLI describe the same behavior.
- Offline tests, types, lint, schemas, docs and staging audits pass; commit and push.

## Completion evidence — 2026-09-21

- Runtime tests: 630 passed. Model-tooling tests: 939 passed, 7 integration
  tests deselected. No inference, training or source downloads were performed.
- Both projects passed Ruff lint/format, strict mypy, lock checks and generated
  schema checks (12 library schemas and 24 model schemas).
- Documentation audit passed (40 guides/skill references, 49 CLI examples);
  strict MkDocs build, specification audit and both skill audits passed.
- All three example evaluation commands passed offline: support pipeline and
  isolated classification/extraction, MCP pipeline and isolated lookup, and
  HTTP pipeline. A negative golden-data test verifies evaluation failure exits.
  These check application wiring, not model accuracy.
- Moved 11,305,010,905 bytes of local assets into ignored `.foliqant/`, preserving
  all 9,023 inventory entries. The old location is a symlink so absolute paths
  in immutable artifacts remain valid. The native-decisions artifact verified
  at its new location with its unchanged artifact ID.
- Reviewed the staged changes; tracked-data audit and whitespace checks passed.
  `.env` and `.foliqant/` remain ignored and untracked.

See [the configuration review](reviews/configuration-dx-2026-09-21.md) for
compiler findings, metadata checks and verification limits. Commit and push are
the final delivery steps; their result is recorded in the task response.

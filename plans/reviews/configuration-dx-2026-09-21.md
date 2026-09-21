# Configuration and workflow DX review — 2026-09-21

Scope: the authorized configuration/compiler refactor, its workflow guide,
shared model workspace policy, and corresponding spec/skill metadata. This is
bounded implementation evidence, not a full-suite or production approval.

## Result

- Workflow authoring supports inline steps or separate YAML/Markdown files,
  with inline/file JSON Schemas passing through the same compiler. Ambiguous
  layouts and duplicate instruction sources fail; success routes terminate
  explicitly. Existing unresolved outcomes still end safely in `needs_review`.
- Preparation checks declared model capabilities and conservative binding
  paths/types; runtime validation remains authoritative for actual values,
  open/complex schemas and partial compatibility.
- Shared environment contracts and `EnvironmentResolver` define annotated
  deployment-field resolution. Authored references and ephemeral resolved
  settings have an explicit representation mapping; secret values remain
  redacted from exports and revisions. `open_application` loads config-local
  `.env`, with explicit environment values taking precedence.
- `model/src/foliqant_model/workspace.py` owns the shared workspace default and
  Git exclusion policy. Setup, curation and migration reuse it. Registry/module,
  reuse, generation and traceability metadata now point to the implementation
  and the example evaluation tests. No new capability or wire contract ID was
  needed: these surfaces belong to the existing package/setup contracts.

## Compiler review findings resolved

1. Output dominance now accounts for an implicit `needs_review` terminal from
   every operation kind, not only decisions.
2. An optional binding to a schema-proven absent path retains its explicit
   fallback. Untyped object-only schema keywords do not wrongly reject valid
   numeric array paths.
3. Graph traversal is iterative, so a long acyclic workflow does not fail on
   Python's recursion limit. Excessive schema-reference recursion produces a
   safe compiler diagnostic.
4. Error details identify safe source/field/reason/hint values without raw
   rejected values. A remote-schema sentinel regression verifies local
   rejection without opening a connection or exposing the URL/token.

## Verification recorded by this review

- `uv run --no-sync pytest tests/test_environment.py tests/test_example_evaluations.py tests/test_compiler.py tests/test_runner.py tests/test_workflow_telemetry.py -q`: **130 passed**.
- `uv run --project model --no-sync python -m pytest -c model/pyproject.toml model/tests/test_workspace.py model/tests/test_setup.py -q`: **9 passed**.
- Focused compiler/authoring Ruff and strict mypy: passed.
- All workflow-guide YAML/Markdown forms compiled from the actual documentation;
  the public `prepare_application` example and model-free CLI run were executed
  offline, returning the documented `hello` payload and completed status.
- Documentation audit: **40 guides/skill references and 49 CLI examples passed**.
- Tracked-data audit and strict MkDocs build: passed.
- Initial installed `check_specs.mjs` audit after metadata changes: passed.

## Final metadata and skill checks

- Installed `generate_spec_manifest.mjs` regenerated the manifest and provenance
  with source revision `working-tree-baabcfd-configuration-dx-2026-09-21` and
  digest `sha256:525781bf6ba1f83dde518e17036753173c38b46e7296b54a581ad8449fb9af38`.
- Installed `check_specs.mjs specs`: passed after regeneration. The readiness
  report remains draft; the new digest does not assert human or production
  approval.
- Installed `check_skill.mjs` exited successfully for both `skills/foliqant`
  and `skills/foliqant-model`. Nonblocking structural suggestions remain:
  runtime SKILL.md exceeds 120 lines; the model setup/data reference has six
  links to other Markdown documents.
- `plugin-eval analyze` completed locally for both skills: 100/100, no failures
  or warnings. These are static structure/budget analyses, not behavioral
  benchmark results. Authored positive and near-miss eval prompts were reviewed;
  no model-backed skill benchmark or observed token-usage run was performed.
- The final reuse map also registers `load_curation_environment` and
  `apply_curation_environment` in the shared curation environment module. The
  CLI and task-prefix evaluation reuse the library dotenv parser and shared
  workspace policy instead of separate parsers or setup-owned Git-policy code.

## Root-agent relocation evidence

The root agent reported an atomic relocation of 11.305 GB of local model data,
with 9,023 entries preserved, the native artifact retaining its verified ID,
and an old-path symlink retained for immutable ancestry. This reviewer did not
repeat the data inventory or artifact verification; these statements are
attributed handoff evidence, separate from the checks executed above.

No whole-repository test result is inferred from these focused checks. This
review made no model calls, downloads, training runs or publishing changes;
example tests use offline doubles or local protocol fixtures.

## Final root-agent verification

After integration, the full runtime suite passed 630 tests and the full model
suite passed 939 tests (7 integration tests deselected). Both projects passed
lint, format, strict type, lock and generated-schema checks. The strict docs
build, documentation audit and staged tracked-data audit passed. The three
example evaluation commands also passed using scripted models or local protocol
fixtures; no model inference was performed.

The root agent independently verified the relocated native-decisions artifact:
`844c86503e824db6c90442f54ec7a9759c9ae358bbc0e393f95b9837a9dc587d`,
valid with 9 files. This final verification supplements the bounded reviewer
checks above; it does not establish live-provider or model-quality acceptance.

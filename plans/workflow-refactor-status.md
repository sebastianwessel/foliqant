# Workflow refactor execution status

Started 2026-09-22 from `eecba17`. Status: completed and verified on 2026-09-22.
Authority: the owner explicitly requested all discussed workflow, configuration,
context, security and cache improvements, with aligned specs/docs/skills and real
evaluation. This supersedes the proposal's former no-implementation/no-inference
restriction. Model curation, training and fine-tuning remain deferred. No push.

Design references: [workflow proposal](workflow-composition-and-context.md),
[cache research](../research/prompt-order-cache-and-evaluation.md).

## Frozen implementation choices

- One workflow contains named flow instances. Each flow definition is a nonempty
  ordered list of operations. Steps have no routes or finish operation.
- Flow instances declare `definition` (inline, explicit file or conventional lookup), named `input` bindings,
  `transition`, and optional `on_unresolved`. Named steps use an ID shorthand or declare `id` and
  optional `definition` (inline or file). Convention lookup follows spec 11; no compatibility mode.
- Targets are `{flow: id}` or `{outcome: completed|needs_review}`; exact-match
  routing has `binding`, `cases`, and required `default`. No coercion/expressions.
- Uncertainty stops the sequence before success routing. A direct unresolved
  target cannot claim completion. An explicit review-handling flow may finish
  successfully while the earlier flow report retains its unresolved assessment.
- Flow output is projected on completed/review outcomes. References to steps
  which may not execute require optional bindings with explicit defaults.
- Bindings inside flows see their local `/payload`, `/metadata`, `/steps`.
  Workflow boundaries see root `/payload`, `/metadata`, `/flows/<id>/result`.
  No cross-flow access to internal steps or shared AI transcript.
- Results expose `flows` with local `steps`, plus selected `transitions` and root
  execution totals. No flattened `decisions` alias. APIs include isolated
  `run_flow(workflow, flow, envelope)` and `run_step(workflow, flow, step, envelope)`.
- LLM templates compile once; `{{ name }}` substitutes an exact declared input.
  `{{{{`/`}}}}` escape doubled braces. Every substitution is JSON-encoded,
  including strings. No recursive rendering, executable syntax or env expansion.
- Decision sources may explicitly choose `format: json`; default remains text.
  Training data contracts and historical artifacts are outside this runtime refactor.
- Static trust policy distinguishes compiler-owned task criteria from untrusted
  documents and derived results. Permissions/routing remain deterministic.

- User-authored projects default to `config/settings.yaml`; filenames remain
  explicitly configurable. Scaffold environment references use `MODEL_ID` and
  `MODEL_BASE_URL`. Prompts/business results use domain terminology, not branding.
  Distribution/import/CLI identifiers remain `foliqant` until a replacement is
  chosen. Runtime format-version fields are removed; immutable model artifacts stay untouched.

- Convention discovery resolves workflow/flow/step definitions, never business
  execution order. Explicit lists, routes and selected input bindings remain.
- Public synthetic example evaluation JSON lives in each example's `evaluation/`
  directory and is committed. Reports and real customer/model data remain private.

## Completion evidence

| Requirement | Result |
| --- | --- |
| Workflow/flow/step contracts, file confinement and conventional discovery | Implemented; inline/conventional equivalence, missing/ambiguous files, symlink loops, schemas, routes and bindings verified |
| Sequential execution, context isolation and shared limits | Run/flow/step, early review, cancellation, deadline, admission and budget tests pass; independent semantic audit found no further defects |
| JSON sources and safe prompt templates | Compiled exact placeholders; JSON preservation, trust boundaries and fresh-conversation capture tests pass |
| CLI, examples and application lifecycle | Scaffold, validate/explain, installed wheel/core smoke tests, real local stdio MCP and HTTP wrapper checks pass |
| Evaluation scopes, reports and gold | Pipeline/flow/step checks and replay/comparison pass; gold stays optional at runtime startup |
| Committed synthetic gold | Seven identical case arrays shared within their examples; all seven expanded datasets and 26 suite fingerprints preserved, saving 9,578 lines |
| Skill and public guides | Self-contained configuration/design references; six-suite independent downstream setup test, compiled snippets, docs audit and strict site build pass |
| Security/cache measurements | 134 serialized local model requests completed; full results retained privately, aggregate findings reviewed; default order retained because the promising cache candidate introduced a classification regression |

Final offline gates:

- Runtime: **1,194 tests passed** (including package installation and wheel checks).
- Model tooling: **952 tests passed, 7 native integration tests deselected**;
  no model-generation/training code changed. The prepared model environment had
  an older installed root wheel, so these checks used `PYTHONPATH=src:model/src`
  to verify current source without changing the environment.
- Strict mypy: **115 runtime/example/research files**, **71 model files** clean.
- Ruff lint/format: **196 runtime/example/research files**, **132 model files** clean.
- Generated schemas: **15 library files**, **24 model files** match.
- Documentation audit: **47 guides/references, 59 CLI examples**; strict MkDocs,
  skill validation, spec check and diff checks pass.
- Tracked-data audit passes after staging. `.env`, generated predictions/reports,
  datasets, model weights, caches and environments are excluded; only authored
  public synthetic example gold is tracked. Negative audit controls verify
  private configuration, nested generated artifacts and environments are rejected.

See [the measured review](reviews/workflow-composition-2026-09-22.md) for every
live run, methodological limits and the case-level semantic findings. The default
security suite passed 172/172 checks across 20 inputs; broader model evidence
matched 421/437 checks and 10/15 complete cases. The candidate improved aggregate
matches but introduced a new criterion-following error. Neither layout is
production-qualified by these small authored fixtures. Those model limitations
remain visible rather than being hidden by changed gold or relaxed validation.

Model generation, training and fine-tuning remain deferred. No cloud inference,
additional product infrastructure or compatibility layer was added. The work is
committed locally; publishing/pushing is outside this execution.

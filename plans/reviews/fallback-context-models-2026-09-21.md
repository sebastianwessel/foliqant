# Fallback categories, explicit context, and step models

Baseline: `254605c`. User approved implementation after the fallback review,
with a defined fallback category and optional multiline description. Examples
and counterexamples belong in descriptions, not additional catalog fields.

## Scope and acceptance

1. Optional deterministic fallback for single-choice decision steps. Category
   keys reuse existing normalization; the definition is separate from the model
   catalog. Original answerability, answer, explanations and citations remain
   unchanged. A public selection records category and model/fallback origin.
   All nonempty reported issues must be explicitly covered; unresolved or
   technical failures never become successful classifications implicitly.
2. Issue-aware `on_unresolved` routing retains the existing string shorthand.
   A map has a required default and optional issue targets. All reported issues
   must agree on one target; otherwise use default. No inferred priority.
3. Evaluate original classification, effective selection, fallback frequency,
   and route correctness separately. Extend authored English/German examples
   with clear no-match versus missing-information contrasts, including cases
   where fallback must not apply. No gold derived from model responses.
4. Demonstrate selected prior-step results as MCP arguments, reusing explicit
   bindings and optional defaults. No ambient context, duplicate binding DSL,
   implicit history forwarding, or additional context endpoint.
5. Allow step model aliases, a profile with model/options overrides, or a full
   provider configuration. Reuse provider validation, environment resolution,
   ownership and admission. Profile-derived variants share admission limits;
   no discovery, hidden retries, or secrets in public metadata/revisions.
6. Align generated schemas, docs, examples, skills and active specs. Run
   affected offline tests, strict typing, lint/format, schema/link/spec/skill
   audits and strict docs build. Real local model checks run sequentially only
   after code is stable and no data generation is active. Preserve all results.

## Reuse and ownership

- Contracts/compiler/core: existing authoring models, frozen plans and runner.
- Provider configuration: existing ModelConfig/options and owned model factory.
- Context: existing tagged literal/pointer bindings and dominance/type checks.
- Evaluation: existing report, metrics, replay and synthetic example helpers.
- Native decision training contract and curation recipes remain unchanged.
- No external dependencies, persistence, queues, authentication or transports.

## Confidence follow-up

The user explicitly requested a discussion after this implementation. Do not
silently replace calibrated model-tooling scores or add an invented evidence
score. Inspect current native evidence/reason fields and explain how a later
per-category evidence design would differ from probability/calibration.

## Status

Implementation, offline integration and evidence review complete. Local-model
acceptance is unverified, as documented below. No model-training contract or curation artifact
was changed.

Offline verification on this working tree:

- Runtime: 855 tests passed, including 43 fallback/routing tests, 32 per-step
  model tests, and 4 extraction-to-MCP tests.
- Model tooling: 939 passed, 7 opt-in integration tests deselected.
- Strict mypy: 104 runtime/example files and 68 model-tooling files passed.
- Ruff lint and formatting passed; 13 runtime and 24 model schemas match.
- Documentation audit: 43 guide/skill references and 59 CLI examples passed;
  strict MkDocs build passed. Spec manifest/check and tracked-data audit passed.
- Skill quick validation passed. Architecture lint reports only advisory size
  and cross-reference warnings, not broken references or errors.
- Independent agent review found no concrete model lifecycle/credential defects.
  Repeated-report replay and language grouping tests preserve native results,
  selection origins and denominators, including missing/error observations.

The live attempt used configured Qwen sequentially with low reasoning and
temperature 0.1, after confirming no generation processes were active. It produced
no suite report after more than eight minutes, so the owned evaluator was
interrupted (exit 130). No completed live results or quality improvement are
claimed. Client shutdown does not establish remote cancellation. Confirm the
backend is idle before another live attempt. Context/MCP/HTTP live follow-ups
were not started. Private attempt metadata is in
`.foliqant/evaluations/fallback-context-models-20260921T210620Z/`.

The explicit scripted support example passed all 184 checks across 30 pipeline
and isolated-step executions. Its ignored report is
`.foliqant/evaluations/fallback-offline-final.json`. It is wiring evidence only.
No private dataset, model output, environment file or model weight is committed.

## Evidence discussion to follow

The current native contract has no numeric confidence field. It already keeps a
concise explanation, supporting citations, contrary citations, missing facts and
answerability issues. This task preserves those fields and the separate model
calibration toolchain. A useful next discussion is option-specific evidence,
so evidence for two different categories remains inspectable when single-choice
triage cannot choose one. This would require explicit contract, prompt, training
and evaluation decisions; no evidence score or schema migration is implied here.

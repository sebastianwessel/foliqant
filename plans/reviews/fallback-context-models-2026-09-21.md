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

Implementation, offline integration and evidence review complete. The initial
live attempt was interrupted; the backend-restart verification below establishes
working integration with remaining model-quality failures. No model-training
contract or curation artifact was changed.

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

## Verification after backend restart

The user restarted the backend and authorized another run. On the implementation
commit `deb240c`, one pilot classification completed in 11.91 seconds, followed
by the standard suites sequentially. The configured model remained
`incoai/Qwen3.8-27B-Splash`, reasoning low, temperature 0.1. No model discovery,
parallel inference, prompt changes, or automatic retries were used. Model identity
is the configured ID, not a verified weights digest.

| Suite | Checks passed | Attempts | Runtime failures |
| --- | ---: | ---: | ---: |
| Support pipeline and isolated steps | 181/184 | 30 | 0 |
| Extraction to MCP, original gold revision 1 | 38/42 | 6 | 0 |
| Extraction to MCP, corrected gold revision 2, offline replay | 38/38 | same 6 | 0 |
| Local MCP pipeline and isolated step | 28/28 | 4 | 0 |
| HTTP/ASGI boundary with live model | 108/110 | 12 | 0 |

There were 52 evaluated attempts and 58 model requests, plus the one-request
pilot. Repeated pipeline/step/HTTP uses are not independent gold examples.
Support suites took 259.11 seconds, extraction/MCP 15.02 seconds, model-free MCP
1.83 seconds, and HTTP 105.49 seconds. All completed without timeouts or runtime
failures. This is small synthetic integration evidence, not a production-quality
or calibration claim.

Two kinds of mismatch were distinguished independently:

- **Model diagnosis:** English `insufficient_information` failed its issue check
  in both pipeline and isolated classification, and its pipeline clarification
  route. HTTP failed the equivalent German case's issue and route checks. The
  model returned `no_matching_option` instead of `missing_information`. Original
  answers remained null and the configured `misc` fallback was applied correctly,
  but the issue-specific policy consequently chose manual triage rather than
  clarification. Category/origin metrics alone would conceal this defect. The
  gold and prompts for these cases remain unchanged.
- **Invalid evaluation assertion:** the extraction workflow requests a free-form
  short summary, while revision 1 required arbitrary exact English/German wording.
  References, languages and tool outcomes were correct. Revision 2 removes only
  those four unjustified wording checks; it does not copy model output into gold
  or weaken reference/language checks. Summary presence/type remains validated by
  the runtime schema; semantic faithfulness is not scored. Original reports remain
  immutable. The saved results were rescored through `evaluate_configuration`
  with the identical runtime digest and client opening forbidden; no further
  inference was needed.

Private reports, per-call snapshots, configuration/model metadata, corrected gold
and replay results are preserved under
`.foliqant/evaluations/restart-retry-20260921T212814Z/`. These remain ignored.

For the evidence discussion, this run demonstrates that inspectable evidence is
not itself correctness: the English failure explicitly listed a missing specific
request in `missingFacts`, yet emitted the wrong issue code. Future experiments
should measure evidence relevance, answerability issues and deterministic route
selection separately, without introducing a synthetic confidence score.

Post-correction verification: 856 runtime tests passed, strict mypy passed for
104 runtime/example files, Ruff lint/format and 13 runtime schema checks passed.
Documentation audit and strict MkDocs build passed. A focused regression accepts
valid paraphrased summaries while rejecting incorrect references and languages.
Model tooling was unchanged; its earlier 939-test result above was not rerun.

# Native decision-data readiness review

Date: 2026-09-19. Status: offline review passed; bounded real execution verified. The
project owner authorized the work; this note is not formal specification
approval, human-gold validation, model-quality qualification, or production
certification.

Historical scope: this review predates the fixed-snapshot quality audit and
canonical-target repair. Its named run remains diagnostic; current generated-data
quality disposition is in
[`generated-data-quality-repair-review.md`](generated-data-quality-repair-review.md)
and [`generated-data-quality-repair-pilot.md`](generated-data-quality-repair-pilot.md).
Do not use this older run as training-readiness evidence.

## Scope and method

This review inspected the native decision contracts, authored and projected
seeds, generation path, serial runner, storage and endpoint boundaries against
`specs/09-native-decision-data.md` and the answerability research. The independent reviewer ran offline unit, type and style checks without
endpoint calls. Root separately performed the bounded local endpoint checks
recorded below. No training or bulk generation was started.

## Fixed findings

- **Generator/checker contract alignment:** the blind solve now receives the
  exact system message that will be stored in the accepted training record,
  followed by the final `DecisionInput`. It does not receive the oracle,
  scenario name or hidden reference. The solver prompt version changed, so old
  cached outcomes cannot be reused under the new behavior.
- **Guided-schema compatibility:** the rewrite call no longer relies on JSON
  Schema `prefixItems`. It requests a bounded array with enumerated source IDs
  and kinds; deterministic validation still requires the exact original order,
  ID and kind for every source.
- **Generation isolation:** native generation rejects parent/job mismatches,
  non-native operations, non-training splits and language changes before an
  endpoint call. Rewrite mode exposes state and immutable questions without the
  target. Annotate mode leaves the complete input unchanged and performs only a
  blind solve.
- **Acceptance semantics:** schema-valid output is checked by
  `validate_decision_output`; its explanation-free semantic signature must
  equal the hidden oracle signature. Generated explanations and citations are
  retained, while citation source and exact-quote validity are checked against
  the candidate input. No confidence or probability field exists in the native
  result.
- **Failure and provenance boundaries:** semantic failures are quarantined
  within the configured attempt bound; transport and integrity failures stop
  the run. Accepted records are `origin=teacher`, `reviewed=false`, inherit the
  parent family/group keys, name their parent record and bind the model,
  prompts, schemas, parameters and request identities.
- **Scenario and answerability coverage:** the authored schedule now reaches
  choice, multiselect, predicate, ordinal, request-unit, partial-answerability,
  condition/dependency, missing-context, dated/financial, prompt-injection and
  whole-response-adequacy cases. Counterfactual groups share family IDs.
- **Wire and specification alignment:** the specification now describes the
  implemented source kinds, typed answer graph, nullable-answer rules,
  request-unit relations, four generic issue codes, and semantic-signature
  acceptance rule. The persisted system instruction names the complete result
  graph and is the same instruction used by the blind solve.
- **Coverage and source lineage:** coverage cells now come from training
  families, including zero-planned training cells under an insufficient job
  budget while excluding held-out-only cells. Accepted dimensions include
  scenario, language, split, type, answerability and issue. Deterministic source
  projections carry conservative source rights, family/split, a non-human
  projected origin, and an `original-source-record:<id>` tag.
- **Request-unit semantic comparison:** request units now carry a required
  nullable `subject`. Non-null subjects must occur verbatim in the unit's
  evidence. The signature compares stable ID, category, status, subject and
  relations while allowing description prose to vary, preserving same-category
  entity distinctions without exact-paraphrase rejection.
- **Pilot selection:** the eight-job English pilot now uses a frozen priority
  list that covers choice, multiselect, predicate, ordinal and request-unit
  outputs plus unknown, partial, conditional and whole-response-adequacy cases.
  A focused test asserts this mix. The runner recipe version changed, preventing
  reuse of an earlier schedule.
- **Combined-family publication:** a prepare-only regression projects five
  valid BANKING77-shaped rows across all four source splits, combines their
  inherited families with authored families, publishes the native seed dataset,
  and verifies projected origin, record-level lineage tags and inherited source
  restrictions. This exercises the sorted combined-family invariant that the
  first real preparation exposed.

## Review disposition

No code-level blocker remains from this review. The bounded endpoint and resume
checks below establish execution for the selected model/runtime and recipe.

## Empirical pilot risks

- `DecisionOutput` produces a comparatively large Draft 2020-12 schema with
  internal definitions, discriminated unions and nullable answers. Local
  validation and the selected LM Studio runtime both accepted this exact schema
  within the frozen token and timeout limits. Other model/runtime combinations
  still require their own bounded pilot.
- Prompt-mode structured output remains an explicit recipe choice when guided
  grammar is incompatible. There must be no automatic fallback because changing
  transport changes the request identity and experimental condition.
- A blind solve by the same local model is a conservative automatic filter, not
  independent human adjudication. Literal citation checks prove occurrence,
  not entailment. The pilot can establish pipeline behavior and observed
  acceptance only; it cannot establish calibrated answerability, production
  prevalence or authorization to automate financial actions.

## Verification observed

- `python -m pytest -q model/tests/test_decision_generation.py`: 6 passed.
- Combined native contract, seed, generation and runner tests passed after
  contract, scenario, coverage, pilot-selection and projection-publication
  changes.
- `mypy` and `ruff` pass for `decision_generation.py` and its focused test.
- The complete offline suite passes: 351 passed and 7 integration tests
  deselected, using the supplied standalone Git runtime for workspace-policy
  coverage.
- Full source `mypy`, source/test/script `ruff`, generated-schema drift (23
  schemas), documentation links/examples, tracked-data audit and `git diff
  --check` pass.

The implementation is ready to start a bounded full data-generation run.
Full-run coverage and generated-data quality remain unverified until that run
finishes and its results are reviewed. This is not production model approval.

## Real local execution evidence

The final frozen recipe ran against the configured private-network LM Studio
endpoint with model `qwen3.8-27b-splash`, explicit JSON Schema output, concurrency
one, eight candidates, one attempt each, 8,192 output tokens and a 300-second
per-call limit. No transport fallback was used.

- Run: `native-financial-decisions-pilot-v1-2ec5a0ef5498` in the external data workspace.
- Six accepted derivatives and two quarantined outcomes from 16 serial model
  calls (247.6 seconds of reported model-call time).
- Accepted scenarios: choice, multiselect, unknown predicate, contradictory
  ordinal input, distinct same-category requests, and omitted-request adequacy.
- Rejected scenarios: partial request extraction and unresolved conditional
  requests. The model overlooked incomplete input in the former and added an
  unsupported prerequisite relation in the latter. The hidden oracle comparison
  rejected both; acceptance checks were not weakened.
- The exact same wrapper command completed again in 9.59 seconds with all 24
  cached call/outcome files unchanged and the same artifact identity. Unit tests
  independently enforce zero generation calls on a completed resume.
- Published native artifact verified as
  `61b126d602294be841d47c75517b190fafb62dfd69683fcf3053da4ef41d772c`;
  the coverage report names that artifact and the configuration, recipe and
  observed model identities.
- Full recipe preparation (without inference) verified 9,600 source records,
  4,200 unreviewed synthetic native parents, and 3,215 training-only jobs across
  34 cells, with at least 72 jobs per cell and at most 11,550 model calls.
  The full run has not been executed; its per-cell acceptance gate can still
  fail, particularly on the two difficult scenarios above.

An earlier diagnostic pilot exposed an order-sensitive multiselect comparison
and underspecified status instructions. Those were corrected before the final
recipe. The runs are not a controlled model-quality comparison: their prompt
and seed identities differ. Six of eight accepted records is an observed
pipeline yield, not an accuracy estimate, calibration result, or evidence of
production readiness. Seven unrelated native training/inference integration
tests remain deselected; no new checkpoint was trained in this work.

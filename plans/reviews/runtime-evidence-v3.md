# Runtime evidence V3 acceptance review

Date: 2026-09-22. Status: runtime refactor verified; known local-model quality limitations remain.
Authority: user-approved runtime refactor and subsequent approval of
`reason` plus `evidence_strength: limited | strong | null`.

## Scope and contract

- Runtime input remains native V2. Runtime output is V3 in
  `foliqant.contracts.decisions`; old native V2 dataset/model contracts remain
  in `foliqant.decisions`. No artifact conversion, training, generation,
  downloads, cloud inference, or publishing was performed.
- All five runtime decision types have a nonblank reason bounded at 400
  characters and required support assessment. Null/unknown abstentions require
  null strength; substantive false and allowed empty collections require a
  non-null rating. Collections rate their weakest returned item; answerability
  remains separate from support strength.
- Full contextual validation preserves membership, cardinality, status/issue,
  request relation, and allowed-source subject checks. Standalone result
  validation prevents inconsistent support/null combinations, including mutated
  model instances and selection boundaries. Results follow input question order.
- No automatic retries, response repair, rating thresholds, route changes,
  provider discovery, extra inference passes, persistence, or background work
  were added. Ratings are model assessments, not measured probabilities.
- Classification evaluation can explicitly include null in its label catalog.
  Expected null then participates in the confusion matrix. Missing, invalid,
  failed and skipped outputs stay distinct. Multilabel remains string-only.
- Specs, generated schemas, guides, examples and both skills describe the runtime
  and model-toolchain boundary. Private reports and exported gold remain ignored.

## Evidence and limits

Offline verification and live examples are recorded separately. Live calls use
local `incoai/Qwen3.8-27B-Splash`, low reasoning, temperature 0.1, output limit
8192, one call at a time. Development gold was authored before observing output.
Pass counts are assertions, not independent examples or accuracy estimates.

Four-case development experiment reports (all under `.foliqant/evaluations/`):

| Variant | Checks | Report |
| --- | --- | --- |
| Initial V3 prompt | 87/117 | `example-report-20260922T005806Z-8a1a0fdb.json` |
| Category-limit clarification | 88/117 | `example-report-20260922T010305Z-58d5cef1.json` |
| Additional unknown-predicate consistency reminder | 88/117 | `example-report-20260922T010456Z-36d05428.json` |
| Answer-first schema property order | 59/117 | `example-report-20260922T010832Z-63eddf59.json` |

The retained prompt clarifies that excess positively supported categories are
`multiple_valid_options`, not automatically `no_supported_answer`. It also
preserves IDs/enums across languages and removes the misleading suggestion that
indirect inference necessarily means limited support. The extra consistency
paragraph and schema-order experiment were discarded. One-pass small-suite
results do not establish a statistically reliable improvement.

The principal failure was an unknown predicate marked answerable. Strict
validation rejected it; no invalid decision reached downstream routing. A
separate diagnostic response reproduced this inconsistency. Adding more prompt
text did not reliably resolve it. This remains a local-model limitation, not a
license to infer or repair the status silently.

Private experiment scripts, prompt texts and diagnostic response bodies are in
`.foliqant/experiments/evidence-v3/`. They are not package runtime code and are not
committed. The two-case post-selection report is
`example-report-20260922T010642Z-077888b7.json` (53/58 checks); an independent review confirmed ambiguity in the authored scope. Revision 2 now
explicitly separates card/statement actions from the dispute dimension. Gold
answers were not changed. Those inspected cases are now called validation cases,
not an untouched holdout. It is not evidence for calibrated support ratings.

Request-unit assertions check expected entries and relationships. Complete unit
counts and the semantic faithfulness of descriptions/reasons also require manual
inspection. A high check count cannot compensate for one invalid full response.

## Offline verification

- Runtime suite: 920 passed. Model-toolchain suite: 952 passed; 7 native training
  integration tests intentionally deselected because training is out of scope.
- Mypy: 107 runtime/example and 71 model files. Ruff lint/format passed.
- Generated schema checks: 14 runtime/shared, 24 model files. Existing native V2
  schemas and model implementation/tests have no diff.
- Documentation audit: 44 guide/skill references and 61 CLI examples. Strict
  MkDocs, both skill validations, spec integrity checker and diff whitespace check
  passed. Model checks use current checkout via `PYTHONPATH=src:model/src`.
- Offline examples: support 260/260, HTTP 156/156, public stdio MCP 28/28,
  extraction-to-MCP wiring 38/38. Focused evidence default only checks
  configuration/gold, without fabricated responses. Exported R9 support/HTTP
  and R2 evidence gold is private; configuration evaluation checks passed.

Independent read-only reviews found and resolved standalone support validation,
indirect-inference wording, translated holdout overlap, and example scope
ambiguity. The request-unit count/description measurement limitation is explicit.
No additional automated guarantee was invented to disguise it.

## Final live verification

Support R9: `example-report-20260922T011332Z-e956fbb1.json`, 254/260 checks.
Full pipelines passed 156/156 (16 cases, 22 calls). Isolated classification passed
80/86 (16 cases/calls): `category_words_without_request` returned invalid output
and was safely rejected. Isolated extraction passed 18/18 (6 cases/calls).
The failed isolated response was not retained in the ordinary report; its exact
invalid field cannot be inferred from the sanitized error. Do not attribute it
to the predicate issue observed in the separate diagnostics.

Additional final reports:

| Example | Checks | Report |
| --- | --- | --- |
| HTTP support, 16 cases | 156/156 | `example-report-20260922T011616Z-a5609a5d.json` |
| Focused evidence R2, 4 development cases | 59/117 | `example-report-20260922T011721Z-35b86cd7.json` |
| Focused evidence R2, 2 additional validation cases | 58/58 | `example-report-20260922T011748Z-dbd9bfe1.json` |
| Extraction-to-MCP, 6 step/pipeline executions | 38/38 | `example-report-20260922T011803Z-e79d905c.json` |

Both relative-timing development cases returned an unknown predicate marked
answerable; one also assigned strong support to that unknown value. Both full
responses were rejected. Diagnostic response bodies retained privately confirm
this; there was no transport timeout. These failures are not attributed to the
limited urgency interpretation. Additional valid responses and all 16 support
pipeline reasons were inspected against the authored inputs. Returned request
counts matched expected counts in the accepted focused responses; descriptions
and reasons were concise and consistent with the scoped criteria.

All calls ran sequentially, and all model processes finished. The extra-prompt
and property-order experiments were not retained. No attempt was relabeled as
successful, no expectation was removed, and rejected responses were not repaired.

The staged-file inspection and tracked-data audit passed. `.env`, gold exports,
reports, diagnostic bodies, environments and model/data artifacts are untracked
and excluded. Nothing was pushed or published.

## Conclusion

The runtime refactor, evaluation support, example wiring, schemas, documentation
and skills are complete and regression-tested. Current Qwen still fails some
multi-question consistency checks and one isolated classification check. Strict
rejection prevents those invalid responses from being used as decisions, but
cannot prove the correctness of structurally valid outputs. This work does not
qualify financial model accuracy, calibrate support levels, or claim a fully
reliable generation pipeline. Further model-quality work needs separately
reviewed gold and explicit evaluation, not weaker validators or hidden retries.

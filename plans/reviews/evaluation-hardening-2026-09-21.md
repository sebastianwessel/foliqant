# Evaluation hardening

Scope authorized by the user's evaluation review and implementation request.
Baseline: `5727fa8`; 682 offline package tests passed before implementation.

## Requirements

- Align example gold with independently defined native answerability and
  source-preserving extraction contracts. Keep the old live reports unchanged.
- Preserve permitted envelope metadata across the example HTTP boundary without
  introducing authentication or trusting caller-supplied identity.
- Check the requested action and important review/routing behavior. Expand small
  authored English/German examples with ambiguous and changing instructions.
  No expected values may be copied from a model prediction.
- Persist example evaluation reports by default to new private ignored files.
- Add descriptive per-label scores, latency and usage summaries without hiding
  missing values, failed attempts, unsupported labels, or intended reviews.
- Support explicit repeated measurements with source-case and attempt identities,
  bounded concurrency, cancellation, and offline replay of each saved attempt.
- Compare saved baseline/candidate reports offline only when gold, inputs,
  scorer semantics, and targets are comparable. Expose mixed changes instead of
  cancelling regressions against improvements. Do not claim significance.
- Keep language/group breakdowns generic and based on explicit input metadata;
  do not build an expression language, automatic judge, or optimizer.
- Align public documentation, active specification, generated schemas when
  affected, and the repository's runtime skill with tested behavior.

## Verification

Use the existing package suite, strict type checking, lint/format checks, schema
drift checks, documentation and tracked-data audits, and strict MkDocs build.
Add focused negative tests for invalid reports/options and incomplete observations.
Run the real examples sequentially against the existing local endpoint after
offline verification, with no competing model calls. Retain all failures in the
new private artifacts. Synthetic runs remain smoke evidence, not production
accuracy, calibration, or a representative financial holdout.

## Boundaries

No runtime persistence, queues, auth package, model training changes, endpoint
discovery, dependencies for a separate evaluation framework, hidden retries, or
gold changes made merely to improve an observed score. Repeated observations are
not independent gold cases. Prompt changes require fresh inference; rescoring
saved output cannot demonstrate their effect.

## Status

Implementation and verification complete. All three example reports are saved
privately; the remaining measured model mismatch is documented below.

## Evidence and decisions

- Offline tests cover type-sensitive gold, unknown usage, repeated-attempt
  identities, incompatible report rejection, source-span bounds, offline replay,
  metadata grouping, intended review versus failures, and private publication.
- Independent review corrected a comparison ranking defect: `needs_review`
  must not be inherently worse than `completed`. Authored checks decide whether
  either is correct. Unknown artifact modes are rejected.
- Gold corrections preserve deadline operators (`by`/`bis`) and distinguish
  known missing facts from unassessable answerability. Revisions preserve the
  earlier private reports instead of replacing them.
- The first expanded live run passed 91/99 support checks. Five failures came
  from valid verbatim actions containing attached account qualifiers. The scorer
  now accepts independently annotated required/allowed source ranges, not a
  list of observed acceptable predictions. A classification response was invalid.
- Two separate sequential diagnostic responses reproduced oversized public
  explanations (422 and 485 characters against the existing 400 maximum); one
  also incorrectly used partial answerability for a choice. Runtime guidance now
  states the same constraints enforced by the schema and host. It neither
  truncates nor retries responses.
- The next live batch passed support 97/99, MCP 28/28 and HTTP 60/60, with no
  execution failures. Both support mismatches returned `no_matching_option`
  instead of `missing_information`; their review routing was correct. Shared
  runtime instructions now explain all four issue codes and status meanings
  using the existing native decision specification. Gold is unchanged for the
  final measurement.
- Curation recipes, prior artifacts, provider settings and model weights are
  unchanged. Calls use low reasoning, temperature 0.1, 8192 maximum output tokens
  and concurrency one. No agent made additional model calls.

Private evidence lives under ignored `.foliqant/evaluations/`:

- `hardening-20260921T193640Z`: first expanded support run and stopped-run record.
- `native-diagnostic-20260921T194255Z`: final response JSON only, never private
  provider reasoning, for the two validation diagnoses.
- `hardening-20260921T195315Z`: complete support/MCP/HTTP baseline and an offline
  MCP self-comparison (four unchanged attempts).
- `hardening-20260921T195922Z`: final candidate, including source-file hashes.

## Remaining product opportunities (not implemented)

- Representative, independently reviewed held-out email/thread cases in both
  languages, with difficult category boundaries and more positive/negative pairs.
- Explicit caller-owned CI thresholds once the cost of wrong actions and review
  is known; comparison currently reports changes without inventing a policy.
- Calibration or interval estimates only after sufficient independent reviewed
  data exists. Synthetic assertion success is not calibrated confidence.

## Final verification

- Runtime package: 775 tests passed (default offline suite).
- Model tooling: 939 tests passed, 7 explicit native integration tests deselected.
  The current checkout's shared package was selected with `PYTHONPATH=src:model/src`
  for this check; the local model environment otherwise held an older installed
  wheel. No environment or active generator was modified.
- Strict mypy: 97 runtime/example files and 68 model-tooling files passed.
- Ruff and formatting: passed. Runtime schemas: 13 matched; model schemas: 24
  matched. Documentation audit: 42 guides/skill references and 59 CLI examples.
- MkDocs strict build, specification manifest/hash validation, skill validator,
  and staged tracked-data audit passed. Skill architecture audit has only
  nonblocking length/cross-reference suggestions; no structural errors.
- Final support live measurement: pipeline 59/60, classification 21/21,
  extraction 18/18, combined 98/99. No execution failures. The remaining
  pipeline mismatch returned the correct review outcome but the wrong issue
  code (`no_matching_option` instead of `missing_information`). The isolated
  classification of that source passed, showing residual model variability.
  Do not weaken the gold or replace the model output to hide this limitation.
- Same-gold saved support comparison: one improved attempt, zero regressed,
  zero mixed, 23 unchanged. This is descriptive evidence on tiny authored
  examples, not proof of a statistically significant prompt improvement.
- Final MCP measurement: 28/28 checks; no execution failures.
- Final HTTP measurement: 60/60 checks; no execution failures. Across all
  example suites, 186/187 assertions passed in 37 suite executions over 11
  distinct authored scenarios. These are smoke measurements, not 37 independent
  reviewed business examples. All final reports have owner-only mode 0600.
- No model training, provider changes, customer-data uploads, or pushes occurred.

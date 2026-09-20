# Standalone Splash reasoning-effort comparison

Date: 2026-09-20. Status: 60 real requests and independent blinded content
review completed. Development evidence for choosing the next generator pilot
configuration, not financial production qualification or held-out accuracy.

## Recommendation

Use `reasoning_effort: low`, keep `max_tokens: 8192`, and retain the configured
temperature of `0.1` for the next pilot. Reasoning stays enabled. Low is a prompt
instruction to reason briefly, not a zero-reasoning mode or a fixed token cap.

Low had the best acceptance and lowest total runtime in the larger controlled
comparison. Medium did not consistently improve answers. Xhigh occasionally
improved explanation completeness, but consumed substantially more time and
exhausted the completion budget on three cases without returning an answer.
Do not routinely increase the budget or escalate every rejection to xhigh.

Medium is a candidate for targeted repair of rejected complex records: it passed
one citation-sensitive case that low failed. This experiment did not test a
repair conversation or establish an automatic escalation policy. Both low and
medium failed the missing-attachment case, so additional effort alone is not a
general repair strategy. Keep validation, retained rejected responses, and
review in the pipeline.

At the time of this comparison, configuration and generation code were not
changed and the endpoint adapter did not expose a reasoning-effort override.
The subsequent [implementation and bilingual readiness check](splash-full-run-readiness.md)
adds explicit configuration/payload support, binds it to cache identity, and
records the final pilot and resume checks. Neither this comparison nor that
follow-up started full generation or training.

## Method

- Standalone Splash at `192.168.2.101:8000`, model
  `incoai/Qwen3.8-27B-Splash`, Apple M5 Pro, 64 GB, context 262,144.
- Main phase: 16 frozen English development cases, 14 solves and two rewrites,
  all three efforts at temperature zero: 48 requests.
- Follow-up: four counterexamples selected during the main run, reviewed and
  frozen before their inference, all three efforts at the root configuration's
  temperature `0.1`: 12 requests. Reported separately, not retroactively added
  to the original test set.
- Current solver/rewrite prompts and schemas, strict JSON Schema, explicit
  `max_tokens: 8192`, paired requested seeds, one request at a time, no retries.
  Six rotating effort-order permutations approximately balance position; cache
  state was not cleared, so these are workload timings, not cold-start benchmarks.
- Existing validators and reference checks stayed unchanged. A separate agent
  reviewed randomly identified response packets without effort, timing, usage,
  or sequence metadata. Judgments were frozen before revealing the effort map.
  The primary agent separately inspected the material failures.
- Internal reasoning text was not retained; final responses, failures, usage,
  fingerprints, exact requests, schemas, and validator decisions were retained.

The cases cover calculations, multiselect, conflicting priority information,
omitted requests, prompt injection, entity references, execution order, withdrawn
requests, policy applicability, conditional alternatives, missing collections,
mixed answerability, multiple valid choices, and meaning-preserving rewrites.
The earlier ambiguous payment-confirmation probe was excluded before inference.

## Main phase results

| Effort | Strict checks passed | Schema-valid responses | Budget exhaustion | Median request | Total time | Reasoning tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| low | 15/16 | 16/16 | 0 | 12.5 s | 243.0 s | 11,285 |
| medium | 12/16 | 16/16 | 0 | 14.0 s | 303.0 s | 15,166 |
| xhigh | 12/16 | 13/16 | 3 | 35.4 s | 1,094.2 s | 50,190 |

Strict checks combine schema, deterministic validation, reference agreement,
and rewrite guards. They are not a semantic accuracy estimate. In particular,
one medium rejection is a validator false positive.

Independent review separated these outcomes:

| Effort | Substantive semantic faults | Explanation-completeness gaps | Validator false positives | No final answer |
| --- | ---: | ---: | ---: | ---: |
| low | 1 | 1 | 0 | 0 |
| medium | 3 | 1 | 1 | 0 |
| xhigh | 1 | 0 | 0 | 3 |

- Low and medium invented a placeholder request for an unspecified operation in
  a missing attachment. Correct partial status does not make that placeholder
  acceptable. The validator rejected both.
- Medium omitted an explicit `precedes` relation from a “first, then” request.
  Low and xhigh preserved it. This was not an ordering artifact.
- Medium and xhigh returned an unknown amount predicate while incorrectly
  labeling that question answerable. Low handled the per-question status and
  missing-information issue correctly. Validators rejected the inconsistent
  outputs.
- Xhigh exhausted all 8,192 completion tokens on reasoning for conflicting
  priority, unresolved branches, and missing attachment cases. These are budget
  outcomes, not evidence of incorrect final answers. Low and medium completed
  the first two correctly.
- On policy applicability, all efforts correctly returned unknown. Xhigh also
  identified missing firm status, beyond missing date and jurisdiction. Low,
  medium, and the authored reference explanation omitted that distinct condition.
  This is a real explanation-completeness finding outside the signature gate.
- Medium's “65% or above” preserved “at least 65%,” but the lexical guard rejected
  it. It remains a strict rejection and is not counted as a model semantic fault.

All 47 completed solve-result summaries were within the 400-character hard cap;
two exceeded the 160-character target (maximum 194). Longer internal reasoning
was not used as a quality signal.

## Temperature-0.1 counterexamples

| Effort | Solve checks passed | Rewrite guard passed | Semantic faults | Citation-contract faults | Total time |
| --- | ---: | ---: | ---: | ---: | ---: |
| low | 2/3 | 0/1 | 0 | 1 | 57.8 s |
| medium | 3/3 | 0/1 | 0 | 0 | 81.6 s |
| xhigh | 3/3 | 0/1 | 0 | 0 | 169.2 s |

All efforts correctly handled an exact 20% ratio asked to be strictly below 20%,
and a conjunctive policy with one explicitly false required condition and another
unknown condition. The false condition decisively prevents applicability.

All also preserved a resolved predicate and both conditional, mutually exclusive
request branches. Low's second request citation did not itself contain the exact
subject `USD 101`, although the subject was correct and supported elsewhere in
the same source. The unit-level citation rule correctly rejected it; medium and
xhigh met that rule. This supports retaining the citation gate, not treating a
correct branch graph as sufficient.

All three rewrites preserved the strict `>` threshold and exact boundary value,
but the guard rejected equivalent “exceeds” and “(strictly) greater than” wording.
These are three validator false positives, not three model errors. No gate was
relaxed and no rejected output was promoted to training data.

All 12 requests finished normally. All 12 solve-result summaries were within the
hard cap; one was 161 characters, just over the preferred target. These four
selected counterexamples corroborate specific behaviors at the configured
temperature; they are not a replacement for the larger controlled comparison.

## Follow-up findings outside effort selection

Before interpreting bulk acceptance rates, repair generic comparison
normalization for inclusive “or above” and strict “exceeds” synonyms, with
countertests that still reject changed boundaries, polarity, and negation.
The current regex counts omit those forms. Keep original measured gate results
and label any later replay separately.

Review reference-explanation completeness for applicability conditions. Also
audit the existing conditional seed's refund category: the follow-up used a
neutral `charge_refund`/“Refund a charge” category to remove the existing
fee-versus-unspecified-charge ambiguity before inference. Production seed
definitions were not changed by this experiment.

## Evidence and limits

Private evidence:
`~/.local/share/foliqant/checks/splash-effort-comparison-2026-09-20/`.
It includes both manifests, all 60 final response records, rejected outputs,
blind packets/maps, reviewed comparison, runtime snapshots and source hashes.
The independent rubric and frozen judgments are under
`~/.local/share/foliqant/checks/reasoning-effort-independent-review-2026-09-20/`.

- Main manifest SHA-256:
  `eb14aec607b1e3bb074743258cd448b9004b8ec161579a57b31700e1317ad971`.
- Counterexample manifest SHA-256:
  `f8b05c1c479f4ddf97a4f5bf0f709707259dda735870b7ba271b3a6f6a5cc7f5`.
- Consistency checks verified all expected responses and source hashes, positive
  reasoning-token usage on every request, stable model/runtime identity, and
  exactly 48 then 12 submitted/completed server requests. Budget exhaustion is
  included among completed API requests. No transport failures occurred.

This is one sample per effort/case, with previously used development probes and
authored examples, short English inputs, one model/runtime, and an AI-assisted
content review rather than independently human-labeled holdouts. It supports a
practical low-effort pilot choice, not a universal effort ranking, calibrated
accuracy, multilingual readiness, long-document performance, or enterprise
qualification. Keep the two temperature phases separate.

# Recovery pilot: independent content review

Date: 2026-09-21. Independent agent review of the immutable completed outcomes in
`~/.local/share/foliqant/curation-audits/recovery-817f1531671bf4f5`, using its frozen
plan, prepared seeds, retained call identities/final responses, attempt traces,
and accepted outcome records. No inference, downloads, endpoint requests, runtime
changes, reference edits or artifact changes were performed for this review.

## Scope and limits

The fixed snapshot contains **7 completed jobs: 4 accepted and 3 quarantined**,
covering **13 question results, 11 attempts and 18 retained endpoint final
responses** (9 rewrites and 9 solver responses). Five jobs are English and two
German. All seven rejected calls have their final response retained in the
verified call cache, with request/response digests matching the outcome trace.

The interruption report records a 300-second solver timeout at position 8.
That unfinished job has no inferred quality outcome. The plan selects 32 jobs;
this review does not cover the remaining 25, source projections, or full-recipe
quality. These are independent model-assisted judgments, not human-gold review,
accuracy measurements, population estimates or training approval.

## Content findings

| Position / job prefix | Case | Independent assessment |
| --- | --- | --- |
| 1 / `9b43a89f6e90` | EN debt/assets ratio | Correct arithmetic and predicate: 45/100 is below 50%, with the period retained. However, the rewrite adds an unsupported company entity type. |
| 2 / `4ae4ea9ea924` | EN mixed sufficiency | Correct question-relative answers: the requested address change is classifiable, explicit absence of the new address supports false, and an unavailable identity-check result supports unknown with missing information. |
| 3 / `bc15a46378e1` | DE mixed sufficiency | Same distinctions correctly preserved. Source, summaries and missing-fact prose remain German; stable contract IDs remain unchanged. |
| 4 / `835b8a5c6ec2` | EN conditional fee / matching purchase | Correct false predicate, two conditional branches, opposite predicate gates and mutual exclusion. The explanation request correctly has no catalog match. The final subject anchor and citations are preserved. |
| 5 / `da3de8c756e7` | EN conditional duplicate fee | Final quarantine is justified: the second request declares a subject but cites only a pronoun-bearing clause that omits that anchor. The overall meaning is recoverable from the full source, but the unit's own evidence fails the explicit support requirement. |
| 6 / `815c96ddadaf` | EN partial requests | Final quarantine is justified: the model invents a placeholder request unit for unavailable email contents. Only the identified address-change action should be returned; the unknown remainder belongs in missing facts. |
| 7 / `1d1e9339d7ae` | DE partial requests | Final quarantine is justified for the same placeholder error. Additionally, the generic balance term is used as a subject even though it identifies no account, period or other distinguishing target. German language is preserved. |

The unsupported detail at position 1 is a quality limitation beyond numerical
agreement: neither the original source, question nor criteria identify a company.
The inserted entity type does not change the ratio, but violates strict
fact-preservation. No record-specific or company-specific guard, reference
replacement or retrospective acceptance change follows from this finding.

The first rewrites at positions 4 and 5 were rejected by the lexical negation
guard despite preserving the intended branch semantics: a lack construction
replaces explicit negation in one, and an explicit negative alternative replaces
“otherwise” in the other. These are conservative false rejections, not evidence
that those paraphrases changed the decision. Subsequent rewrites pass the guard.

The accepted outputs preserve exact decoded evidence text, including the quoted
fee anchor. The solver's summary paraphrases do not mutate the evidence quotes.
This sample supports correct serialization on these completed calls only; it
does not establish that the quotation guidance prevents every decoding failure.

## Recovery findings

Positions 6 and 7 provide direct evidence of phase-specific recovery. Each has
one validated rewrite followed by two solver calls. The second solver call uses
the identical rewritten task and original solver messages; its appended assistant
message exactly retains the first rejected final response. Feedback identifies
the solver validation issue without supplying the reference answer or asking
for source rewriting.

In both cases the model adds the required issue for its null category, but leaves
the unsupported placeholder unit intact. The final semantic comparison therefore
quarantines the result. Structural correction alone does not establish meaningful
request extraction; the semantic check remains necessary. Position 7 also retains
the generic subject error after this repair.

No call follows the terminal semantic mismatch in either ledger, and no feedback
contains a semantic target. Both mismatches occur at the configured second and
last attempt, so this sample cannot independently demonstrate early stopping
when attempt budget remains. Offline tests, rather than these two traces, must
support that broader stopping claim. Position 5 also exhausts its two attempts
before another solver repair is available; its absence of a repair is not a
wrong-phase retry.

All three quarantined records remain unpublished in the inspected outcomes.
Accepted result semantics are sound for their stated questions, but the entity
embellishment and conservative guards prevent treating automatic acceptance as
proof of exact source equivalence. The incomplete pilot does not justify a full
run quality or production-readiness claim.

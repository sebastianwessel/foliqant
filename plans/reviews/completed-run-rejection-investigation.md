# Completed run: rejection investigation

Date: 2026-09-20. Read-only audit of
`native-financial-decisions-en-de-v1-continue-3beaee64b8c2` in the external
Foliqant workspace. No inference, discovery, repair, training or downloads ran.
The parent and its artifacts remain unchanged. This report supersedes the
immediate-repair recommendation in `completed-run-and-category-catalog.md`.

This is a historical diagnosis of the completed run and the implementation
before the recovery/source fixes. The repair behavior and source mappings
described below are not current operating instructions. See
[curation recovery and source quality](curation-recovery-source-quality.md)
for implemented changes, verification and the unresolved live timeout.

## What repair actually does

`generate-data --repair-from <completed-run> --progress always` validates a
completed parent, creates a separate child, carries accepted outcomes unchanged,
and makes fresh model requests for quarantined jobs. It supplies the previous
response and rejection code as feedback. It does not investigate source-label
correctness or fix pipeline code. Progress controls display only.

For this parent it retains 654 accepted jobs and retries 211 quarantines. Each
quarantine already failed two attempts. The configured limit permits up to two
attempts per job again; rewrite-mode attempts may need both rewriting and solving
calls. The child retains remaining failures and can resume. Repair requires the
same configuration and generation recipe; it is not a route for silently applying
new prompts, validators or reference labels to an old artifact.

## Exhaustive counts

All 865 outcome ledgers and their attempt histories were inspected.

| Source/task origin | Accepted | Quarantined | Total |
|---|---:|---:|---:|
| BANKING77 annotation | 256 | 54 | 310 |
| WANLI annotation | 206 | 139 | 345 |
| Authored state rewriting | 192 | 18 | 210 |
| Total | 654 | 211 | 865 |

English: 559 accepted, 201 quarantined. German: 95 accepted, 10 quarantined.
557 jobs passed their first attempt; 97 passed their second; 211 failed both.
These are automated acceptance counts, not model accuracy or human-gold review.

Semantic inspection covered a purposeful sample of 32 disagreements: all seven
authored cases, 14 WANLI and 11 BANKING77. Provisional assessments were 11 solver
answer errors, six solver contract errors, nine likely reference problems and
six ambiguous references. These proportions must not be extrapolated to the
whole run. Accepted controls were also inspected to distinguish working mappings
from problems hidden by model/reference agreement.

| Final rejection | Jobs |
|---|---:|
| Semantic disagreement with reference | 172 |
| Predicate unknown paired with answerable status | 31 |
| Truncated solver output | 2 |
| Rewrite unit check | 1 |
| Rewrite unchanged | 1 |
| Request subject absent from its evidence | 1 |
| Dependency between mutually exclusive requests | 1 |
| Null category without required issue | 1 |
| Citation not found verbatim | 1 |

The 172 semantic disagreements comprise 111 WANLI, 54 BANKING77 and seven
authored examples. All 165 imported rejected inputs and labels match saved
source records; their model inputs retain the projected task. No projection ID
or answer-copying defect was found. Multiselect ordering is not implicated:
none of these 172 jobs is multiselect, and the signature sorts those selections.

## Findings

### Imported references are not infallible

Inspection found both genuine model mistakes and defensible disagreements with
the supplied labels. Examples include a BANKING77 transfer-duration question
labeled as transfer-not-received, and a WANLI claim repeated in its evidence
labeled neutral. These are audit judgments, not replacement gold annotations.
Do not expose the expected answer to the model or keep retrying until it agrees.
Questionable cases should remain quarantined pending adjudication or exclusion.

WANLI neutral cases are especially problematic: only 28 of 152 were accepted;
124 were rejected. By comparison, 137/148 expected-true and 41/45 expected-false
cases passed. Among the rejected expected-unknown cases, final outputs were
66 true/answerable, 33 false/answerable and 25 unknown/answerable. The accepted
subset therefore strongly underrepresents insufficient-evidence examples.

The WANLI prompt asks whether evidence establishes a claim, while its criteria
require a three-way truth judgment (support, contradiction, neither). Those
criteria are explicit, but the question wording invites a binary support test.
Clarify this distinction and its answerability mapping in the next recipe;
do not collapse unsupported claims into false.

There is a deeper projection limitation: source NLI labels describe a relation
between two texts, whereas the native task asks for a proposition's truth from
evidence and derives answerability from that. The mapping can work for clear
declarative pairs, but is not universally safe. An accepted pair even uses an
interrogative as the claim; semantic agreement does not turn that question into
a truth proposition. Consider preserving NLI as a three-way `choice` task, or
narrow and review the eligible predicate projection. Source neutral labels are
not independently annotated missing-fact or answerability gold.

BANKING77 currently fills option descriptions with raw label names, rather than
actual definitions or boundary rules. Near-neighbor categories such as physical
card acquisition are therefore underspecified. Add reviewed category definitions
and explicit boundaries, or keep this source auxiliary until the mapping is
suitable. The category-authoring helper alone does not enrich frozen source
catalogs. A whole-input exact citation verifies quotation integrity, not the
correctness of the source category annotation.

### Most structural validators are protecting the contract

All 39 non-semantic final failures were inspected. The 31 unknown/answerable
combinations violate the current contract: an unresolved predicate cannot also
claim sufficient evidence for a full answer. The isolated citation, dependency,
subject-evidence and missing-category checks also caught actual violations.
Some affected outputs contain additional semantic errors beyond the first
reported validation code. Disabling those checks would admit defective data.

One clear conservative false positive is the unit guard: it counts the bare word
"year", rejecting a meaning-preserving rewrite from "year-end 2025" to
"end of 2025". Correct this generically without weakening protection for actual
durations, rates and numeric units. Preserve regression cases for both languages.

### Repair feedback is directed to the wrong stage for solver defects

In rewrite mode, `decision_generation.py` selects previous rewrite feedback,
applies it to the rewriter and excludes feedback from the solver. The runner
also requests the rewrite response when repairing these jobs. Consequently,
a solver citation or cross-field error causes another state rewrite rather than
a targeted solver correction. One inspected job regressed from an acceptable
rewrite to unchanged text after solver failure.

Use phase-specific recovery: retain a validated candidate state while repairing
its solver output; repair state rewriting when the rewrite itself fails.
Keep the reference answer hidden. Report structured, non-oracle validation
feedback rather than relying only on the first opaque error code. Semantic
disagreements need separate review, not an instruction to guess another label.

### Truncation is not simply evidence for a larger token limit

The two final truncation failures contain incomplete JSON followed by thousands
of carriage-return characters. The configured output limit was already 8,192
tokens, with low reasoning. Retained failures show a length finish reason but
do not preserve token-usage counters, so reasoning/completion usage cannot be
reconstructed. Investigate degenerate structured generation and preserve useful
usage metadata in a future version; do not automatically increase limits or
disable reasoning. There is no evidence from these failures that concurrency
overloaded the endpoint.

## Recommended next sequence

1. Preserve the completed run and all accepted data as diagnostic artifacts.
   Do not start a blind 211-job repair pass or promote the corpus to training
   readiness on the strength of acceptance alone.
2. Resolve source-task mappings and category-definition gaps; implement generic
   phase-specific recovery, explicit predicate/answerability instructions and
   the calendar-unit correction with offline regression tests.
   Keep ambiguous references quarantined; do not hand-patch individual examples
   to improve the acceptance count.
3. Version changed recipes and define explicit ancestry/revalidation rules before
   reusing old outcomes. Existing `--repair-from` deliberately rejects changed
   recipes; do not bypass that protection or rewrite parent hashes.
4. Run a bounded sequential pilot only after these changes, covering each failure
   family and accepted controls in English/German. Review semantic quality and
   insufficient-evidence coverage, not just acceptance rate. Then decide which
   remaining cases warrant repair, source adjudication or permanent exclusion.
5. Prepare any later source-extension plans against the actual chosen completed
   parent. Existing plans do not automatically transfer to a repaired child.

This investigation changes the recommended order of work; it does not modify
generation behavior, reference annotations or the published dataset.

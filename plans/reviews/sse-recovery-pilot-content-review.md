# SSE recovery pilot: independent content review

Date: 2026-09-21. **Completed review: all 32 planned jobs** from
`~/.local/share/foliqant/curation-audits/recovery-ffdd99e72bcad729`. The verified
completion report records 686.87 seconds and no terminal timeout. This report
consolidates the earlier incremental snapshots. The previous nonstreaming
snapshot remains separately recorded in
[the earlier review](recovery-pilot-content-review.md).

The review read the frozen plan, original prepared seeds, accepted outcome
records and every retained call for these 32 jobs through checksum-validating
`load_object`. Request and response digests match the outcome traces. No network,
inference, runtime edit or change to the running pilot was performed.

## Audited sample

**32 completed jobs: 24 accepted and 8 quarantined, 38 final question results,
35 attempts and 51 cached response entries**: 16 rewrites and 35 solver calls.
Fifty entries contain final responses; one contains a retained partial output
rejected for a whitespace loop. Twenty-five jobs are English and seven are German.
The 16 authored jobs yield 14 accepts and two semantic quarantines. The eight
BANKING77 jobs yield four accepts and four semantic quarantines. The eight WANLI
jobs yield six accepts, one citation quarantine and one semantic quarantine.
All imported source projections in this sample are English; German coverage is
from authored tasks. The completed outcome ledger and report counts agree.
These independent model-assisted judgments are not human-gold review, accuracy
measurements, a transport reliability guarantee or training approval.

| Position / job prefix | Case | Content assessment |
| --- | --- | --- |
| 1 / `ad47f1c5e7a8` | EN debt/assets ratio | Arithmetic, period and answer are correct. The rewrite again adds an unsupported company entity type. |
| 2 / `7a73e6a8b152` | EN mixed sufficiency | Correctly separates the classifiable action, explicit address absence as answerable false, and unavailable identity-check result as not-answerable unknown. |
| 3 / `bf59720f1d5a` | DE mixed sufficiency | Same question-relative answers are correct. German prose and exact evidence are preserved, with a possible absence-scope shift in the rewrite noted below. |
| 4 / `37feb56079aa` | EN conditional matching purchase | Correct predicate and conditional branches. A second solver call repairs the second request's citation without changing the validated rewritten input. |
| 5 / `2a5c21c41dda` | EN conditional duplicate fee | Correct false predicate, preserved conditional statuses, opposite gates, mutual exclusion and catalog no-match for the explanation request. |
| 6 / `f06e5fac4f75` | EN partial requests | Correct partial extraction: one identified address-change request, no placeholder unit, and the missing email contents recorded as a gap. |
| 7 / `c1ac0888e698` | DE partial requests | Justified semantic quarantine: the generic balance term is used as a subject without any distinguishing account or period. The partial status and omission of an unknown placeholder are otherwise correct. |
| 8 / `db6f21357541` | DE ordinal duration | Whitespace-loop output is rejected and retained; the same rewritten task is then solved correctly as low priority for thirty minutes, below one hour. |
| 9 / `88cbf439af1b` | DE conflicting deadlines | Justified semantic quarantine: the answer correctly abstains but labels contradictory deadline evidence as multiple valid options instead of conflicting information. |

## Rewrite and evidence limits

Position 1 inserts “the company's debt” even though the original report,
question and criteria specify no company or other entity type. The ratio remains
correct, but exact numerical preservation and blind agreement do not establish
complete fact-preservation. This repeats the earlier unsupported-detail finding;
no source reference is relabeled and no record-specific rule is recommended.

Position 3 changes an unqualified statement that the new address is missing into
wording that it is not available to the speaker. This may narrow the absence to
the speaker's possession or knowledge, which the original does not explicitly
specify. It does not change the asked-for presence result, but is a wording-scope
concern for strict source equivalence, not evidence that the requester lacks an
address in the real world.

The other inspected rewrites preserve the requested actions, factual absence,
conditional alternatives, ledger facts and quoted target anchors. Final evidence
quotes match the decoded rewritten source. German source, summaries and missing
facts remain German, with stable contract tokens unchanged. The fee anchors
retain their original text and quotation marks in source/citation strings;
summary paraphrasing has not mutated those citations in this sample.

## Successful recovery observed

Position 4 supplies direct evidence of useful phase-specific repair. The first
solver response assigns the second request a distinguishing subject but quotes
only the pronoun-bearing alternative clause, omitting the subject anchor. Its
rejection is justified by the unit-level evidence contract.

The second solver call reuses the exact validated rewrite and the original
solver message prefix. It appends the exact first rejected final response plus
only the subject-in-evidence validation problem and generic correction guidance.
The feedback contains no oracle result, expected branch choice, target quote or
reference label. The repaired response expands the second unit's citation to an
exact source span containing its subject and request, while preserving the
correct decisions. No second rewrite is requested. The retained first response
and successful second response remain independently inspectable.

Position 7 demonstrates early stopping with budget remaining: its first solver
response ends in semantic mismatch, and the completed outcome records one attempt
against a configured maximum of two. There is no correction call or target-bearing
feedback. The generic balance term appears literally in the evidence, but literal
containment alone does not make it a distinguishing subject. Quarantine correctly
preserves this disagreement without automatically removing the subject to obtain
reference agreement.

Position 8 supplies direct live evidence of bounded whitespace-loop recovery.
The retained rejected call is
`e0824986545923570bfc69e478af2d3e4f3d159dff0512fe2a3587ef1271b6bb`.
Its cache contains `rejection.contentDiagnostic=long-json-whitespace-run`, not an
accepted output. The 1,274-character retained partial contains an unfinished
German summary with mismatched quote characters, followed by 1,026 whitespace
characters. Its request and consumed-response digests match the outcome trace.
The partial is not parsed, repaired into an answer, or published.

The second solver request preserves the exact earlier solver messages and
validated rewrite, appends the complete partial unchanged, and supplies only
generic output-validation feedback. It does not supply the reference level.
The subsequent answer selects low priority for thirty minutes, cites the exact
German source, and uses an unquoted German summary. This demonstrates one live
rejection-and-recovery event; it neither proves prevention of malformed model
output nor establishes a general timeout reduction.

Position 9 also ends on semantic disagreement at attempt one with budget left.
The supplied records state incompatible deadlines. Although the solver correctly
returns no ordinal answer, its issue classification treats these as multiple
valid options instead of a conflict in the evidence. The ordinary semantic check
retains the disagreement without target-bearing feedback or forced retry.

## Additional authored cases, positions 10–16

The remaining seven authored cases are semantically appropriate. Position 10
preserves two active requests and their explicitly stated order, without turning
order into an execution prerequisite. English and German choice controls select
the stated request, unknown-predicate controls distinguish unavailable evidence
from false, and both multiselect controls return all and only the supported
categories. Source text and response prose remain in their declared language.
No material unsupported rewrite addition was identified in these seven cases.

## BANKING77, positions 17–24

Original annotations were read independently from the verified frozen source
plan, then compared with the projected editorial definitions and solver outputs.
All eight use annotation-only mode: no source rewrite occurs. No source reference
is relabeled. Whole-request citations preserve the input text, but quotation
integrity does not make the source category correct.

| Position / job prefix | Original annotation and solver outcome | Independent assessment |
| --- | --- | --- |
| 17 / `b64398587a65` | Recipient nonreceipt annotation; solver selects transfer timing; quarantine | The request asks about transfer duration without asserting nonreceipt. The solver's timing interpretation is supported; the source label appears unsupported for this record. |
| 18 / `5c8e096be060` | Order-physical-card annotation; solver selects get-physical-card; quarantine | The request supports acquisition of a physical card. Both catalog entries explicitly acknowledge overlap; neither party's unique choice resolves that overlap. |
| 19 / `8ef7f01a383b` | Unrecognized cash-withdrawal annotation; solver returns not-answerable/multiple-valid-options; quarantine | The text concerns an unrecognized card purchase plus cancellation and refund. Cash withdrawal is unsupported; multiple supported request categories make abstention defensible under single-choice cardinality. |
| 20 / `3b46b71d295c` | Apple/Google Pay annotation; solver selects top-up failure; quarantine | Both the wallet topic and failed top-up are explicit. The catalog supplies no precedence between the overlapping topic and operation, so this disagreement is unresolved rather than a basis for automatic relabeling. |
| 21 / `1d65ba3a6e76` | Unrecognized cash withdrawal; accepted | The source expressly disputes a cash-withdrawal transaction. |
| 22 / `223f9f9f821d` | Wrong withdrawal exchange rate; accepted | The source expressly disputes the exchange rate for withdrawn cash. |
| 23 / `89849b7c08b0` | Transfer timing; accepted | A transfer-duration question supports timing, with no reported nonreceipt. |
| 24 / `5f7a593ecc3e` | Wrong cash amount received; accepted | Requested and received cash amounts differ, supporting the category. The solver summary additionally mentions an ATM, although no withdrawal venue is stated. |

The ATM wording at position 24 follows a term in the editorial description, but
is not independently established by the request. The accepted canonical target
summary does not carry that venue assertion. Category matching must not be
interpreted as proof of every contextual detail in a category description.
Likewise, physical-card overlap and wallet/top-up overlap must not be resolved by
inventing a per-record distinction or adding a source-label hint to the solver.

All four BANKING77 disagreements stop after one attempt with budget remaining;
none receives target-bearing correction feedback. These examples support
quarantine as a useful boundary, not infallibility of either source labels or
model judgments. The original source annotations remain available for later
adjudication; this review does not replace them.

## WANLI, positions 25–32

The source annotations were checked against the frozen original source rows.
The projection remains a three-way choice over text relations; a neutral choice
is answerable and is not fabricated missing-fact or predicate-unknown supervision.
None of these source texts was rewritten. Every accepted annotation-only outcome
is exactly its prepared parent record; solver prose does not replace the target.

| Position / job prefix | Relation and outcome | Independent assessment |
| --- | --- | --- |
| 25 / `8a9653dbd0dd` | Neutral; accepted | A specific person's judgment of another does not establish a general claim about intelligent people's self-perception. |
| 26 / `f19a103228ce` | Neutral; accepted | Advice about visiting before blossoms bloom neither establishes nor denies their beauty. |
| 27 / `c03a6af2ddcd` | Neutral; accepted | Statements about different groups do not establish or contradict one another without an additional premise. Exact quoted text is preserved. |
| 28 / `5cd7a7712e51` | Neutral; accepted | A premise framing whether governments can succeed as an open question does not assert the affirmative hypothesis. No truth value is invented for the unresolved subject matter. |
| 29 / `a185938cf919` | Entailment; accepted | Under ordinary linguistic usage, calling a poll the most important supports the weaker statement that it is important. |
| 30 / `8ccbcf017f59` | Contradiction; accepted | Plausible when both phrases describe the physical manner of marching, but not unambiguous: marching in step can coexist with mental confusion. Agreement does not settle that reading. |
| 31 / `105ab1abe5f1` | Reference entailment, solver neutral; citation quarantine | Both solver attempts corrupt the premise's quotation marks, so neither satisfies exact evidence requirements. The reported-statement versus asserted-truth distinction also makes neutral defensible; it is not adjudicated here. |
| 32 / `17e231026464` | Reference entailment, solver neutral; semantic quarantine | Inability to control a situation does not necessarily establish lack of knowledge about how. The disagreement is defensible and remains quarantined without source relabeling. |

At position 31, decoded citation characters were inspected explicitly. The source
uses U+201C/U+201D quotation marks; attempt one substitutes U+0022, and attempt two
substitutes actual U+0002 control characters. These are evidence mutations, not
harmless differences in JSON escaping. The second call retains the exact original
task and first rejected final response, with only generic exact-quote correction
guidance. It does not receive the expected relation or a replacement citation.
The citation validator excludes both responses; canonicalization does not rescue
or publish the invalid source reference.

Position 32 stops after one attempt with budget remaining. Together with the
BANKING77 disagreements, it demonstrates preservation of disagreement rather
than retrying until the model reproduces a source label. The apparently ambiguous
accepted relation at position 30 is a further reason that blind agreement cannot
be described as human-gold adjudication or proof of source-label correctness.

## Completed-review conclusion

This bounded pilot demonstrates completion, retained whitespace-loop rejection
and recovery, useful exact-evidence repair, and early quarantine of semantic
disagreement. It also exposes unresolved limits: unsupported rewrite details,
potential scope shifts, overlapping source categories, ambiguous NLI readings,
and a failed quote-preservation repair. The successful transport and contract
checks do not remove those content limits.

Accepted records retain canonical references rather than arbitrary solver prose.
That excludes the ATM assertion from the published target at position 24, but
also means blind agreement cannot improve a questionable canonical source label
or justify an unsupported fact in rewritten state. No old artifacts, source
annotations or accepted records were edited by this review. No per-record
matching rule or automatic source-label correction is recommended.

The purposeful sample is not an accuracy benchmark or a representative measure
of the full recipe. It establishes neither full-run quality, human-gold review,
training readiness nor production fitness.

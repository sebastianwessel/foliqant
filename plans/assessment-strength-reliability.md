# Assessment strength and reliable runtime decisions

Status: implemented, measured and verified on 2026-09-22.
Baseline: 5a214eb. Authority: owner clarification that strength supports the whole
reported assessment, including justified abstention, and explicit request to
implement, verify, extend evaluations, research reliability and improve prompts.
This supersedes only the answer/strength coupling in the preceding runtime V3
review. Existing artifacts and historical reports remain unchanged.

## Required outcomes

1. Keep runtime V3 reason/strength shape and native V2 model artifacts separate.
   Strength is support for answerability, issues and any answer. Strong abstention
   is valid; null means unassessed, independent of whether an answer is present.
   No extra enum, confidence percentage, hidden retry, or routing threshold.
2. Preserve answer/status invariants. Expose predicate consistency to structured
   generation through supported JSON Schema branches, with independent Python
   validation. Test actual provider schema conversion and local backend behavior.
3. Replace circular weak-urgency gold with varied independently authored examples.
   Include strong abstentions, indirect support, conflicts, multiple intents,
   limited permissible interpretations, partial/mixed collections and EN/DE.
   Keep translated families together and unused validation families separate.
4. Use evidence-based research and explicit prompt variants. Run local Qwen
   sequentially, record variant identity, settings, full final response and exact
   failure diagnostics privately, without new persistent runtime infrastructure.
   Count failed results and usable coverage separately from raw rating diagnostics.
5. Compare isolated and grouped decisions without confusing repeated attempts
   with independent gold. Evaluate the selected candidate on unused families;
   audit answer/status/strength/reasons, not merely assertion totals.
6. Align runtime fixtures, examples, docs, specs and skills; generate schemas and
   run runtime/model regressions, type/lint, doc/skill/spec and tracked-data checks.
   Commit authorized source work; do not push or alter training artifacts.

## Evidence boundary

The current task concerns runtime use of stock local Qwen, not dataset generation,
training or production qualification. Provider constraints ensure only represented
structural rules, not semantic truth. Research findings are hypotheses for this
model/backend until tested. Do not tune expected values to observed predictions.
Null strength has no automatic business-case proxy; exercise it at contract
boundaries rather than inventing an opt-out workflow or labeling all abstentions null.

An opt-in experiment recorder may retain final model JSON outside Git. Ordinary
runtime errors remain sanitized, and no internal provider reasoning is exported.

## Implementation and selection

Whole-assessment semantics, independent answer/status validation, provider-facing
predicate schema branches, generated runtime schema and active guidance are
aligned. Strong abstention is accepted without permitting `unknown/answerable`.
Python and strict-provider schema tests exercise the same native answerability
rules. No model-training contract or immutable artifact was changed.

Keep the simpler corrected whole-assessment prompt. Extra examples, additional
unknown/negative and tentative/decisive prose, reversed predicate branches, and
reason-first schema property ordering did not establish a repeatable quality
improvement. The boundary-prose challenger improved one development run but
reintroduced missing-information errors in its repeat. Its limited ratings also
changed. No challenger prose or property-order change is shipped.

The selection was recorded before inspecting reserved validation predictions.
One challenger validation run had already been dispatched; it remains recorded
as unselected. The selected baseline received its own final validation run. No
further tuning used that validation set.

## Evaluation design and results

Revision 4 contains 15 grouped development messages plus two isolated predicate
controls on existing inputs: 17 attempts, 15 unique inputs, 10 families. Four
validation messages form two additional EN/DE families. Translations and related
variants stay together. Gold is authored synthetic, independently reviewed by
agents, not human-adjudicated production ground truth.

A construct review corrected four revision-3 limited labels: uncertainty about
which remedy to request did not weaken the clearly stated communication purpose.
Revision 4 adds a genuinely uncertain document-kind family, with English, German
and mixed-context variants. The correction follows the question's criteria;
original gold snapshots and reports are preserved. Validation has only strong
gold, so it cannot measure limited recall. Null is tested at contract boundaries,
not through a fabricated business case.

All local model calls were sequential with low reasoning, temperature 0.1 and
8,192 maximum output tokens. Reports identify the configured model, not a verified
weight digest or engine build. No cloud inference, training or data generation ran.

| Measurement | Whole cases | Checks | Valid results | Strong gold matched | Limited gold matched |
| --- | --- | --- | --- | --- | --- |
| Selected baseline, development r4 | 11/17 | 432/449 | 17/17 | 71/71 | 0/6 |
| Boundary-prose challenger, development r4 | 13/17 | 440/449 | 17/17 | 71/71 | 0/6 |
| Challenger, targeted repeat | 2/4 | 77/84 | 4/4 | 14/14 | 2/2 |
| Selected baseline, reserved validation | 2/4 | 113/118 | 4/4 | 20/20 | No support |
| Unselected challenger, reserved validation | 1/4 | 111/118 | 4/4 | 20/20 | No support |

The baseline development measurement replays 14 unchanged-input observations
plus three new observations with verified configuration/prompt/schema provenance.
This is 17 original inferences, not 17 new replay calls. Their original inference
time totals 360.97 seconds; replay timing is not inference latency. The challenger
full run totals 375.19 seconds. These small sequential samples are not throughput
or population-accuracy estimates. The targeted repeat is not a full-suite repeat.

In the initial two-case predicate probe, the baseline passed 8/12 checks and the
example-augmented prompt passed 12/12. That apparent improvement did not transfer
reliably to grouped tasks. Changing branch or property order did not resolve the
semantic errors. All retained final comparisons returned structurally valid
results: schema validity alone does not establish truth.

## Error and reason analysis

- Missing information sometimes became a substantive true predicate or a known
  empty label set. Two baseline reasons explicitly said neither true nor false
  was established while their values still said true/answerable. That is a
  semantic contradiction, not an invalid strong-abstention combination.
- All six tentative-purpose ratings became strong in the selected development
  measurement. Four reasons promoted an uncertain recollection to definite
  document content; two mixed-context reasons omitted the uncertainty. A later
  challenger repeat got two of these ratings right, demonstrating instability.
- Another baseline case described two active intents correctly but returned
  `no_supported_answer` instead of `multiple_valid_options`.
- Reserved validation retained two German subject-format mismatches (`Konto S-71`
  versus the requested identifier alone, likewise S-72) and three German issue
  mismatches (`no_supported_answer` instead of `conflicting_information`). Both
  subject forms occur in the source; the issue is the authored identifier
  criterion, not hallucinated text. Review routing was retained for the conflict.
- Manual review covered 77 baseline public reasons. One priority reason implied
  no action remained despite an active statement request; the routine priority
  itself was supported. Five reasons exceeded the 160-character soft target;
  none exceeded the 400-character limit. Matching strength labels did not prove
  that reasons or answers were correct.

The strength field is therefore aligned to its intended meaning but is not yet a
reliable automatic decision gate for this stock model. Keep deterministic
answer/status and route checks. Do not weaken validators, manufacture confidence
percentages, silently repair values from reasons, or claim production readiness.

## Broader example verification

| Example | Mode | Cases | Checks passed |
| --- | --- | --- | --- |
| Support pipeline and isolated classify/extract | Local model | 38 | 260/260 |
| Thin HTTP host using the support workflow | Local model, in-process ASGI | 16 | 156/156 |
| Extract request then call MCP, plus isolated steps | Local model and local MCP | 6 | 38/38 |
| Public-request MCP pipeline and isolated lookup | Local stdio, no model | 4 | 28/28 |

These suites repeat related examples across pipeline/step/transport boundaries;
60 live-example cases are not 60 independent business inputs. Support strength
gold is all strong and cannot establish limited recall. Full results and public
reasons are retained privately; ordinary runtime logs remain sanitized.

## Verification and artifact references

Final selected code: 1,015 runtime tests and 952 model tests passed; seven native
integration tests were excluded. Strict mypy passed for 108 runtime/example and
71 model files. Ruff checks/formatting, 14 runtime and 24 model schema checks,
44-guide/skill-reference documentation checks with 61 CLI examples, strict MkDocs,
both skill validators and specification checks passed. Independent final review found
no blockers. Staged-file inspection, tracked-data audit and whitespace checks passed;
private reports, secrets, generated corpora and weights are excluded. No native training,
export-inference or production qualification is claimed.

Private observations live under `.foliqant/experiments/assessment-strength/`:

- `selection.json` records the decision before validation inspection.
- `replays/20260922T083028.962528Z-f2e9ff36/` is the final baseline r4 composite.
- `runs/20260922T082905.205977Z-41b8790c/` is the full challenger comparison.
- `runs/20260922T083559.529323Z-d968a78d/` is the challenger repeat.
- `runs/20260922T084121.580276Z-5c717514/` is selected-baseline validation.
- `runs/20260922T083840.667079Z-da90ee2a/` is unselected-challenger validation.

Broader example reports in `.foliqant/evaluations/`:
`example-report-20260922T084919Z-98931d8b.json` (support),
`example-report-20260922T085242Z-72e5c471.json` (HTTP),
`example-report-20260922T085315Z-70ba4f4a.json` (extraction/MCP), and
`example-report-20260922T085223Z-bfc01212.json` (public MCP).
These private paths are evidence references, not committed datasets or logs.

Research findings and transfer limits are in the
[research memo](../research/assessment-strength-reliability.md). The useful next
quality work is independently reviewed, representative EN/DE boundary data and
family-separated measurement of missing facts, conflicting instructions and
uncertain category signals. That is a recommendation, not authorization for new
training or model runs. Existing holdout failures are now known; do not reuse
that split as unseen evidence after tuning against them.

# Deferred rubric-based evaluation workstream

Date: 2026-09-20. Status: deferred research plan; not an implementation
decision, model qualification, benchmark result, or authorization for inference,
training, data download, paid calls, or a new plugin.

## Purpose and boundary

Explore a future evaluator workstream inspired by the useful separation in
[openJev](https://github.com/Heman10x-NGU/openJev-verdict-2.0/tree/a458733c5f43fc7f30b6e4381636cbfbf8437633): a system can assess a proposed
answer without being the system that produced it. No evaluator architecture is
selected. Its synthetic-label assumptions and model agreement must not be
treated as correctness. [Custom inference options](custom-inference-options.md)
separately compares ordinary causal-model paths, supported classifiers, and
custom heads if relaxing standard inference is worthwhile.

This is separate from the current deterministic generation validator and
classifier: JSON/schema, configured fields, literal-evidence presence, exact or
structural reference comparison, and generated-token score thresholding remain
their existing narrow functions. They are useful baselines and integrity checks.
They do **not** establish semantic supportedness, completeness, policy
adherence, or answerability. This workstream proposes no runtime decision API,
no default threshold, no automatic action, and no replacement for those checks.

The first objective is an offline, generic, rubric-question evaluator that can
compare candidate answer(s) against an explicit question contract and permitted
evidence. A later model may answer the rubric questions by constrained label
scoring or generation, but the rubric, human labels, splits, and audit protocol
come first.

## Proposed evaluator contract (research only)

Each evaluation item is versioned and contains: a typed task question and
cardinality, allowed evidence/source identifiers and time scope, relevant policy
snapshot, one candidate answer (or blinded A/B candidates), and the rubric
version. The evaluator returns a short result per rubric question:

| Function | Required outcome | Short reason and trace |
| --- | --- | --- |
| `supportedness` | supported / unsupported / insufficient-evidence | One short reason plus the evidence IDs and spans relied on, or the missing/conflicting evidence. |
| `completeness` | complete / incomplete / insufficient-evidence | Whether every independently defined active request, condition, and required answer part is handled; list omitted unit IDs only when known from the evaluation record. |
| `policy_adherence` | adherent / violation / not-applicable / insufficient-evidence | The policy-rule ID and the relevant candidate behavior; content cannot replace policy. |
| `trace_outcome` | traceable / untraceable / inconsistent | Whether the stated outcome, cited evidence, and declared deterministic branch agree; this does not claim a faithful account of model internals. |
| `pairwise_rank` | A / B / tie / insufficient-evidence | A rubric-specific comparison; swap-invariant rationale, evidence, and unresolved criterion. |

Each rubric verdict remains separate. Where an item also concerns input
answerability, reuse its existing status (`answerable`, `partially_answerable`,
`not_answerable`, or `undetermined`) and its generic issue categories where
meaningful. Do not create an evaluator confidence or uncertainty contract. A
short reason is evidence for reviewing the verdict, not proof of the verdict and
never hidden chain of thought. Evidence references must be mechanically
resolvable to the allowed source; unsupported evidence references fail the
evaluator output contract.

Typed questions prevent a single vague "quality" score from combining distinct
failures. Examples: *Is the answer supported by the dated prospectus provided?*
and *Does it answer both active requests under policy P-12?* Pairwise ranking is
for cases where an absolute label is not well-defined; it must retain tie and
insufficient-evidence rather than forcing a winner.

## Independent truth and partitions

Two qualified annotators label each item independently before seeing evaluator
results. They label the allowed evidence, answer requirements, verdict per
rubric function, and, where applicable, the existing answerability status and
issue categories; an adjudicator resolves only defined disagreements and records
agreement, unresolved items, rubric version, and time spent. Model/teacher
labels can seed candidate cases but are never gold labels or a substitute for
this process.

Partition before any evaluator prompt, model, calibrator, or threshold is
chosen. Group source thread, customer/template family, underlying document and
all derived/paraphrased/counterfactual variants together. Also reserve temporal
and domain slices: for example, later document revisions and a held-out family
of funds, disclosures, regulations, contracts, or reports. Development may tune
questions, labels, prompts, models, calibration, and acceptance policies.
The final human-labeled holdout remains inaccessible to all of those activities,
including teacher generation, retrieval indexing, few-shot examples, and error
analysis. Any subsequent learning uses development-only labels with documented
cross-fitting; it must never train on final holdouts.

## Judge controls and qualification

An evaluator is an untrusted measurement component until it agrees sufficiently
with the independent labels on frozen data. Qualify each evaluator/profile with
these controls:

- Blind candidate origin and model identity. Do not judge a model with its own
  output unless that self-preference comparison is explicitly measured.
- Counterbalance A/B order and rerun every pair swapped. Report position
  consistency, abstentions, and ties; reject order-sensitive aggregate claims.
- Add length-matched concise-versus-padded variants and wording/paraphrase
  variants. Report whether verbosity or presentation changes a rubric verdict.
- Treat candidate text, quoted documents, evidence, and reasons as untrusted
  data. Test direct and indirect prompt-injection strings, conflicting evidence,
  missing sources, long contexts, and German/code-switched examples. The
  evaluator's criteria and output schema stay in trusted instruction fields.
- Require exact evidence resolution, structured labels, bounded short reasons,
  deterministic schema validation, and repeated-run stability checks. A fluent
  rationale does not make a judgment correct or causally faithful.

Position and verbosity tests follow known LLM-judge failure modes documented by
[MT-Bench/Chatbot Arena](https://arxiv.org/abs/2306.05685) and the focused
[position-bias study](https://arxiv.org/abs/2406.07791). Injection testing is a
required adversarial slice because judges can be manipulated by candidate text;
see [the LLM-judge injection study](https://arxiv.org/abs/2505.13348). These
are motivations for measurement, not evidence that any proposed evaluator is
robust.

## Measures and decision discipline

Report each rubric function separately: human agreement and adjudication rate;
macro/micro agreement with the frozen gold labels; false supportedness,
completeness, policy, and trace acceptance; pairwise accuracy/tie handling;
evidence-reference validity; invalid-output and abstention rate; robustness
changes for each adversarial control; and language/domain/time/source-family
slices with counts.

For any selective automation simulation, plot accepted error against coverage
across frozen thresholds. Include the acceptance count and two-sided 95%
confidence intervals. Use source-family block bootstrap intervals for aggregate
rates (and paired, family-clustered resampling for A/B comparisons); state when
small samples make an interval uninformative. Threshold selection is development
work only. Freeze a policy before the final audit and report accepted error and
coverage there, rather than treating an aggregate score as a per-case guarantee.

Measure wall latency per evaluator call and per complete evaluation decision,
input/output tokens when exposed, number of passes/retries, hardware/runtime,
and attributable local or provider cost. Report explanation generation as an
incremental cost over label-only evaluation. If a cost is unavailable, record it
as unavailable rather than estimating from characters. Compare like-for-like
label-only paths and keep human annotation/adjudication cost visible.

## First bounded experiment (not yet authorized or run)

Prepare a preregistered development-only pilot of 48 independently double-labeled
case packages, grouped into at least 24 source/template families. Cover clear and
insufficient-evidence cases, omitted request units, unsupported citations,
policy violations, stale/conflicting documents, A/B/tie comparisons, and prompt
injections; include a small German/code-switched diagnostic slice. The selection
uses only development data and is frozen before candidate generation or evaluator
selection. Keep a separate, untouched final holdout out of the pilot.

For each case, evaluate the five functions above against fixed candidate answers
that include correct, plausible-incomplete, unsupported, and policy-violating
variants. Compare (1) the existing deterministic checks as a baseline where they
apply, and (2) one generic rubric-evaluator configuration. Run A/B pairs in both
orders and include concise/padded and injection variants. Record all private
artifacts outside Git. The pilot answers only whether the rubric is labelable,
the evaluator is stable enough to warrant a larger comparison, and what it costs;
it does not choose a production evaluator, train a judge, fit calibration, or
authorize automation.

Advance only if annotation disagreements expose no unresolved core contract,
evidence references can be checked, swap/verbosity/injection controls do not
produce unexplained material verdict changes, and the result improves on the
deterministic baseline for the semantic functions it cannot itself measure. A
larger frozen development comparison and independently labeled final audit would
then need their own plan and authorization.

## Open questions

- Which answer and policy contracts are sufficiently stable to support human
  rubric labels, especially for legal applicability and financial calculations?
- What constitutes a fair semantic match for request units and evidence spans,
  including multiple same-category requests and conditional branches?
- Can one generic evaluator profile maintain agreement across correspondence,
  funds, disclosures, regulation, contracts, and reports, or must scope remain
  domain-specific?
- Which ordinary causal-model, supported-classifier, or custom-head option offers
  adequate agreement, injection resistance, latency, and cost? Architecture
  comparison belongs to `custom-inference-options.md`, not this rubric track.
- What accepted-error and coverage target, minimum slice counts, and human-review
  process are appropriate before any automated use is considered?

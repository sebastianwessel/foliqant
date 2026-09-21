# Input answerability and evidence-backed decisions

Date: 2026-09-19. Status: research recommendation, not an implemented API, trained capability, or production qualification. This extends the [model concept](model-research.md). Supporting reviews cover [answerability research](confidence-answerability-research.md) and [statistical risk methods](confidence-risk-methods.md).

2026-09-20 target refinement: [specification 10](../10-business-decisions-and-processes.md)
records per-label/value evidence, host-resolved source spans, explicit ordered
thread snapshots, bounded extraction and one process with multiple child tasks.
These additions remain unimplemented. Historical native V1 artifacts, source
snapshots and recipe identities remain immutable. Current native curation uses
the V2 contract in specification 09; this research does not redefine that API.

## 1. Product decision

Foliqant should answer **typed questions about supplied state**, with caller-defined options, predicates, and score rubrics. Its distinctive reliability feature should assess whether that state supports answering the question completely. It must not equate a concentrated model distribution with sufficient information.

Keep these four objects separate:

| Object | Target | Example |
|---|---|---|
| Input answerability | Could a competent evaluator determine a substantive answer from the allowed evidence under this question's criteria and cardinality? | The email clearly requests a refund, but does not identify which transaction. |
| Returned-answer adequacy | Is this particular answer correct, complete, supported, and contract-valid? | A correct refund label still omits a second request for a statement. |
| Explanation | Which evidence and criteria support the answer; what is unresolved? | Cite both request spans and explain which required identifier is missing. |
| Action eligibility | Do trusted prerequisites and authorization permit execution? | A clear refund request does not authorize a refund. |

Answerability belongs to `state + question + criteria + allowed sources + time boundary`, not to the email alone. More reasoning cannot recover an absent document or determine a customer's unstated intent. Conversely, sufficient input does not guarantee a model answers correctly. This distinction is supported by [Sufficient Context, ICLR 2025](https://arxiv.org/abs/2411.06037); applying it to financial correspondence is our proposed extension.

## 2. Preserve the typed decision features

| Question type | Required semantics | Reliability boundary |
|---|---|---|
| Choice | Caller supplies meanings and stable IDs; declares one choice or multiple selections. | Alternative probabilities for one uncertain answer are not multiple simultaneous facts. |
| Predicate | Evaluate a caller-defined condition; distinguish supported true, supported false, and insufficient evidence. | Lack of evidence must not silently become false; unknown is not a fabricated 0.5 probability. |
| Ordinal score | Caller supplies ordered levels and their rubric; return the supported level and evidence. | Missing rubric facts are not automatically the middle level. A mean assumes defensible numeric spacing. |
| Several questions | Assess each question independently against the same state; preserve IDs and unresolved answers. | A batch-level success check requires every required question to be handled; errors may be correlated. |
| All active requests | Identify distinct request units before mapping them to a catalog. | Two requests can have the same category; a category set alone loses this distinction. |

Choice, predicate, and score distributions remain desired features. Normalized categorical probabilities apply to mutually exclusive alternatives. Multiple selections instead need per-option applicability probabilities (which need not sum to one) or a specified joint distribution over subsets. Values derived from model scores remain model distributions until their interpretation is independently validated. A distribution conditioned on known options does not account for an omitted category, missing evidence, or an ill-posed question. Numeric score uncertainty is also separate from input sufficiency.

Do not require a special inference server or custom model head. Train an ordinary causal model on the final state/question/result format. Request-unit analysis can be one bounded structured inference task; a verifier is an optional additional ordinary request whose latency and benefit must be measured. This is not the single-pass encoder architecture of every reference product, and equivalent speed is not promised.

## 3. Multiplicity is not ambiguity

State: “Please cancel my standing order and send my annual statement.”

- “What are all active requests?” is answerable with two request units.
- Returning only `cancel_standing_order` is incomplete even if that label is certain.
- “Choose exactly one category representing all requests” has a cardinality mismatch. A 50/50 distribution does not fix it.
- “Which request has priority under rule R?” may be answerable if R explicitly resolves this combination. The presence of two requests alone is not a reason to abstain.

Contrast “Please cancel it” with two plausible referents: this is one ambiguous request, not two confirmed cancellations. A set of plausible interpretations must never trigger both actions.

The distinction also applies to same-category requests: “Send January's statement and March's statement” contains two requested items even if both map to `statement_request`. Preserve request identity, entity, and supporting spans. When decomposition is itself ambiguous, retain that ambiguity instead of manufacturing a definitive count.

At runtime, no method knows the number of requests it failed to detect. Report predicted completeness and evidence, not an observed coverage fraction calculated from the model's own list. Offline request recall uses independently annotated request units and a declared matching rubric.

## 4. A useful value with an honest meaning

Recommend the product term **answerability**, with a readable status and reasons first. If a validated numerical value is available, define it as an estimate of:

`P(A = 1 | state, question, rubric v)`, where `A` is the frozen, adjudicated binary sufficiency label.

This is an estimate of an input property, not the probability that the model's chosen category is correct. Raw reviewer disagreement is retained separately; the score does not estimate the proportion of reviewers voting yes. It is still produced by an imperfect estimator; “input-focused” does not make it objective or immune to model errors. Validate its calibration against blinded, adjudicated labels on representative held-out inputs. Expose `null` with an explicit unavailable status before qualification, rather than a model-written percentage.

Keep an optional, separately validated **answer adequacy** estimate for:

`P(the returned answer is correct AND complete AND supported AND contract-valid)`

The second estimate evaluates the actual returned answer. Do not obtain it by averaging or multiplying component scores. Train and validate that joint target directly. Unknown can be the correct answer: an honestly supported “not enough information” may have high answer adequacy while input answerability for a substantive choice is low. Neither number is permission to execute an action.

Illustrative assessment for the two-request email, if a caller requires exactly one category covering everything:

```json
{
  "questionId": "route_all_requests",
  "answer": null,
  "answerability": {
    "status": "not_answerable",
    "issues": [
      {
        "code": "multiple_valid_options",
        "explanation": "Two distinct requests cannot be fully represented by one category.",
        "evidence": [
          { "sourceId": "email-1", "quote": "cancel my standing order" },
          { "sourceId": "email-1", "quote": "send my annual statement" }
        ]
      }
    ],
    "probabilitySufficient": null,
    "calibrationStatus": "unavailable"
  },
  "suggestedResolution": "allow_multiple_request_units"
}
```

This is a proposed result shape, not a current contract or a measured prediction. Statuses are predictions too. The application attaches calibration metadata and chooses a permitted resolution. It must not let untrusted input change the question contract.

Use a compact top-level status (`answerable`, `partially_answerable`, `not_answerable`, `undetermined`) plus only three generic issue codes: `no_supported_answer`, `conflicting_information`, `multiple_valid_options`. This follows the user's refinement to avoid domain-specific enum growth. Missing attachments, ambiguous referents and cardinality details belong in explanations. Multiple causes can coexist. Several clear requests are not an issue when a collection answer is allowed. A failed model call or malformed contract is a technical error, not evidence of unanswerability. Do not overload the binary probability target with partial answerability: retain per-question or per-unit results and define all-required-units sufficiency separately.

Issue codes do not mechanically imply unanswerability. When the answer space explicitly permits `no_match`, sufficient evidence for no catalog match can yield `answerable`; a forced catalog choice cannot. For predicates, preserve the distinction between evidence supporting true/false and a correct report that neither is established. A question specifically asking whether evidence is missing can itself be answerable. The rubric must define its substantive target, including no-match and unknown semantics, before labelling or calibration.

### Ownership without duplicated decisions

The solver proposes answers, request units, citations and issue candidates. The input assessor owns the published predicted answerability status and its separately calibrated value; it receives only the state and question contract. The answer assessor owns the adequacy assessment and can inspect the proposed answer. Record the estimator/profile for each assessment; these are logical roles, not a requirement for three separately deployed models. A shared checkpoint can support the roles through ordinary inference requests, and unused assessment features need not run.

If solver issue candidates disagree with the input assessment, do not silently average or overwrite them. Apply a declared bounded check or defer for review; count these cases in evaluation. Deterministic missing-source or contract failures take precedence over an optimistic prediction. Freeze and audit the complete policy, including this disagreement path and its cost.

## 5. Explanation and reasoning

Every answer should offer a concise explanation of the applied criterion, supporting evidence, contrary evidence, and missing facts that could change it. Validate source IDs and offsets mechanically; evaluate relevance and entailment separately. For funds, reports, and regulations, retain document revision, effective date, jurisdiction and units where the question requires them. “The supplied document states X” and “X is legally applicable here” are different questions.

Optional bounded reasoning is a solver mode, not a substitute for explanation or evidence. Compare direct answering and deliberate answering on the same held-out cases, including unsupported inputs. A longer rationale can still be wrong. Do not claim generated reasoning is a faithful causal account of the model's internal computation; [research on reasoning faithfulness](https://www.anthropic.com/research/reasoning-models-dont-say-think) gives concrete reasons to avoid that promise.

The workflow's actual branch explanation is deterministic: which validated field and rule caused clarification, review, or routing. Keep it separate from the model's proposed explanation.

## 6. Match the remedy to the problem

| Detected condition | Proposed response |
|---|---|
| Several clear requests | Preserve all request units and handle each through the configured workflow; do not ask the customer to select one unnecessarily. |
| Missing accessible document | Retrieve the authorized source, then reassess using the newly versioned state. |
| Missing customer-only fact | Ask the smallest useful clarification. |
| Ambiguous reference or intent | Present the unresolved alternatives and ask what distinguishes them. |
| Conflicting evidence | Apply an explicit source/temporal precedence rule if available; otherwise request resolution or review. |
| Sufficient input but unreliable answer | Use a bounded solver/checker escalation or human review. |
| Outside supplied catalog | Use no-match or a configured broader search; never force the nearest class. |
| Partial resolution | Retain resolved answers; process them separately only if workflow policy declares the units independent. |
| Missing authorization | Stop the action regardless of model scores. |

Cause diagnosis must itself be evaluated. [ConfuseBench, ACL 2025](https://aclanthology.org/2025.acl-long.840/) finds models can misidentify why they are uncertain. Treat proposed causes as testable predictions, not trusted introspection. More retrieved text also need not resolve the specific missing fact.

Decomposition must preserve conditions, order and dependencies. “If this is a duplicate charge, refund it; otherwise explain it” contains mutually exclusive branches, not two independent actions to perform now. Represent the relationship and unresolved condition before considering any execution; do not mistake an alternative or conditional request for an unconditional one.

## 7. Methods to adopt, compare, or defer

| Method | Decision and reason |
|---|---|
| Task-relative sufficiency labels; request-unit and evidence annotations | Adopt as the evaluation foundation. Directly measures the requested behavior rather than proxy fluency. |
| Trusted extraction checks and declared prerequisites | Adopt. Missing attachments, truncation and invalid references are observable; semantic completeness is not. |
| Supervised sufficiency and whole-answer assessors | First scoring baseline. Train on independently labelled cases and out-of-fold solver outputs, then separately calibrate. Test whether an additional inference pass earns its cost. |
| Token margins, option entropy and mean completion likelihood | Compare only as baseline features. A confidently omitted request has no output token whose low probability reveals the omission. |
| Selective answering with an independently audited policy | Adopt for qualification. Report error among accepted cases together with acceptance coverage and sample counts. |
| Conformal prediction / conformal risk control | Evaluate later for bounded alternative sets or specified omission losses. [Conformal Risk Control, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f3549ef9b5ff520a7e41ff3cc306ab2b-Abstract-Conference.html) addresses expected bounded monotone loss under its assumptions, not a universal per-email confidence guarantee. |
| Semantic entropy | Optional challenger for output inconsistency, with sampling cost counted. Consistently incomplete answers can have low entropy; it does not certify input sufficiency. [Nature 2024](https://doi.org/10.1038/s41586-024-07421-0). |
| Input-span uncertainty attribution (ShaQ) | Research challenger for identifying useful clarification spans, not a default dependency. The [May 2026 preprint](https://arxiv.org/abs/2605.28170) relies on model-based perturbation/entropy analysis and requires domain and cost validation. |

Conformal alternative sets are possibilities, not confirmed simultaneous requests. Omission control over category sets does not cover two missed same-category request units. A bounded catalog method needs an explicit loss and separately validated decomposition. Ordinary marginal coverage also does not automatically control error conditional on automation or each rare financial category. Details and primary references are in the [risk review](confidence-risk-methods.md).

## 8. Data and experimental design

1. Write the question/cardinality/source rubric. Have two annotators independently assess input answerability before seeing model output or scores; adjudicate disagreements and retain their rate. Do not force consensus on an inherently undefined question: repair its contract or label it unresolved.
2. Annotate all active request units, including same-category requests, quoted history, withdrawals, references, and contradictions. Separately label candidate answers for whole-answer adequacy and reasons for failure. Do not review only the units the model detected.
3. Preserve authorized natural examples and create reviewed counterfactual variants: add a request, withdraw it, omit an attachment, resolve a referent, or change a relevant policy fact. Synthetic negatives supplement data; they do not establish production prevalence.
4. Group each source thread, customer/template family, document and its derived variants before partitioning. Keep training, development, assessor fitting, probability calibration, policy selection, and final audit disjoint (or use explicitly justified nested/cross-fitting). All final audit outcomes remain unavailable to tuning.
5. Compare deterministic checks, raw score baselines, a trained input assessor, and input-plus-answer verification. Use the same held-out cases. Include deliberately wrong and incomplete candidate answers so a verifier cannot succeed by always trusting a fluent solver.
6. Evaluate direct and bounded reasoning modes separately, plus their frozen escalation policy. Measure whether clarification obtains the missing fact and improves the final answer, not merely whether the question sounds plausible.
7. Qualify English first; retain German and code-switched evaluation slices without claiming multilingual calibration transfers. New weights, quantization, prompts, catalogs, extraction pipelines or policies require validation of the affected profile.

Model-produced semantic features may include proposed ambiguity or contradiction. They are not trusted preprocessing facts. Runtime retrieval diagnostics may include source presence and candidate counts; true retrieval recall needs independently known relevant items and is an offline metric. Do not leak gold answerability, gold request counts, or gold recall into an assessor's deployment features.

### Required acceptance cases

| Case | Required behavior |
|---|---|
| One clear request, all facts supplied | Answer without unnecessary clarification. |
| Two explicit requests in different categories | Return both; reject an all-requests single-choice mismatch. |
| Two distinct requests sharing a category | Preserve both request units and their entities. |
| Conditional or dependent requests | Preserve if/else, ordering and prerequisites; never run mutually exclusive branches as independent actions. |
| One request with two possible referents | Do not execute both; ask a distinguishing question. |
| Clear refund intent without transaction ID | Intent can be answerable while transaction identification is not. |
| Missing attachment or truncated conversation | Explicit source gap; no claim to have read missing content. |
| Current instruction withdraws an earlier request | Mark that request withdrawn while preserving unrelated active requests. |
| Conflicting facts without precedence | Preserve conflict; no invented resolution. |
| No supported catalog option | No-match rather than forced choice. |
| Predicate with absent evidence | Unknown rather than false or invented probability. |
| Financial ratio missing denominator/period | Do not infer a numeric score from incomplete units. |
| Regulation with missing jurisdiction/effective date | Distinguish text extraction from applicability assessment. |
| Two topics with an explicit primary-topic rule | Follow the rule if the evidence resolves it. |
| Correct label with irrelevant but real quote | Reject support even though substring validation passes. |
| Plausible answer omits one active request | Fail whole-answer adequacy despite per-label correctness. |
| Input instructs the model to ignore policy | Treat this as untrusted content; do not alter criteria. |

Report request-unit recall and any-omission rate; sufficiency confusion matrices and false acceptance of insufficient input; whole-answer correctness; evidence entailment; clarification benefit and unnecessary clarification; probability calibration (Brier/log loss and reliability plots); error-versus-automation coverage; and latency/cost. Set the error budget before policy selection. Audit a frozen policy on representative untouched data, reporting confidence bounds and counts. An aggregate bound is not a guarantee for an individual case or an under-sampled slice; changing traffic and correlated cases require explicit treatment.

## 9. Gap to the current implementation

| Existing component | What it establishes today | Needed for this proposal |
|---|---|---|
| `risk.py`: `_eligible`, `select_threshold`, `audit_threshold` | Thresholds mean generated-token log probability and audits exact-correctness outcomes. | Add separately specified semantic labels/assessor artifacts; retain the current path as a baseline, not input-confidence evidence. |
| `scoring.py`: `_evidence_valid` | Quoted text appears in input messages. | Source identity/offset validation plus independently measured support, contradiction, and completeness. |
| `scoring.py`: `score_generated` | Schema, configured field and exact-output checks. | Request-unit matching and rubric-based adequacy with acceptable equivalent answers; do not discard necessary strict contract validation. |
| `evaluation.py` | Versioned deployment profiles, split and lineage checks. | Bind answerability rubric, assessor, probability calibration and policy to a profile; audit all claimed semantics. |
| Existing public-source curation | Produces diagnostic data and optional augmentation. | Human-adjudicated sufficiency, omission and clarification labels; teacher agreement is not this evidence. |

The current `calibrate` command selects a threshold; its name does not mean an input-answerability probability estimator has been fitted. No runtime contract, model training or data download changes are part of this research update.

Recommended next implementation slice: specify the annotation and evaluator contract, establish a small blinded financial-message baseline covering the cases above, and only then train/compare the assessors. Select the least expensive method that improves input assessment and accepted-answer quality on the locked audit. There is no evidence yet that Foliqant achieves the proposed quality targets.

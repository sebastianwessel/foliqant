# Input answerability and whole-response risk methods

Date: 2026-09-19. Status: research proposal, not implemented, calibrated, or
qualified for production decisions.

## Recommendation

Do not expose one model-written confidence value. Treat reliability as four
different questions with different labels and controls:

1. **Question answerability:** does the supplied state contain enough
   non-conflicting information to answer this typed question under its stated
   criteria and cardinality?
2. **Request completeness:** when the task requires exhaustive extraction, does
   the response contain every active request unit in the message or thread,
   including distinct requests that share a catalog category?
3. **Whole-response adequacy:** is the returned response correct, complete,
   supported by the supplied state, and compliant with the output contract?
4. **Action eligibility:** do deterministic policy, authorization, and business
   prerequisites permit the downstream action?

The first three are supervised prediction or risk-estimation problems. The
fourth is application policy and must fail closed. A high probability for a
label cannot establish that evidence is sufficient, that no other request was
missed, or that an action is authorized.

The first baseline should be a separately supervised answerability assessor over
`(state, question contract)` and a whole-adequacy assessor over `(state,
response)`, followed by a locked selective policy and independent audit. Both
can be ordinary language models fine-tuned to emit bounded labels through the
existing chat API, or small application-side calibrators over frozen features.
Neither requires changing the generator architecture or accepting a
model-written confidence statement. Candidate log probabilities, where a
standard API exposes verified comparable scores, are optional features rather
than the definition of answerability.

Before validation, the semantic response may still contain statuses such as
`insufficient` or `conflicting`, but any numeric calibrated answerability or
adequacy value must be `null` or unavailable. Never substitute a generated
confidence percentage. Conformal label and request-candidate sets are optional
challengers after the supervised baseline. Their coverage and risk statements
are population statements under explicit assumptions, not correctness
guarantees for a particular email.

## Define the targets before choosing a method

Let `x` contain the versioned state, question definition, allowed labels,
evidence and provenance available at decision time. Let `q` be one typed
question, `Y_q` its adjudicated label, `R` the complete indexed collection of
active request units, and `z` the generated structured response. A request unit
has its own provenance and may share a category with another unit.

| Target | Definition | What it does not establish |
| --- | --- | --- |
| Label distribution | `P(Y_q = y | x, q)` for one bounded question | That the question is answerable, that every request was found, or that an action is allowed |
| Answerability | `A(x,q)` in `sufficient`, `insufficient`, `conflicting`, or `out-of-scope` | That the model used sufficient evidence correctly |
| Request truth | The complete active collection `R`, including zero, one, or several simultaneous request units and repeated categories | Which alternative label is right for one ambiguous request |
| Whole adequacy | `H(x,z)=1` only when the full response is correct, complete, supported, and contract-valid | Authorization or fulfillment of external prerequisites |
| Eligibility | `E(x,z,policy)` from deterministic policy and trusted system state | Statistical correctness beyond the checked prerequisites |

`P(Y_q | x,q)` is therefore the wrong denominator for both answerability and
completeness. Two simultaneous requests produce two units in `R`, even if they
map to the same category. One ambiguous request has one unknown target and may
produce a prediction set of alternative labels. A conformal label set for the
latter must not be presented as multiple active requests.

Answerability must be labeled from the input pair before viewing the model's
response. The adjudicator asks whether an ideal evaluator, restricted to the
supplied state, could determine the answer under the question's explicit
criteria. The rubric must cover required facts, time and policy version,
cardinality, unresolved contradiction, missing or truncated attachments, and
out-of-catalog cases. `unknown` or `insufficient` is a valid semantic answer to
some predicates; it is separate from the system abstaining because it cannot
reliably predict that semantic answer.

Call the input **substantively answerable** only when the supplied state resolves
the requested substantive value. An input labeled `insufficient`, `conflicting`,
or `out-of-scope` is not substantively answerable. Nevertheless, if the question
contract permits `unknown` or `insufficient`, returning that status with the
right support can satisfy `H`. Whole-response adequacy judges whether the model
gave the correct contract response; it does not require a substantive answer.
Such a response may support requesting information or review, never an action
that requires the missing substantive value.

For an input with a required question collection `Q`, define complete
substantive answerability separately: `A_sub_all(x,Q)=1` only if every required
substantive value can be resolved to its declared type and cardinality and,
where request extraction is part of the contract, the state supports identifying
all required request units. Do not average per-question answerability into this
target; one unsupported required value makes the complete input substantively
unanswerable. `H` may still equal one when the correct contract response reports
that missing information.

Request completeness requires full-thread annotation against the applicable
catalog, including withdrawn, quoted, corrected, still-active, and repeated
same-category requests. Reviewing only the model's detected units cannot reveal
omissions. At runtime the true number of active request units is unknown, so the
service cannot report an observed recall or “fraction captured” for that input.

Whole adequacy should be one preregistered binary target plus inspectable reason
codes. It requires every output demanded by the declared task scope, exact label
semantics, evidence that actually supports each conclusion, correct
missing-information declarations, and schema validity. An exhaustive
full-thread extraction task additionally requires the complete active-request
collection. A primary-topic question or an explicitly scoped typed question is
not inadequate merely because unrelated active requests are omitted. Literal
quote presence is a useful deterministic check, but it does not prove entailment
or sufficiency.

## Feasible scoring with standard inference APIs

Freeze the base model, prompt, tokenizer, quantization, runtime, constrained
decoding mode, catalog, and policy version before collecting calibration data.
Train the answerability assessor only from input-side features and adjudicated
answerability labels; it must not observe the generated answer or its claimed
confidence. Train the whole-adequacy assessor from frozen, out-of-fold generated
responses and adjudicated `H` labels. If its probability calibration succeeds,
`p_H(x,z)` estimates `P(H=1 | fitted features)` for the matching deployment
profile. This is an estimated population conditional probability, not a
guarantee for `z`.

For each bounded question or catalog candidate, prefer a stable short option ID
and, as optional assessor features, obtain comparable candidate scores through
either:

- log probabilities at the decision position from an ordinary chat/completions
  API; or
- bounded candidate-scoring requests with the same prompt prefix when the
  runtime exposes ordinary completion likelihoods for every required
  alternative.

Verify whether scores are measured before or after constrained-decoding
renormalization, and test option order and tokenization. Mean log probability of
the entire JSON response is only a baseline feature: prose length, punctuation,
and easy formatting tokens can dominate the score while an omitted request is
never scored.

Construct assessor features without asking the model to state its confidence:

- per-question top score, margin, entropy, prediction-set size, and disagreement
  between bounded scoring variants;
- required-field, schema, citation, catalog-ID, and cardinality checks;
- directly observed input-availability facts from trusted ingestion, such as
  whether a referenced attachment arrived, extraction succeeded, or bounded
  truncation occurred;
- separately supervised semantic predictions such as contradiction,
  unresolved-reference, out-of-catalog, and evidence-sufficiency states, clearly
  identified as model outputs rather than trusted preprocessing facts;
- runtime retrieval observables such as candidate count, candidate scores,
  cutoff, and source availability, where retrieval is used; and
- input length, thread structure, language, question type, and policy/catalog
  version.

True retrieval recall requires a gold relevant-item or correct-catalog label and
is therefore an offline evaluation metric. It must never be computed from or
smuggled into runtime features. Runtime candidate scores and counts are only
proxies and require their own validation.

Any learned semantic feature must itself be generated out of fold when fitting
the downstream assessor, then frozen before probability calibration. Otherwise
its in-sample errors make the adequacy layer look better than deployment.

Fit simple, inspectable assessors first: regularized logistic regression or a
small monotone tree model. Generate their training rows from out-of-fold base
model predictions so the assessor does not learn from in-sample model behavior.
Calibrate separate scores for answerability, each bounded answer,
request-collection completeness, and whole adequacy. Do not average or multiply
them; the events are dependent and answer different questions.

Temperature scaling is a reasonable multiclass baseline, but its success in the
original image and document classification experiments is empirical rather than
universal. Report Brier score, log loss, and reliability by source and class;
reject the probability interpretation if calibration does not transfer to the
locked audit set.

Local fine-tuning can teach the base model to emit explicit answerability states,
missing-information fields, stable option IDs, and exhaustive request-unit
records.
Training targets must come from adjudication or controlled transformations with
reviewed semantics, not teacher confidence. Continue to calibrate the adapted
checkpoint externally. No custom classification head, hidden-state hook, or
inference-engine fork is required.

## Selective prediction and conformal sets

### One typed question

For a single mutually exclusive target `Y_q`, split conformal prediction can
return a set `Gamma_q(x)` of plausible alternatives. A singleton can support an
automation candidate; a larger set expresses ambiguity and routes to review or
clarification. Standard marginal coverage means that the true label is included
at the target rate over exchangeable future examples. It does not give that
probability conditional on this input or certify that a singleton is correct.

### All active request units

This is an optional challenger only when the system has a nested candidate
generator over request units, with provenance and duplicate category labels
allowed. A label-only set cannot represent two distinct requests in the same
category. Let a candidate collection `C_lambda(x)` grow as the threshold becomes
more conservative. Calibrate one of two explicitly chosen losses on completely
annotated threads:

- `L_any = 1[R is not contained in C_lambda(x)]` controls the population
  probability of omitting at least one active request unit; or
- `L_fraction = omitted(R, C_lambda(x)) / max(1, |R|)` controls the expected
  fraction of active request units omitted under a frozen matching rule.

First verify endpoint feasibility: the maximal member of the nested family must
be capable of containing every annotated request unit under that matching rule,
and the chosen method's remaining assumptions must hold. If even the maximal
candidate collection misses units or cannot meet the target loss, disable the
bound and use abstention or the non-conformal fallback. Calibration cannot
recover a request unit the generator never produces.

Conformal risk control applies to bounded losses that decrease monotonically as
the candidate set grows, under exchangeability of the calibration and future
loss functions. `L_any` directly matches “all active requests captured” but may
produce large sets. `L_fraction` can hide a costly single omission on threads
with several requests. Predeclare the loss; do not select the more favorable one
after evaluation.

The result is a **possible-request superset**, not a confirmed intent list.
Multi-label methods that limit false positives solve a different problem and can
trade away recall. They may be useful for a separate shortlist, but that
shortlist cannot serve as proof of completeness. Exact multilabel conformal sets
over complete label vectors represent uncertainty about the entire truth set;
without structure they can be exponentially large.

### Whole-response automation

Let `s_H(x,z)` be the separately fitted score for `H`. A threshold creates a
selective policy that answers or automates some cases and defers the rest. Plot
accepted error against coverage across a threshold grid. Conditional error among
accepted cases is a ratio and is not automatically covered by the simple
monotone-loss conformal-risk result above.

Use either a valid selective-risk method or Learn-then-Test on a finite,
predeclared policy grid to choose a threshold while accounting for the search.
Then freeze the complete policy and apply Foliqant's independent exact-binomial
audit on a later untouched sample. The audit estimates the fixed policy's error
among accepted cases under its sampling assumptions. It is not a per-input
guarantee and does not survive arbitrary drift.

The recommended first experiment is the supervised answerability/adequacy
baseline plus a frozen audit; conformal candidates should be compared only after
that baseline is sound. Current Foliqant thresholding uses mean generated-token
log probability and whole-output exact correctness. Keep it as a reproducible
baseline. It does not yet estimate input answerability, semantic evidence
support, or request completeness, and its literal evidence validator establishes
occurrence rather than entailment.

## Distribution shift and partial information

Ordinary split conformal and conformal risk control rely on exchangeability.
Chronological changes in counterparties, document templates, products, catalog
definitions, policy, language mix, or human escalation behavior can invalidate
that premise.

Weighted conformal prediction can address a narrower covariate-shift case when
the conditional target law is stable and the train-to-target covariate density
ratio is known or accurately estimated from target covariates. A new policy that
changes the correct label, a new type of missing evidence, or a changed
annotation rubric is conditional or concept shift and is not repaired by
importance weighting.

Online conformal methods can adapt long-run set coverage under changing
distributions once outcomes arrive. They do not provide immediate conditional
correctness, cannot learn from cases without adjudicated feedback, and can hide
short harmful intervals behind aggregate behavior. Use them for monitoring and
recalibration research, with a conservative fallback during detected shift.

Operationally:

- version every weight, prompt, parser, catalog, policy, and scoring profile;
- detect support loss and changed input mix before applying a calibration
  artifact;
- abstain or request review for unseen policies, catalogs, extraction failures,
  and unsupported languages;
- recalibrate from recent adjudicated target data after a change; and
- retain chronological and unseen-catalog audits even when an aggregate bound
  passes.

## Data partitions and adjudication

Use distinct, lineage-bound partitions:

1. **Model training:** supervised fine-tuning and any synthetic robustness data.
2. **Development:** feature design, prompt selection, loss choice, and error
   analysis.
3. **Assessor fitting:** out-of-fold base-model outputs with human answerability,
   complete-request, evidence-support, and whole-adequacy labels.
4. **Probability calibration:** a held-out partition used only to map each
   frozen assessor score to a probability; do not choose policy thresholds here.
5. **Policy calibration:** another held-out partition for conformal thresholds
   or the predeclared selective policy grid.
6. **Locked audit:** one evaluation of the frozen policy and all claimed risks.
7. **Later temporal audit:** post-deployment target data, never used to justify
   the original claim retroactively.

No thread, customer, document, template, or derived family may cross these
partitions. If sample size makes separate assessor-fitting and probability-
calibration partitions impractical, use a predeclared nested cross-fitting
scheme that keeps each row out of every model and calibration map used to score
it. Policy selection still needs untouched data, and the locked audit is never a
cross-fitting fold.

Group by thread, customer or counterparty, duplicated template, source document,
and derived synthetic family before splitting. Keep related questions from one
state together. Include future-time, unseen-catalog, changed-policy, long-thread,
missing-attachment, contradictory-evidence, English, and German slices. A slice
with too few accepted examples is unqualified, not evidence of zero risk.

Answerability and full-completion labels need a written rubric and blinded
review. Adjudicators must see the supplied state and question contract, but not
model scores or the proposed action. Resolve disagreements before calibration
and preserve disagreement rates. Teacher distributions and teacher confidence
in synthetic typed-decision data are not adjudicated risk labels. Synthetic
deletions, contradictions, and withdrawals are useful stress tests; only
representative adjudicated target data supports a deployment risk claim.

## Metrics and release gates

| Object | Required measurements |
| --- | --- |
| One typed answer | Accuracy, macro and per-class recall, log loss, Brier score, reliability diagram with counts, conformal set coverage and size |
| Answerability | Per-question and complete-input confusion matrices; answer-on-insufficient and answer-on-conflicting rates; abstain-on-sufficient rate; accuracy conditional on sufficient input |
| Request completeness | Exact indexed-collection match, any-active-request omission rate, category and request-unit recall, false-negative proportion, candidate-collection size, no-request and out-of-catalog performance |
| Whole adequacy | Risk-coverage curve, accepted count and coverage, accepted errors, one-sided bound for the frozen policy, reason-code counts |
| Evidence | Literal span validity separately from human support, contradiction, and sufficiency judgments |
| Action policy | Unauthorized-action attempts, missing-prerequisite violations, eligible-action error, review/request-information rates |
| Shift and slices | The same counts by time, language, catalog, policy, source, thread length, attachment state, and rare high-cost request category |

Predeclare a small number of primary risks. Correct simultaneous claims or use a
method designed for multiple risk control. Marginal aggregate coverage can hide
poor rare-request or subgroup behavior; descriptive slice estimates do not become
guarantees without adequate samples and simultaneous inference.

A release candidate should fail closed unless all of these hold: the required
input envelope is parseable and evidence availability is explicitly represented;
the answerability status is accepted for the proposed disposition; the whole
response passes deterministic checks and the frozen selective-risk audit; and
action eligibility passes independently. An action that requires a substantive
value needs substantively sufficient input, while a correct `unknown` may permit
only clarification or review. A conformal request superset, if evaluated, is
supporting evidence rather than a mandatory product dependency. Human review
remains the fallback, not a label that converts an unsupported output into a safe
action.

## Expected failure modes

- Candidate retrieval omits the correct catalog item. No downstream model or
  conformal set over retrieved items can recover it; measure retrieval recall
  offline against gold and provide a broader-search or no-match path.
- Constrained decoding changes score semantics, option IDs split into different
  tokens, or a runtime omits log probabilities. The matching calibration artifact
  is inapplicable.
- The assessor learns formatting or source shortcuts and misses withdrawn,
  quoted, repeated, or newly introduced requests.
- Incomplete request annotations make omission control look better than it is.
- Exact-output labels penalize harmless representation differences, while loose
  semantic labels overlook unsupported conclusions. Preserve both contract and
  semantic reason codes.
- Marginal conformal coverage concentrates failures in rare or difficult groups,
  or candidate sets grow too large to be useful.
- Repeated customers, templates, and questions violate effective independence
  and make confidence intervals too narrow.
- Automation changes the future case mix through feedback and selective labels;
  reviewed cases alone may no longer represent automated cases.
- Missing attachments, truncation, stale policy, and contradictory state can
  yield a confident label despite an unanswerable input.

## Primary-source evidence and limits

- [Guo et al., *On Calibration of Modern Neural Networks*, ICML
  2017](https://proceedings.mlr.press/v70/guo17a.html) found modern neural
  classifiers poorly calibrated and temperature scaling effective on many of
  their image and document datasets. This supports a baseline, not universal
  calibration for generative financial outputs.
- [Geifman and El-Yaniv, *Selective Classification for Deep Neural Networks*,
  NeurIPS 2017](https://proceedings.neurips.cc/paper/2017/hash/4a8423d5e91fda00bb7e46540e2b0cf1-Abstract.html)
  formalized the accuracy-versus-coverage trade for a reject option. Its reported
  guarantees concern its procedure and evaluation setting, not arbitrary LLM
  scores or shifted email traffic.
- [Kamath, Jia, and Liang, *Selective Question Answering under Domain Shift*, ACL
  2020](https://aclanthology.org/2020.acl-main.503/) showed softmax-only
  abstention degraded under their unknown-domain QA mixtures and that an error
  calibrator benefited from known out-of-domain examples. This is evidence to
  train on representative failures, not proof that a calibrator handles unseen
  financial shifts.
- [Cauchois, Gupta, and Duchi, *Knowing What You Know*, JMLR
  2021](https://www.jmlr.org/papers/v22/20-753.html) developed marginally valid
  multiclass and multilabel confidence sets and structured methods for the
  otherwise exponential multilabel output space. Marginal coverage can remain
  uneven across inputs.
- [Fisch et al., *Conformal Prediction Sets with Limited False Positives*, ICML
  2022](https://proceedings.mlr.press/v162/fisch22a.html) treats exchangeable
  examples whose true output may contain any number of labels, including zero,
  and explicitly trades recall for false-positive control. It therefore cannot
  be reused as an all-requests-captured guarantee.
- [Angelopoulos et al., *Conformal Risk Control*, ICLR
  2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f3549ef9b5ff520a7e41ff3cc306ab2b-Abstract-Conference.html)
  controls the expected value of bounded monotone losses under exchangeability
  and demonstrates false-negative-rate control. Non-monotone selective losses
  need a different construction.
- [Angelopoulos et al., *Learn then Test*, Annals of Applied Statistics
  2025](https://doi.org/10.1214/24-AOAS1998) frames finite-sample risk calibration
  as multiple hypothesis testing and can handle several predeclared risks. Its
  validity still depends on the sampling and loss assumptions used by each test.
- [Tibshirani et al., *Conformal Prediction Under Covariate Shift*, NeurIPS
  2019](https://proceedings.neurips.cc/paper/2019/hash/8fb21ee7a2207526da55a679f0332de2-Abstract.html)
  derives weighted conformal prediction when train and test covariates differ but
  the relevant likelihood ratio is known or accurately estimated. This does not
  cover arbitrary changes to `P(Y|X)`.
- [Gibbs and Candès, *Conformal Inference for Online Prediction with Arbitrary
  Distribution Shifts*, JMLR
  2024](https://www.jmlr.org/papers/v25/22-1218.html) develops adaptive online
  prediction sets with local-regret guarantees under dynamic distributions. It
  needs sequential outcome feedback and does not give pointwise correctness.
- [Joren et al., *Sufficient Context: A New Lens on Retrieval Augmented Generation
  Systems*, ICLR 2025](https://arxiv.org/abs/2411.06037) separates whether a
  question-context pair contains enough information from whether a model answers
  correctly. Their results use an autorater and a small human-labeled sufficiency
  set, so they motivate Foliqant's target definition but do not validate an
  automated financial sufficiency judge.

No cited method removes the need for representative adjudicated labels, a frozen
deployment profile, explicit loss definitions, and a later audit. Until those
exist, Foliqant should expose answerability and calibrated adequacy as
`unavailable`, retain deterministic evidence and policy checks, and route
uncertain cases to clarification or review.

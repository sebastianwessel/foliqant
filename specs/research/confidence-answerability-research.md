# Input sufficiency, ambiguity, and coverage research note

Date: 2026-09-19. Status: evidence note, not an implemented contract or benchmark result.

## Research question

The desired value concerns the input: does the supplied message and context state the requests clearly, cover all relevant request units, and contain the evidence or prerequisites needed for a specified decision? It is not the model's self-reported confidence and is not interchangeable with the probability that a generated answer is correct.

The literature supports measuring several adjudicable targets rather than compressing them into one undocumented number:

- **Request-unit identification:** which distinct active questions or actions are present, including two requests that map to the same catalog category.
- **Ambiguity or underspecification:** whether a request unit has one supported interpretation, multiple plausible interpretations, or an unresolved referent or parameter.
- **Evidence sufficiency relative to a named decision:** whether the supplied context is enough to answer one particular question. Sufficiency for intent classification does not imply sufficiency for execution.
- **Required-information state:** which externally defined prerequisites are present, missing, conflicting, or inapplicable.
- **Coverage:** whether a response accounts for every gold request unit and required output field. At runtime, without a gold denominator, this is only a model prediction and must not be presented as an observed fraction such as `1/2`.

These targets separate input state from model capability. A capable model can fail to use sufficient context, and a weak model can abstain even when the context is sufficient. Conversely, repeated or low-entropy model answers do not prove that the input is complete.

## Primary evidence

| Source | What it measures | Transferable technique | Limits for this use case |
|---|---|---|---|
| [Sufficient Context: A New Lens on Retrieval Augmented Generation Systems (Joren et al., ICLR 2025)](https://arxiv.org/abs/2411.06037) | Whether supplied context contains enough information for a diligent reader to give a definitive answer; model outcomes are stratified by context sufficiency. | Annotate sufficiency independently of the tested model and always relative to a question. Report errors with sufficient and insufficient context separately. | The experiments concern QA and RAG. They do not define request decomposition or operational prerequisites in financial correspondence. Its autorater is still a model and needs human validation for a new domain. |
| [Unanswerability Evaluation for Retrieval Augmented Generation (Peng et al., ACL 2025)](https://aclanthology.org/2025.acl-long.415/) | Six types of knowledge-base-specific unanswerable queries, using unanswered and acceptable ratios to expose the answer/reject tradeoff. | Preserve typed reasons instead of one generic uncertainty state; evaluate answerable and unanswerable cases together so universal abstention cannot score well. | Queries are synthesized for RAG knowledge bases. Rejection quality does not measure whether all requests in one email were found. The paper finds no single tested configuration best across data distributions. |
| [Towards Reliable and Factual Response Generation: Detecting Unanswerable Questions in Information-Seeking Conversations (Lajewska and Balog, ECIR 2024)](https://arxiv.org/abs/2401.11452) | Answer presence at sentence, passage, and ranked-list levels in a conversational retrieval corpus. | Keep evidence availability at its natural granularity and aggregate explicit evidence units rather than asking for a free-form confidence score. | Presence in a retrieval corpus is narrower than business sufficiency. It does not establish that the evidence entails an action or meets authorization requirements. |
| [Do not Abstain! Identify and Solve the Uncertainty (Liu et al., ACL 2025)](https://aclanthology.org/2025.acl-long.840/) | Distinguishes document scarcity, model capability limitation, and query ambiguity; evaluates identifying and addressing the source. | Record the cause of an unresolved case. Missing evidence, ambiguous input, and model failure require different remedies. | The benchmark itself reports that models struggle to identify the root cause and tend to over-attribute uncertainty to query ambiguity. A model-produced cause is therefore a prediction, not ground truth. |
| [CLAMBER: A Benchmark of Identifying and Clarifying Ambiguous Information Needs in Large Language Models (Zhang et al., ACL 2024)](https://aclanthology.org/2024.acl-long.578/) | Ambiguity identification and clarification-question quality over a taxonomy and about 12,000 examples. | Evaluate detection separately from clarification quality; annotate ambiguity types; include clear inputs to measure unnecessary clarification. | The benchmark concerns information needs rather than financial workflow completion. The tested models' weak clarification behavior means a fluent question is not evidence that the correct gap was found. |
| [QuestBench: Can LLMs ask the right question to acquire information in reasoning tasks? (Lee et al., 2025 preprint)](https://arxiv.org/abs/2503.22674) | Selection of the information-acquisition question needed to solve an underspecified reasoning problem, compared with performance on its fully specified form. | Pair an incomplete case with a completed counterfactual and evaluate whether the requested missing fact is the one that changes answerability. | It uses option selection and constructed reasoning tasks. It does not validate open-ended clarification or workflow-specific required fields. |
| [Knowing but Not Showing: LLMs Recognize Ambiguity but Rarely Ask Clarifying Questions (Su and Cardie, May 2026 preprint)](https://arxiv.org/abs/2605.25284) | Ambiguity recognition under an explicit judgment task versus observed behavior in ordinary QA. | Test the diagnostic output and the operational behavior as separate endpoints. A good ambiguity classifier does not imply that the model will ask when needed. | This is a recent, non-peer-reviewed preprint. Its judge-based behavior labels and QA setting need independent replication before they support a product threshold. |
| [Localizing Input Uncertainty Quantification for Large Language Models via Shapley Values (Lee et al., May 2026 preprint)](https://arxiv.org/abs/2605.28170) | Attribution of input-induced uncertainty to spans using changes in conditional entropy under clarification; evaluated on ambiguity benchmarks and a clinical dialogue dataset. | Span-localized ambiguity could help reviewers see what wording needs clarification and supports controlled input perturbation tests. | This is a recent, non-peer-reviewed and computationally heavier method. It still derives uncertainty through a model and does not establish request coverage, evidence truth, or business prerequisites. |
| [Detecting hallucinations in large language models using semantic entropy (Farquhar et al., Nature 2024)](https://doi.org/10.1038/s41586-024-07421-0) | Uncertainty over meanings across sampled answers, grouping semantically equivalent generations before computing entropy. | Use as an optional model-behavior signal in research: disagreement among meaning-level samples can prioritize review better than surface-string variation. | It measures output uncertainty, not objective input sufficiency. A model can produce stable, confidently wrong, or consistently incomplete answers. Sampling and semantic clustering add cost and their errors become part of the score. |
| [AmbigQA: Answering Ambiguous Open-domain Questions (Min et al., EMNLP 2020)](https://aclanthology.org/2020.emnlp-main.466/) | Recovery of every plausible answer to an ambiguous question and minimally disambiguated rewrites for each interpretation. | Evaluate set recall over interpretations, not only whether one selected answer is valid. This is a useful foundation for completeness scoring. | Its latent interpretations of factoid questions differ from multiple explicit requests. Enumerating plausible interpretations is not the same as decomposing simultaneously requested actions. |

## Implications for a financial-message target

### Decompose requests before labels

The annotation unit should be the active request, question, or asserted decision predicate, not the final catalog label. Category sets lose information when distinct requests share one category. For example:

> Please reverse the duplicate card charge and send me a replacement card.

A singleton broad label such as `card_support` can be correct while the response is incomplete. Offline evaluation needs a human-adjudicated set of two request units and matching between predicted and gold units. Suitable metrics include request-unit precision/recall/F1, exact-set accuracy, and the fraction of examples with every request represented. Category accuracy should be reported after unit matching, not used as the coverage denominator.

At runtime, the system does not know how many units it missed. It may emit a categorical assessment such as `coverageAssessment: complete | possibly_incomplete | indeterminate`, with cited spans and a calibrated validation profile, but it must not calculate “one of two requests covered” from its own predictions. The `1/2` value is legitimate only in offline evaluation against two independently annotated gold units.

### Make sufficiency question-relative

Consider:

> Please refund the duplicate transfer. I cannot find the transaction reference.

The text may be sufficient to identify a refund intent and the stated reason. It is insufficient to identify the transaction or execute a refund if a verified transaction reference is a declared prerequisite. A single `sufficient: false` value hides this distinction. An adjudication should bind each assessment to a named target, for example:

| Target question | Input-grounded assessment | Reason |
|---|---|---|
| Is a refund requested? | sufficient | The request is explicit. |
| Which transaction is affected? | insufficient | The required reference is absent. |
| May the refund be executed? | insufficient or out of scope | Required identity, authorization, policy, and transaction facts are not all supplied. |

This also prevents an intent model from being penalized for missing execution-only information and prevents a clear intent label from being mistaken for an executable decision.

### Keep ambiguity, missing information, contradiction, and capability separate

A proposed annotation vocabulary should at least distinguish:

- `clear`: one supported reading for the target question;
- `ambiguous`: two or more materially different readings remain;
- `underspecified`: a required referent, constraint, or value is absent;
- `conflicting`: supplied evidence supports incompatible values or instructions;
- `unavailable`: the declared evidence source was not supplied or could not be read;
- `not_required`: the field is irrelevant to this target;
- `model_failure`: adjudicators find the input sufficient, but the evaluated model does not produce the supported result.

These states need operational definitions and multi-annotator examples. `Unknown` should not silently combine them. The required fields must come from the task, policy, or workflow contract where possible; letting the model invent its own prerequisites makes the measure circular.

## Evaluation design

Create paired and contrastive cases from authorized held-out examples, then have domain annotators label request units, target questions, required facts, evidence spans, ambiguity type, and permitted disposition. Preserve natural multi-request messages. Add controlled variants that remove one prerequisite, introduce a conflicting value, resolve an ambiguous referent, withdraw one request, or add a second request with the same category.

Report separate results for:

1. request-unit detection and exact request-set coverage;
2. per-target sufficiency classification and reason classification;
3. ambiguity detection, including false clarification on clear cases;
4. missing-field identification and clarification-question utility;
5. downstream answer or route correctness within each adjudicated input-state slice;
6. selective risk versus coverage after a policy chooses answer, request information, or human review.

Evaluate all of these against human or deterministic contract labels. Measure annotator agreement and retain disagreements as evidence that the task definition itself may be ambiguous. Split related templates, threads, customers, and counterfactual variants together to avoid leakage. Synthetic omissions and contradictions are valuable stress tests, but results on them do not establish prevalence or reliability on production mail.

Semantic entropy, token likelihoods, verbal confidence, and self-consistency can be compared as predictors of error. They should remain in a separate model-reliability evaluation. None should overwrite the adjudicated input-state labels or be exposed as an input-completeness percentage without held-out calibration demonstrating that exact interpretation.

## Research boundary

This note does not select an output schema, threshold, prompting method, model, or training recipe. It does not show that any current Foliqant model can reliably produce these fields. The recent 2026 sources are preprints, and all cited benchmarks require domain transfer validation. A release claim needs an English-first, held-out financial-message benchmark with explicit task-relative annotations; multilingual, production-prevalence, and execution-safety claims remain separate work.

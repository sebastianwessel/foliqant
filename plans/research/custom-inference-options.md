# What relaxing standard inference would buy Foliqant

Date: 2026-09-20. Status: bounded research proposal, not an architecture decision,
implemented feature, benchmark result, or authorization to train/download models.
The existing ordinary causal-model requirement remains unchanged.

Read with [model research](../../specs/research/model-research.md),
[answerability and reliability](../../specs/research/input-answerability-and-reliability.md),
and the [prior decision-scoring review](../reviews/decision-scoring-and-explanations-2026-09-20.md).
These define the desired semantics; this note compares alternative implementations.
Primary sources were checked on the date above; vLLM references are pinned to 0.29.0
where possible. No model execution, training, weight download, or paid call was run.

## Recommendation

Relaxing the requirement could make **bounded decisions, extraction, and assessment
cheaper and more direct to train**. It does not unlock reliable confidence by itself,
and most desired product features are already expressible through ordinary structured
causal inference. The strongest experiment is a small discriminative assessor beside
the existing causal solver, followed by evidence extraction if the assessor earns its
cost. Replacing the whole solver with a many-head encoder is premature.

Keep natural-language questions, caller-supplied catalogs, all request units, and a
short explanation for every returned value as the comparison contract. A specialist
that returns a category and score while omitting explanations is a smaller product,
not evidence that equivalent Foliqant inference became faster.

## Three different constraints

| Deployment choice | What changes | What remains practical |
| --- | --- | --- |
| Ordinary causal model; endpoint adapter | Request label scores or structured answers from an existing runtime. No custom head. | Current checkpoint/export direction; endpoint parity still needs testing. |
| Supported encoder/classifier in an existing server | Drop the causal-only requirement and use classification, scoring, or pooling endpoints. | Standard serving infrastructure may remain; ordinary desktop chat compatibility is no longer the acceptance criterion. |
| Custom heads or adaptive execution | Add model code, output decoding, and possibly custom runtime control. | A plugin or small maintained inference service can suffice; an engine fork is not automatically necessary. |

vLLM's [generative scoring endpoint](https://docs.vllm.ai/en/v0.29.0/serving/online_serving/generative_scoring/)
already scores specified next-token labels with a causal model. Its documented
response returns the first label's normalized score per item, not an arbitrary full
distribution. This is a runtime-specific endpoint adaptation, not a new architecture.
Multi-token labels and prompt continuations require separate handling.

vLLM's [classification support](https://docs.vllm.ai/en/v0.29.0/models/pooling_models/classify/)
includes `ModernBertForSequenceClassification`, single-label and multi-label problem
types, regression, and activation control. Its [pooling runner](https://docs.vllm.ai/en/v0.29.0/models/pooling_models/)
also exposes token-level classification/representations. These capabilities do not
prove that an arbitrary combined Foliqant head is supported unchanged.

[Model and IO plugins](https://docs.vllm.ai/en/v0.29.0/design/plugin_system/)
can register external model implementations and pooling preprocessing/postprocessing;
[out-of-tree registration](https://github.com/vllm-project/vllm/blob/main/docs/contributing/model/registration.md)
does not require a vLLM fork. An IO plugin alone cannot make unsupported model
computation work. A custom multi-head forward pass requires compatible model/pooler
implementation; adaptive layer skipping additionally needs execution support.
An HTTP wrapper only standardizes transport, not model portability or semantics.
Do not assume these heads survive the current GGUF/LM Studio/Ollama export path.

## Concrete gains and their limits

### 1. Directly trained answerability and adequacy estimators

An encoder can estimate input sufficiency from state, question, rubric, permitted
sources, and time boundary. A second role can inspect those inputs plus a proposed
answer and estimate whether the complete answer is correct, supported, complete,
and contract-valid. These are distinct targets, not renamed category confidence.

The gain is direct supervised losses, accessible logits/features, and potentially
less inference work than another generative assessment. An ordinary causal model can
already perform either role; the encoder is an efficiency/generalization hypothesis.
It may be worse on new rubrics, unfamiliar financial reasoning, and long documents.

Train adequacy on independently assessed, out-of-fold solver answers including
plausible omissions and incorrect evidence. Train input sufficiency without giving it
the solver's answer. Sharing weights does not permit answer leakage: a bidirectional
joint encoding of state and candidate answer contaminates an alleged input-only
head. Use distinct inputs/passes or a demonstrated masking design. A common encoder
does not make solver and assessor errors independent.

Neither sigmoid nor softmax is calibrated correctness.
[Guo et al.](https://proceedings.mlr.press/v70/guo17a.html)
show neural-classifier miscalibration and post-hoc calibration methods. Fit the chosen
calibration separately, then select policy thresholds separately and audit untouched
cases. Neither a calibration transform nor a larger classifier fixes the wrong target,
missing labels, shifted traffic, or insufficient data. Keep the joint adequacy target
direct; multiplying component probabilities introduces unjustified assumptions.

### 2. Evidence spans and several structured outputs in one encoding

Token/span heads can select source offsets directly, reducing opportunities to alter
literal quotes while generating JSON. They can associate support and contradiction
with particular requested values. Multiple trained heads can share input encoding for
bounded labels, issue predictions, and evidence. This avoids repeated encoding only
where their required inputs are genuinely the same.

[Transformers' ModernBERT implementation](https://huggingface.co/docs/transformers/en/model_doc/modernbert)
provides sequence, token, and extractive-QA heads. Its ordinary QA start/end head is
not a complete multi-request extractor: repeated same-category requests, overlapping
or discontinuous evidence, withdrawals, and conditional relationships require an
explicit representation and appropriate training. A span detector does not determine
whether a cited regulation applies to this customer.

For dynamic labels, [GLiNER](https://aclanthology.org/2024.naacl-long.300/)
demonstrates bidirectional, label-conditioned parallel entity extraction. This is
evidence for the mechanism, not proof of financial decision quality or complex
instruction following. A fixed output neuron per customer category would violate
Foliqant's changing-catalog goal. Compare label-conditioned spans or cross-encoding
each supplied option instead; count the cost of every candidate and test unseen
definitions and option permutations.

Offsets establish where text came from, not whether it supports the decision.
[ERASER](https://aclanthology.org/2020.acl-main.408/)
distinguishes rationale agreement from faithfulness. Evaluate evidence entailment,
counterevidence and omissions independently; attention or attribution scores are
not automatically evidence. Missing facts often have no extractable span, so a
span-only explanation cannot cover every answerability issue.

### 3. Better-fitting heads for bounded value types

Independent applicability heads naturally represent several simultaneous labels;
their probabilities need not sum to one. Ordinal heads can learn ordered rubric
thresholds instead of pretending each level is unrelated.
[CORAL](https://arxiv.org/abs/1901.07884)
is an example of an architecture-agnostic rank-consistent ordinal method. Ordered
outputs do not imply equal numeric spacing, calibrated uncertainty, or validated
financial meaning.

These heads could improve sample efficiency and constrained output consistency.
They need task-specific labels and losses, and unknown/no-match remain explicit
semantic outcomes. Per-category outputs do not distinguish January's statement
request from March's statement request. Neither a multi-label head nor a hierarchy
replaces request-unit extraction and dependency handling.

Changing catalogs favor descriptions supplied as inputs, not hard-coded head widths.
Different caller rubrics likewise require conditioning and held-out rubric testing;
a specialized ordinal head for one fixed priority scale is not a general scoring API.

## Explanations remain part of every returned value

A discriminative encoder does not generate an unrestricted short explanation.
Two honest alternatives are deterministic templates using validated structured
facts, or a retained causal model that explains the selected value using its criterion,
support, counterevidence and relevant missing facts. Templates work only where the
fields express the necessary reasoning; they are not a general substitute for prose.

For every returned choice, predicate, score, or request item, preserve the link to its
own criterion and evidence. A shared collection summary is supplementary. The prior
review's short-summary guidance can apply per explained value, but this is a proposed
comparison contract, not a change to the current question-level schema. If a product
returns several candidate scores, it must declare which values need individual
explanations and include their cost; do not silently compare to one winning label.

Treat extractor/assessor/solver disagreement explicitly. An explanation stage must
not rewrite the accepted decision to make its rationale sound convincing. If it finds
contrary evidence, trigger the declared reassessment/review path. Generated prose is
neither correctness proof nor a faithful trace of internal computation.

## Cascades, hierarchies, and early exit

A small specialist can handle a validated bounded slice and escalate the rest to the
causal model. A coarse catalog stage can narrow candidate comparisons, but parent
mistakes can exclude the correct child; measure candidate recall and allow broader
search/no-match. Both patterns are possible with standard endpoints already.
[FrugalGPT](https://arxiv.org/abs/2305.05176)
demonstrates learned model cascades; its reported savings are not a Foliqant estimate.

Internal early exit is different: classifiers attached to intermediate layers can
stop computation for selected examples. [FastBERT](https://arxiv.org/abs/2004.02178)
demonstrates this with extra training and inference control. It is not enabled merely
by adding a final classifier or IO plugin. Confidence-based exits can fail on shifted
or deceptively easy inputs, and irregular exits can reduce batching efficiency.
Defer this until ordinary fixed-depth inference is a measured bottleneck.

For a serial specialist-first cascade, expected compute cost is approximately
`specialist cost + escalation fraction × solver cost + explanation/validation cost`.
Escalated cases incur both stages; tail latency can increase even when mean cost
falls. Parallel assessment changes critical-path latency but still consumes resources.
Calibrate and audit the final routed population/policy, including false early accepts,
not only each standalone component's aggregate accuracy.

## Local feasibility and total ownership cost

The [ModernBERT model card](https://huggingface.co/answerdotai/ModernBERT-base)
provides concrete reference sizes: 149M/395M parameters and an 8,192-token context;
training is English/code focused, so it is not the automatic multilingual selection.
At two bytes per parameter, weights alone are approximately 0.30/0.79 GB (decimal
arithmetic, not measured resident memory). A small encoder therefore looks plausible
on a 64 GB Mac, but activations, optimizer state, sequence length, batching, and any
co-resident generator determine actual training/serving memory.

[PyTorch MPS](https://docs.pytorch.org/docs/2.9/notes/mps.html)
provides a Metal execution route. This is not evidence that a particular ModernBERT
configuration, fused attention path, or multi-head export has passed on either Mac.
Measure the selected attention implementation, unsupported operations/fallbacks,
peak memory and end-to-end latency on the actual M1-family and M5-family machines.
Do not transfer CUDA/FlashAttention paper speedups to Apple hardware. The existing
MLX causal-LoRA lifecycle is not automatically a training backend for these heads.

Smaller weights and no autoregressive output can reduce computation; expensive
annotations and maintenance may dominate total cost. Compare frozen-encoder head
training with fine-tuning before building a joint model. Multi-task loss balancing,
customer/catalog changes, multilingual qualification, calibration, model-version
coupling, packaging and deployment tests are additional work. A causal model retained
for all explanations may dominate latency and memory and erase much of the saving.

The [vLLM pooling documentation](https://docs.vllm.ai/en/v0.29.0/models/pooling_models/)
explicitly makes no speed-improvement guarantee over Transformers/Sentence
Transformers. Choose serving based on measured workload and operational fit.
For a bespoke forward pass, a small versioned Transformers/PyTorch service is also
an option; it need not become a novel inference engine. Keeping an HTTP API stable
does not remove its dependency, batching, monitoring and upgrade responsibilities.

## Bounded experiment before any architecture decision

1. Freeze independently annotated cases and targets first: input sufficiency,
   full-answer adequacy, all request units, per-value evidence and explanations.
   Group source/template families and counterfactuals across splits. Keep calibration,
   threshold selection and final audit separate. Repair ambiguous rubrics first.
2. Compare three arms on identical permitted state: current causal structured output;
   causal output plus ordinary generative assessor; causal output plus a small
   discriminative assessor. Keep solver and explanation deliverables identical in
   the assessor comparison. Use the same supervised labels where applicable; do
   not interpret a trained specialist versus zero-shot model as architecture evidence.
3. Only if assessment improves accepted-answer quality or equal-quality cost, compare
   an evidence span head and then a bounded specialist-first cascade. Test one added
   mechanism at a time. Leave fused all-purpose heads and early exit for later.
4. Report error at fixed acceptance coverage and coverage at the chosen error budget,
   confidence bounds/counts, sufficiency false acceptance, whole-answer adequacy,
   request omissions, evidence support, per-value explanation consistency, Brier/log
   loss and reliability plots. Include unfamiliar catalogs/rubrics, same-category
   requests, conditionals, missing sources, long inputs, German and code switching.
5. Measure label-only and complete-output latency separately, with complete-output
   results deciding product viability. Record warm/cold p50/p95, batch/concurrency,
   input/candidate/output sizes, resident/peak memory, training time and escalation
   rate. Evaluate supported-server packaging only for a challenger that wins.

Choose a challenger only if it clears a predeclared practical gain at the required
quality, including explanations and operational cost. No numeric Foliqant speedup,
accuracy advantage, training duration, or calibrated reliability is established by
the sources in this note.

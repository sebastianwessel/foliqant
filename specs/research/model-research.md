# Foliqant: financial decision model research and concept

Date: 2026-09-19. Status: proposal, not implemented or benchmarked.

Hardware update: the user has M1-family and M5-family Macs with 64 GB unified memory. See [local training and model lineage](apple-silicon.md). The 24 GB figures below remain a separate inference comparison budget, not the only available development hardware.

Scope: our own reusable model for interpreting financial emails and threads, identifying multiple intents, assigning priority, matching a supplied catalog, and providing evidence for workflow decisions. The financial domain includes funds, product disclosures, regulations, contracts, and financial reports; correspondence remains the initial operational task. English first; German second; preserve a multilingual foundation. **Development is local first on a 24 GB GPU, using ordinary vLLM, LM Studio, Ollama, or comparable inference engines. No special inference system is required.** Cloud custom-model deployment is a later packaging and operations choice, not the starting architecture.

This is independent model research, not a PURISTA or Harness implementation plan. No Voyage work is included. Repository baselines when researched: `purista 0e1f6ad6e`, `ai-harness b5e09a5`, `starter dc9807f`, `create-purista a87b269`. These repositories were clean and were not modified. Earlier integration research is preserved as historical background in [PURISTA integration research](background/purista-integration-research.md). It does not override this standalone model proposal.

## 1. Recommendation

Build a specialized, post-trained model on an existing multilingual open-weight model. Do not pretrain a foundation model from scratch. Our differentiation should be reliable interpretation of business messages, changing catalogs, evidence, abstention, and measured decision quality.

Start with a **current-model comparison constrained by measured 24 GB inference**, rather than a fixed 8–9B limit. Compare GPT-OSS-20B, Gemma 4 12B-it, and Qwen3.5-9B; add Qwen3.8-27B as a quantized quality-ceiling experiment. Qwen3-8B is an optional older reference, not the preferred foundation. Select the smallest model that meets the agreed error budget at useful automation coverage. Neither parameter count nor a reasoning leaderboard establishes suitability for this workload. Local inference is required; fitting every candidate's training run on the same card is a separate question.

The released product should contain:

1. Specialized model weights and a stable input/output contract.
2. A small calibration artifact and explicit rules for when to abstain.
3. Versioned preprocessing, prompts, inference settings, and a validation report.

Keep business catalogs and workflow policies outside the weights. The model interprets supplied definitions and evidence; application code authorizes and executes actions. For example, it can recognize a request to cancel a transfer without being permitted to cancel anything.

First prove the fine-tuned model locally in standard inference engines. Then deploy the same model lineage through a cloud provider's supported custom-model route. If a cloud container is needed, it should run a standard inference server, not a bespoke model engine. The cloud comparison below is future deployment guidance; it must not delay local development. There is not enough traffic information to honestly declare one platform cheapest.

### Standard inference is a product requirement

The model must remain an ordinary supported language-model architecture. No custom classification head, new operators, inference-engine fork, hidden-state hook, or required bespoke Python model loader belongs in version one. Training code can be specialized; deployment must not depend on that training stack.

Publish a merged Hugging Face checkpoint with Safetensors weights, tokenizer, configuration, and chat template, plus a tested GGUF conversion for desktop engines. Use ordinary quantization formats supported by each target rather than expecting one binary file to work everywhere. Keep MLX training/packaging separate from standard serving exports; Apple Silicon is now an explicit development target. Fine-tuning must preserve architecture compatibility, and conversion must preserve the intended prompt/template behavior.

| Runtime | Initial purpose | Model packaging | Output path |
|---|---|---|---|
| vLLM | Local automated evaluation and a possible later cloud server | Supported Hugging Face checkpoint / quantization | Standard chat completions and structured output |
| LM Studio | Interactive local inspection and developer use | Tested GGUF; MLX optional | Chat UI or local compatible API |
| Ollama | Simple local CLI and application integration | Tested GGUF plus Modelfile; supported Safetensors import is another option | Local chat API or compatible API |
| Cloud custom-model hosting | Later deployment of a validated checkpoint | Provider-supported checkpoint or standard-server container | Provider API with an ordinary client adapter |

vLLM documents compatible chat serving and structured output. LM Studio documents importing GGUF and using JSON-schema output. Ollama documents importing both Safetensors and GGUF, as well as local structured output. These capabilities support the proposed packaging; they do not establish that every architecture, quantization, template, and version combination has been tested. [vLLM server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/), [LM Studio import](https://lmstudio.ai/docs/app/advanced/import-model), [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output), [Ollama import](https://docs.ollama.com/import), [Ollama structured output](https://docs.ollama.com/capabilities/structured-outputs)

Separate **using the model** from **certifying its reliability**. The model works through ordinary inference requests without any companion service. An application that needs validated confidence applies a small calibration function to the returned observations. This is application logic, potentially just a few coefficients and thresholds; it is not another inference engine. For interactive desktop use without that function, show decisions and evidence, but do not label a generated confidence number as calibrated.

Begin confidence experiments with standard token log-probability APIs where available. Current Ollama documentation lists logprobs, and vLLM also documents them; nevertheless, verify the exact alternatives and scoring semantics returned by each installed runtime. Do not make vLLM-specific token-selection extensions a portability requirement. If a runtime cannot supply the validated score inputs, decisions and evidence still work; confidence is unavailable until a separately validated, ordinary-API scoring method exists. A second normal prompt assessing correctness is a possible experiment, not a trustworthy confidence mechanism by itself. [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility)

Local milestone: train a small supported adapter on Apple Silicon, prove the candidate-specific export path, and load the adapted artifact in compatible LM Studio/Ollama or another standard local engine. Run the same small English/German test suite and measure quality, memory, and latency. Validate unmodified vLLM separately on a supported host; do not assume it uses the Mac GPU. No cloud account is required for the Mac milestone.

## 2. What the model should learn

Train a reusable decision capability, not one fixed bank's routing table.

| Capability | Desired behavior | Important negative case |
|---|---|---|
| Multi-intent detection | Identify all active requests, with evidence for each | A quoted or withdrawn request is not automatically active |
| Thread interpretation | Track changes, corrections, and unresolved questions | Do not treat the latest sentence as overriding unrelated earlier requests |
| Catalog matching | Match descriptions and stable IDs supplied at inference time | Return no match or a broader parent category instead of inventing a leaf |
| Priority | Apply supplied business criteria and identify relevant deadlines | An email saying “URGENT” is evidence of a claim, not verified urgency |
| Predicate evaluation | Answer a bounded condition with supported, unsupported, or unknown | Missing evidence is not equivalent to false |
| Evidence extraction | Cite message IDs and exact spans supporting each claim | A plausible explanation without support is insufficient |
| Abstention | Distinguish ambiguity, missing context, and out-of-catalog requests | Never force every message into the nearest known class |

An inference request contains the question or task, thread content with provenance, relevant catalog definitions, policy version, and trusted context available at that moment. Do not include facts that only became known after the historical decision.

For large catalogs, retrieve candidate categories first, then let the model compare them. Candidate recall becomes a separately measured requirement: the model cannot choose a category that the retriever omitted. Keep a no-match path and a broader-search fallback. For small catalogs, supply all categories and avoid an unnecessary retrieval dependency.

Use multiple catalogs during training, with renamed and previously unseen categories in evaluation. Shuffle category order and transient option IDs to detect position bias. This teaches matching by meaning rather than memorizing `category_17` or “go to step 9.”

## 3. Proposed processing architecture

```text
Trusted mail ingestion and attachment extraction
  -> Normalize messages; retain authors, timestamps, quotes, and source spans
  -> Supply current catalog, policies, and permitted business context
  -> Retrieve catalog candidates only when necessary
  -> Specialized model: decisions + evidence + missing information
  -> Validate schema, category IDs, citations, and required fields
  -> Calibrate decision reliability using the deployed scoring profile
  -> Deterministic policy: route / request information / human review
  -> Authorized workflow execution and auditable outcome
```

Ingestion must distinguish trusted transport metadata from claims inside the email body. Deduplicate repeated quotes without losing provenance. Handle missing attachments and unsupported document extraction explicitly. Email text and attachments are untrusted input, never instructions that can change system policy.

Use one causal language model initially. Train it to produce a compact structured record rather than unrestricted prose. Keep its architecture unchanged. Custom classification heads and inference extensions are outside version-one scope because they violate the ordinary-runtime requirement.

A bounded reasoning mode can be an escalation path for difficult threads. Evaluate it against direct structured answering using the same data and risk target. More generated reasoning means more latency and cost; it must earn that cost through better decisions. A multi-pass scorer or explanation is also additional compute: do not advertise all of this as a single cheap forward pass.

### Illustrative result, not a measured prediction

Suppose message `m1` requests a card-payment dispute. A later message `m2` says: “I recognize the payment now. Please do not open a dispute. Please send me a receipt.” An appropriate record could be:

```json
{
  "catalogVersion": "demo-1",
  "activeIntents": [
    {
      "categoryId": "payment.receipt",
      "evidence": [{ "messageId": "m2", "quote": "Please send me a receipt." }]
    }
  ],
  "withdrawnIntents": [
    {
      "categoryId": "payment.dispute",
      "evidence": [{ "messageId": "m2", "quote": "Please do not open a dispute." }]
    }
  ],
  "priority": {
    "level": "normal",
    "reason": "No deadline or ongoing loss is stated in the supplied thread."
  },
  "missingInformation": ["verified payment reference"],
  "reliability": {
    "status": "uncalibrated",
    "estimatedCorrectness": null
  },
  "recommendedDisposition": "request_information"
}
```

The model supplies the semantic fields. The service attaches calibration status and applies the disposition policy; the model does not invent a confidence percentage or grant access. Production records also retain validated span offsets and model, prompt, parser, catalog, and policy versions. The example deliberately uses `null` because no calibration experiment has been performed.

An application rule can then explain the actual branch: receipt requested, dispute withdrawn, payment reference missing, therefore request the reference before retrieving a receipt. That rule trace is reproducible even though the model's internal reasoning is not.

## 4. Reasoning and explanations

A reasoning-capable model is worth testing for changes of intent, negation, time order, and competing requests. It is not automatically necessary to obtain a useful explanation: an instruction model can generate evidence-backed explanations too.

Do not promise that a written chain of thought is a faithful account of how a model reached its decision. Research has demonstrated cases where reasoning traces omit influences on the answer. Those experiments do not measure our proposed financial model, but they rule out assuming faithfulness merely because a trace sounds convincing. [Anthropic research](https://www.anthropic.com/research/reasoning-models-dont-say-think)

Instead, require a reviewable decision record:

- The conclusion and exact source evidence.
- Contradicting evidence and unresolved ambiguity.
- Missing facts that could change the conclusion.
- The applicable business criterion and actual downstream rule trace.

An exact quote check proves that text exists, not that it supports the conclusion. Evaluate support with domain reviewers, contradiction cases, and controlled edits: withdrawing a request should change its active status; changing a customer's name should not change priority. Evidence deletion tests are useful diagnostics, not proof of causal faithfulness.

## 5. Confidence that means something

The winning class need not have high probability: `[0.26, 0.25, 0.25, 0.24]` still has a winner. However, a token probability or a model-written “99% confident” is not automatically an estimate of business correctness.

Define three separate concepts:

| Concept | Meaning | How to establish it |
|---|---|---|
| Decision reliability | How often comparable predictions agree with an adjudicated target | Held-out labels and calibration |
| Evidence sufficiency | Whether the required information is available and supports the claim | Explicit requirements, source checks, and adjudication |
| Event probability | Chance a future event will happen, such as a missed payment | Historical outcome data and a forecasting evaluation |

A clear intent with a missing account identifier can have high classification reliability but still be unsafe to act on. Confidence does not replace prerequisites or authorization.

### Scoring design

First evaluate scores for bounded decisions, not the likelihood of an entire JSON document. Where practical, use ordinary single-token option IDs with a runtime mapping to category descriptions. Obtain comparable scores for all relevant alternatives; for multilabel tasks, evaluate each candidate's applicability rather than forcing all intents into one mutually exclusive distribution.

Token scores remain model scores. Fit temperature scaling for suitable multiclass outputs or a simple binary calibrator for accept/reject correctness. Temperature scaling is a well-established starting point, not a universal guarantee. [Guo et al., 2017](https://proceedings.mlr.press/v70/guo17a.html)

If margins alone perform poorly, compare a small correctness predictor using out-of-fold model errors, score margins, missing-context flags, and retrieval diagnostics. Research supports training models to recognize their errors, but its published results do not guarantee performance on our data. [Kapoor et al., 2024](https://arxiv.org/abs/2406.08391)

Evaluate two scoring implementations in the feasibility stage: scores captured at the decision positions during generation, and bounded candidate-scoring requests with shared prompt prefixes. The latter is easier to inspect but may cost extra passes. Confirm tokenizer behavior, option-order sensitivity, and whether scores are taken before constrained-decoding or sampling renormalization. Unsupported or missing score access limits the confidence feature, not the ability to run the model. Never silently replace a validated score with a self-reported number.

Calibrate the correctness of the **whole routing decision** as well as important individual fields. Do not multiply intent, priority, and catalog scores as if their errors were independent. If a reasoning escalation changes the population or output, validate that path separately.

### Data separation and release evidence

Keep distinct training, development, calibration, and final audit data. Use an additional threshold-selection partition or nested splits so fitting scores and choosing the automation policy do not consume the final audit set. Group related threads, customers, duplicates, and templates to prevent leakage. Include chronological and unseen-catalog tests.

Use real production prevalence for the main risk estimate and report rare-case stress tests separately. Synthetic cases are useful for training and robustness, but cannot establish production confidence.

Measure:

- Accepted error rate versus automation coverage, including uncertainty intervals.
- Recall for urgent cases and costly missed intents.
- Catalog retrieval recall, false matches, and unknown-category handling.
- Brier score, log loss, and reliability plots with sample counts.
- Complete-route correctness, evidence support, latency, and cost.
- English, German, long-thread, attachment, and category slices separately.

An indicative statistical check: after freezing the acceptance policy, zero observed errors among 299 representative i.i.d. accepted cases from the target deployment population, evaluated at a fixed sample size, gives a one-sided 95% exact binomial upper error bound of about 1%; 598 cases gives about 0.5%; 2,995 gives about 0.1%. These are derived from `1 - 0.05^(1/n)`. They are not per-email guarantees, do not survive arbitrary distribution shift, and do not establish the same limit for every rare category. Correlated cases, repeated inspection until a bound passes, and many simultaneous claims require a more careful evaluation design.

Conformal prediction could later return a set of plausible categories. Its standard coverage promise is marginal under assumptions such as exchangeability; it does not mean a particular email is 95% correct, nor automatically certify singleton-only routing accuracy. It is optional for version one. [Angelopoulos and Bates](https://arxiv.org/abs/2107.07511)

Version calibration with the exact deployed weights, tokenizer, prompt, quantization, runtime, and scoring method. Quantization can affect calibration, so local and cloud variants need separate validation. [Proskurina et al., 2024](https://arxiv.org/abs/2405.00632)

## 6. Model shortlist and 24 GB feasibility

Qwen3-8B does support reasoning: its checkpoint offers both thinking and non-thinking modes. It remains a useful baseline, but compatibility alone is not a reason to prefer it to current alternatives. [Official model card](https://huggingface.co/Qwen/Qwen3-8B)

| Candidate | Role in the experiment | Reason to include | Main uncertainty |
|---|---|---|---|
| [GPT-OSS-20B](https://developers.openai.com/api/docs/models/gpt-oss-20b) | Main text-reasoning challenger | Open weights, Apache 2.0, configurable reasoning, structured output and fine-tuning; native MXFP4 makes local inference credible | Task/German quality and adapted-checkpoint packaging must be measured; 24 GB inference does not establish 24 GB training |
| [Gemma 4 12B-it](https://huggingface.co/google/gemma-4-12B-it) | Modern dense cross-family challenger | Configurable thinking and multilingual capability; quantized local inference is plausible | BF16 leaves inadequate 24 GB runtime headroom; pin and verify runtime/quantization support; adapter training still needs a memory trial |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | Lower-memory adaptation candidate | Reasoning-capable multilingual model with standard runner support and more room for local QLoRA experiments | Not the newest Qwen generation; deployment support is architecture-specific |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Latest-Qwen quality-ceiling candidate | Controllable reasoning with official standard-serving guidance; investigate whether newer capability changes the economics | Quantized inference requires bounded context/concurrency; 24 GB training is not a dependable starting assumption |

These are research candidates as of 2026-09-19, not measured quality rankings. Screen the first three without training, then adapt the best one or two. Use the post-trained/instruction-capable checkpoint first; a Base checkpoint is an option for a later recipe that deliberately owns instruction/reasoning post-training. Do not infer financial or legal reliability from coding/math benchmarks.

OpenAI's local guide gives 16 GB as an orientation for GPT-OSS-20B, while its 120B model needs substantially more memory. GPT-OSS-20B has approximately 21B total and 3.6B active parameters: the active count reduces computation, not total resident weights. Its quantization is essential to the memory claim. Preserve its Harmony template through adaptation and conversion; ordinary supported runtimes handle that format without a custom inference engine. Official local and fine-tuning recipes exist, but older archived installation instructions should not replace current runtime documentation. [Ollama guide](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama), [fine-tuning recipe](https://developers.openai.com/cookbook/articles/gpt-oss/fine-tune-transfomers)

Current packaged quantized models provide orientation, not end-to-end memory guarantees: Ollama lists roughly 7.6 GB for Gemma 4 12B and 18 GB for Qwen3.8-27B. Add runtime state and test actual headroom. Current vLLM supports Gemma 4, but its documented BF16 recipe requires more than our 24 GB card; select a supported quantization and verify the exact runtime version. [Gemma packages](https://ollama.com/library/gemma4), [Qwen packages](https://ollama.com/library/qwen3.8/tags), [vLLM Gemma recipe](https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md)

Reserve challengers are [Ministral 3 8B Reasoning](https://huggingface.co/mistralai/Ministral-3-8B-Reasoning-2512), with explicit English/German support, and [Gemma 4 26B-A4B-it](https://huggingface.co/google/gemma-4-26B-A4B-it). The latter's approximately 25.2B resident parameters must not be confused with its 3.8B active parameters. Neither is a reason to expand the first experiment indefinitely. A small model can be added later as a speed baseline.

Also include a conventional small classifier or encoder baseline for fixed labels. If it achieves the required quality, a language model may only be needed for changing catalogs and difficult threads. Do not add a cascade until measurements justify its complexity.

### Memory budget

Arithmetic weight-storage lower bounds, in GiB, using nominal parameter counts:

| Parameters | BF16 | 8-bit | 4-bit |
|---|---:|---:|---:|
| 4B | 7.45 | 3.73 | 1.86 |
| 8B | 14.90 | 7.45 | 3.73 |
| 9B | 16.76 | 8.38 | 4.19 |
| 12B | 22.35 | 11.18 | 5.59 |
| 21B | 39.12 | 19.56 | 9.78 |
| 27B | 50.29 | 25.15 | 12.57 |

These are not full runtime requirements. Add quantization metadata, any unquantized weights or vision components, activations, workspaces, and attention state. Qwen3-8B actually has about 8.2B parameters; nominal names are not exact memory specifications.

For a concrete example, Qwen3-8B's configuration has 36 layers, 8 key/value heads, and head dimension 128. A BF16 key/value cache uses approximately `2 × 36 × 8 × 128 × 2 × token_count` bytes per sequence: 1.125 GiB at 8,192 tokens, 2.25 GiB at 16,384, and 4.5 GiB at 32,768. Count input and generated tokens, including reasoning; concurrent sequences multiply this requirement. This calculation does not apply unchanged to hybrid-attention models. [Official configuration](https://huggingface.co/Qwen/Qwen3-8B/raw/main/config.json)

Start local trials at 8k input tokens, a bounded output budget, and concurrency one; stress-test 16k next. Prefer a measured 4-bit or 8-bit deployment with headroom over squeezing the largest weights into the card. Handle longer threads explicitly; never silently truncate away the evidence needed for a decision. An advertised 128k/256k context is not a promise that it fits on 24 GB.

Inference feasibility and training feasibility are different. QLoRA trains adapters while keeping the base model quantized, reducing memory use. A short-context 8B pilot on 24 GB is reasonable to test; long-context training may need rented larger GPUs. Do not promise full fine-tuning on this card. [QLoRA paper](https://arxiv.org/abs/2305.14314)

## 7. Training data and multilingual progression

Use supervised fine-tuning first, initially with QLoRA. Train compact, correct outputs and evidence rather than verbose invented reasoning. Merge adapters into a complete checkpoint when required by the deployment platform, then quantize and validate the resulting artifact. Preserve an unquantized release checkpoint as well.

Training examples should contain:

- The thread as visible at decision time, with stable message IDs and provenance.
- Catalog and policy snapshots, active and withdrawn intents, and acceptable alternatives.
- Priority criteria, claimed urgency, verified context, and missing information.
- Evidence spans, contradictions, adjudicated labels, and annotation-guideline version.
- Hard negatives: near-neighbor categories, no match, quoted requests, prompt injection, and missing attachments.

Start with a few hundred cases to establish task definitions and baseline failures, then approximately 1,000–3,000 carefully adjudicated examples for a first fine-tuning experiment. These are planning ranges, not promised sufficiency. Grow toward several thousand or more only when learning curves identify a benefit. Calibration and final audit need additional representative cases, with counts driven by the intended risk claim.

Public data can help bootstrap, but cannot replace a real evaluation set. BANKING77 contains 13,083 English customer-service queries across 77 intents and is labeled CC BY 4.0. It is useful for initial intent experiments; it does not provide full email threads, changing intent, or our evidence and confidence targets. [Dataset card](https://huggingface.co/datasets/PolyAI/banking77)

### Ranked public-data candidates

These sources teach different components of the task. None supplies a complete, representative, labeled financial-email routing dataset. License labels below are source metadata, not blanket legal clearance for every downstream use.

| Source | Useful contribution | Limits and stated terms |
|---|---|---|
| [MultiDoGO finance](https://github.com/awslabs/multi-domain-goal-oriented-dialogues-dataset) | Multi-turn finance dialogues, intent/slot labels, and multiple intents per turn | Elicited dialogue rather than email; retain conversation IDs across splits. CDLA-Permissive-1.0 in the repository license. |
| [BANKING77](https://huggingface.co/datasets/PolyAI/banking77) | Banking vocabulary and closely related intents | Single-utterance English classification; CC BY 4.0; useful training/diagnostic material, not final production validation. |
| [MInDS-14](https://huggingface.co/datasets/PolyAI/minds14) | Banking requests across languages, including English and native German | Speech/transcriptions, 14 intents, not email threads. CC BY 4.0; German subset has 611 examples in the current card. Keep a recording and its English translation in the same split. |
| [Bitext Retail Banking](https://huggingface.co/datasets/bitext/Bitext-retail-banking-llm-chatbot-training-dataset) | 25,545 question/answer pairs across 26 intents; linguistic and slot variation | Hybrid synthetic data; strong template-leakage risk. CDLA-Sharing-1.0; review data-redistribution terms. Use intent labels and requests, not generic response scripts as our bank's policy. |
| [CFPB complaints](https://www.consumerfinance.gov/data-research/consumer-complaints/) | Real financial problem descriptions and product/issue categories | Publisher permits use, analysis, and building on published data. Complaints are not representative of all customers or verified business facts; no email-thread routing labels. |
| [Enron email](https://www.cs.cmu.edu/~enron/) | Parsing, quotation, thread chronology, and business-email robustness | Wrong domain/era, privacy cautions, no target labels, and no clear standard training license on the landing page. Prefer parser research; do not assume commercial-training permission. |
| [ConvFinQA](https://github.com/czyssrs/ConvFinQA) | Optional numerical reasoning with financial tables and conversational follow-ups | Repository is MIT-labeled; source-material rights still deserve review. Financial-report QA is not customer-email triage. Use only if numerical reasoning is in scope. |

For Bitext, CDLA-Sharing distinguishes data redistribution from computational results. Do not casually interpret it as either unrestricted data reuse or a requirement to publish all model weights; review the actual terms before distributing a derived corpus. [License text](https://cdla.dev/sharing-1-0/)

Public benchmark examples may already have been seen during foundation-model training. Keep them as training resources or diagnostics, and deduplicate across sources before splitting. Production accuracy and confidence must be measured on inaccessible, human-adjudicated target threads. Do not expose that final holdout through teacher prompts, synthetic-data generation, retrieval indexes, or calibration fitting.

### Finance-specific checkpoints versus task-specific adaptation

Recent finance-specific adaptation is also worth testing: [ODA-Fin-SFT-8B](https://huggingface.co/OpenDataArena/ODA-Fin-SFT-8B) is a 2026 financial reasoning fine-tune of Qwen3-8B. Its [ODA-Fin-SFT-318k corpus](https://huggingface.co/datasets/OpenDataArena/ODA-Fin-SFT-318k) contains English/Chinese financial reasoning data distilled from a larger model. Treat it as an optional domain challenger and a source of carefully filtered training examples. It does not establish German capability, email-state accuracy, or operational legal reliability. The dataset's Apache metadata does not override its explicit warning that the 25+ upstream sources have their own licenses. Audit source provenance before selecting a subset; exclude any overlap with evaluation tasks.

A financial label on a checkpoint does not establish suitability for operational correspondence. FinMA-7B-NLP documents sentiment, news-headline classification, named-entity recognition, and QA. FinGPT is a collection of financial adaptation work, models, and tooling, with prominent news/sentiment and report tasks. Neither source establishes an advantage for our multilingual email-state and routing workload. Their source licenses also do not replace the underlying model and dataset terms. [FinMA model card](https://huggingface.co/ChanceFocus/finma-7b-nlp), [FinGPT repository](https://github.com/AI4Finance-Foundation/FinGPT)

Prefer a current general reasoning model, then specialize on the actual job. Treat any finance-specialized candidate as a challenger, not an automatic winner. Our useful specialization is recognizing corrections, distinguishing claimed from verified urgency, resolving multiple requests, choosing among supplied category descriptions, citing evidence, and withholding unsupported decisions.

Create controlled training contrasts: the same request later withdrawn; a deadline changed; an unrelated urgent sentence added; a quoted historic request versus an active one; an attachment missing; a category renamed; and two simultaneous requests needing different routes. Labels must reflect the changed evidence and policy. Human-written or reviewed synthetic cases can fill these gaps initially, but synthetic tests cannot establish real-world reliability.

Do not use publicly accessible financial text as if publication automatically grants training rights. Check licenses and privacy requirements before ingestion. Historical production data requires appropriate authorization, minimization, and retention controls. Teacher-generated labels and synthetic threads can supplement approved training data, but human review must establish correctness; never use the teacher's own confidence as ground truth.

Language progression:

1. Start from a multilingual checkpoint. Establish a small German evaluation set immediately, even while the initial production scope is English.
2. Fine-tune primarily on English with a controlled multilingual replay mix where data rights permit. Measure whether adaptation degrades German capability.
3. Add native German business examples and adjudication, including date formats, terminology, quoted correspondence, and mixed-language threads.
4. Enable German automation only after German risk/coverage evaluation and adequate calibration. Translation alone is not validation.
5. Treat other languages as unsupported for automated routing until evaluated; return a review requirement rather than silently applying English thresholds.

Avoid training volatile facts such as account status or the current catalog into weights. Continued domain pretraining or reinforcement learning should be later experiments only if supervised adaptation leaves an identified gap.

### Funds, regulations, legal documents, and financial reports

Expand domain coverage without turning the first release into an unrestricted financial/legal adviser. Teach document comprehension, evidence selection, scope/date awareness, and escalation. Retrieve the applicable product terms and regulatory material for each decision. A model may learn what a redemption restriction looks like; the actual restriction for a particular fund and share class must come from the applicable document.

| Source | Proposed use | Important limits and rights checks |
|---|---|---|
| [SEC Mutual Fund Prospectus Risk/Return Summary](https://www.sec.gov/data-research/sec-markets-data/mutual-fund-prospectus-riskreturn-summary-data-sets) | Fund-specific text and numeric disclosures for extraction and grounded QA; build dated training cases and hold out fund families/periods | Official flattened extracts, updated quarterly; use the complete filing to resolve missing context or extraction errors. Raw disclosures are not ready-made question/answer labels. |
| [SEC Form N-PORT](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets) | Structured holdings, exposures, and portfolio information; numerical extraction and deterministic cross-checks | Publicly released filings only; not live fund status and not complete prospectuses. Preserve reporting date, filing date, fund identity, and units. |
| [TAT-QA](https://github.com/NExTplusplus/TAT-QA) | Financial-report table-plus-text reasoning; useful initial auxiliary supervised dataset | 16,552 questions over 2,757 contexts. Dataset explicitly CC BY 4.0, while repository code is MIT; do not confuse the two licenses. Split by source report/company. |
| [FinQA](https://github.com/czyssrs/FinQA) and [ConvFinQA](https://github.com/czyssrs/ConvFinQA) | Evidence-backed arithmetic and follow-up questions about financial reports | MIT-labeled repositories; account for shared source questions across derivatives. Pin corrected FinQA preprocessing: maintainers disclosed a historical label-leakage bug. These tasks do not demonstrate fund-regulation or email-routing accuracy. |
| [MultiEURLEX](https://huggingface.co/datasets/coastalcph/multi_eurlex) | Hierarchical multi-label legal classification and English/German transfer | 65k EU laws across 23 languages with chronological splits. Licensing discrepancy: card body says CC BY 4.0, metadata says CC BY-SA 4.0. Resolve before commercial incorporation; do not assume the more permissive label. |
| [EUR-Lex-Sum](https://github.com/achouhan93/eur-lex-sum) | Grounded legal summarization across languages, including English/German | CC BY 4.0 data artifacts per authors. Summaries and underlying acts overlap with other EUR-Lex corpora; group by source act across languages and datasets. |
| [CUAD / MAUD / ACORD](https://www.atticusprojectai.org/datasets/) | CUAD: clause extraction; MAUD: merger-agreement questions; ACORD: relevant-clause retrieval | Publisher labels these datasets CC BY 4.0. Useful contract capabilities, primarily English commercial agreements; not a substitute for EU fund-law cases. Select only tasks that map to measured failures. |
| [FinanceBench](https://huggingface.co/datasets/PatronusAI/financebench) | Optional report-QA evaluation after permission review | Public release is 150 annotated examples, not the full 10,231-question collection. Card is CC BY-NC 4.0: exclude from the commercial training pool without separate permission, and review commercial evaluation use too. |

The SEC states that government-created website content and EDGAR public filings are free to access and reuse. Retain attribution/provenance and review the precise material rather than extending that statement to unrelated proprietary feeds. [SEC reuse statement](https://www.sec.gov/about/webmaster-frequently-asked-questions)

For current legal/product facts, build a separate versioned retrieval collection:

- **EUR-Lex:** applicable EU acts, amendments, and consolidated versions, including relevant UCITS, AIFMD, PRIIPs, and MiFID material. Preserve CELEX identifier, jurisdiction, effective/applicability dates, and source status. Its reuse notice generally permits commercial reuse but explicitly identifies exceptions such as International Accounting Standards and third-party works; do not assume IFRS/IAS text is freely trainable merely because it is published there. [Legal notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html)
- **German statutes and supervisory material:** selected KAGB/WpHG/KWG provisions from [Gesetze im Internet](https://www.gesetze-im-internet.de/), and applicable BaFin publications/product documents. Retain source and publication history; the rights to an issuer-authored prospectus are not automatically the same as those to a statute. Secure rights before bulk training or redistributing a derived corpus.
- **EBA Single Rulebook Q&A:** practical regulatory questions, status, dates, and legal references. The EBA says Q&As are not legally binding and are not systematically updated after changes to legislation. Check applicability rather than treating any retrieved answer as current law. [Q&A overview](https://www.eba.europa.eu/single-rulebook-qa), [search and disclaimer](https://www.eba.europa.eu/single-rule-book-qa/all)
- **Fund documents and registration data:** licensed English/German prospectuses, PRIIPs KIDs, supplements, annual reports, and official registration/marketing-status records, including [ESMA registers](https://registers.esma.europa.eu/publication/helpPage). A register entry is bounded status evidence, not a blanket conclusion about whether a transaction is permitted. Actual product documents require per-source rights checks.

Prioritize a small training mix: task-specific correspondence, TAT-QA/selected FinQA examples, fund-disclosure extraction, and legal classification/summarization after license clearance. Use CUAD only if contracts are part of the first measured workload. Do not ingest every corpus simply because it is financial or legal. Preserve individual source/task tags so we can remove a source and measure whether it actually improves target performance.

Create expert-reviewed cases connecting these documents to real requests: identifying a fund/share class; finding a stated dealing deadline; distinguishing a report's period from publication date; checking fee units and denominators; locating an exception to a clause; finding the applicable dated rule; and returning insufficient information when client category, jurisdiction, or the relevant document is missing. Calculate financial quantities in deterministic application code where possible; the model supplies cited inputs and the intended operation. Legal applicability and high-impact decisions require qualified review until explicitly validated and authorized.

Evaluation must hold out whole document families: a regulation and its translations/amendments/summaries, a prospectus and its supplements/share classes, and a report plus questions derived from it. Also test incorrect-jurisdiction retrieval, stale versions, conflicting documents, OCR/table errors, and absent evidence. Existing public benchmarks may be in base-model training; final acceptance needs fresh, private, dated English/German cases. We have not identified a public dataset that already joins all of fund documentation, regulation, email-thread state, and operational decisions with appropriate ground truth.

## 8. Later production hosting comparison

This selection follows successful local development. A provider custom-model endpoint is sufficient for the first cloud iteration if it serves our checkpoint and ordinary request contract; no special inference system is proposed.

| Route | Strength | Cost behavior | Recommendation |
|---|---|---|---|
| Local 24 GB GPU | Full control, private experimentation | Hardware, power, maintenance, and opportunity cost | Development and controlled local deployment; no automatic high-availability claim |
| SageMaker asynchronous custom container | Custom weights and scoring; queue survives zero instances | Provisioned instance time, including startup and idle cooldown, plus supporting services | First AWS candidate for delay-tolerant email processing |
| SageMaker real-time / Azure ML online custom container | Control over runtime, scores, and release versions | Warm capacity creates a cost floor | Prefer when latency or sustained utilization warrants it |
| Bedrock Custom Model Import | Less serving infrastructure to maintain | Assigned model units, running copies, five-minute billing windows | Compare with the same supported checkpoint and workload |
| Foundry managed compute | Microsoft manages serving runtime | Hourly accelerator billing | Current preview is not our production default |
| Foundry Fireworks custom import | A documented route for supported custom weights | Provisioned capacity for custom models | Not the default for sensitive financial email; examine data restrictions first |

SageMaker asynchronous endpoints can scale to zero and queue arriving requests. Configure a scale-from-zero policy; otherwise a small backlog can wait for the normal scaling threshold. This is a latency tradeoff, not free instantaneous GPU capacity. [AWS autoscaling documentation](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-autoscale.html)

SageMaker real-time inference components also have scale-to-zero support, but requests can fail while capacity starts. Do not confuse that behavior with asynchronous queueing. [AWS real-time documentation](https://docs.aws.amazon.com/en_en/sagemaker/latest/dg/endpoint-auto-scaling-zero-instances.html)

Azure ML explicitly supports custom containers with managed monitoring, scaling, and authentication. It is the clearer Azure route when we require a specific inference and calibration implementation. We still own custom image maintenance and must verify available GPU SKUs, quotas, private networking, and regional costs. [Azure ML documentation](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-deploy-custom-container?view=azureml-api-2)

### Bedrock compatibility is architecture-specific

Current Custom Model Import documentation lists `Qwen3ForCausalLM` and `Qwen3MoeForCausalLM`, not Qwen3.5's architecture. It requires compatible complete Hugging Face weights/configuration/tokenizer artifacts and currently specifies Transformers 4.51.3 and context below 128k. It also says Qwen3 does not support Converse. Thus Qwen3-8B is a reasonable import candidate, not a completed deployment validation; Qwen3.5 and newer Ministral models must not be assumed supported from their brand names. [Import requirements](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-import-model.html)

Imported models now have documented structured-output and log-probability features. The legacy completion format exposes top-one output scores; the OpenAI-compatible chat format documents top-N prompt/output scores. This is more useful than a text-only API, but we must verify that the exact endpoint exposes all scores needed for our calibration method. A top-N list may omit relevant alternatives, and requesting prompt scores has a performance cost. [Advanced import features](https://docs.aws.amazon.com/bedrock/latest/userguide/custom-model-import-advanced-features.html)

### Foundry caveats

Microsoft's current managed-compute documentation explicitly labels it preview, without an SLA, and does not recommend it for production. It describes a curated catalog and accelerator families starting at A100 80 GB, H100 80 GB, and MI300X 192 GB. This does not establish a suitable arbitrary-private-checkpoint route or economical 24 GB equivalent for our first release. [Managed compute overview](https://learn.microsoft.com/en-us/azure/foundry/concepts/managed-compute-overview)

Fireworks on Foundry does document custom-model import, including Qwen3.5-9B. However, Microsoft states that data is shared with Fireworks, the service is excluded from EU Data Boundary commitments, and it should not process payment/cardholder data. That makes it a poor default for this proposed workload regardless of its model support. This is a provider-documented limitation, not a general claim about all Azure services. [Import documentation](https://learn.microsoft.com/en-us/azure/foundry/how-to/fireworks/import-custom-models), [data handling restrictions](https://learn.microsoft.com/en-us/azure/foundry/how-to/fireworks/enable-fireworks-models)

## 9. Cost model and decision rule

Compare complete, acceptable business outcomes, not just token prices. Relevant variables include input length, reasoning/output budget, scoring passes, batching, queue delay, idle time, review coverage, retries, and deployment operations.

```text
Custom endpoint compute = provisioned instance hours × regional hourly rate
Bedrock compute = sum over copies(billed minutes × CMUs per copy × price per CMU-minute)
Warm minimum capacity ≈ 730 hours/month × minimum instances × hourly rate

Total monthly cost = compute + storage/network/monitoring + operations
                   + human review + evaluation/retraining allocation

Human review cost = messages × review fraction × minutes per review / 60 × hourly labor cost
```

Use five-minute windows for Bedrock billed minutes. The Qwen pricing table currently lists $0.05718 per CMU-minute in US East/West and $0.07144 in Frankfurt, plus $1.95 monthly storage per CMU. **The number of CMUs for our checkpoint is unknown until import**; do not substitute the published Llama example. AWS's separate cost guide has inconsistent units in its displayed formula, and the pricing page's older worked example differs from its current table. Use the actual assigned CMUs and current regional rate when quoting a budget. [Pricing table](https://aws.amazon.com/bedrock/pricing/), [CMU allocation and billing windows](https://docs.aws.amazon.com/bedrock/latest/userguide/import-model-calculate-cost.html)

For orientation only: at the listed Frankfurt rate, one CMU active for an entire five-minute window costs $0.3572; continuously active for 730 hours costs about $3,129.07 before storage. Multiply by actual CMUs and copies. These are arithmetic illustrations, not a Qwen3-8B deployment quote.

A separate hypothetical comparison: an endpoint priced at $1.50/hour costs $1,095/month if continuously warm, or $150 for 100 provisioned hours. The $1.50 rate is deliberately an assumption, not a vendor quote. Cold starts and scale-in delays count toward provisioned hours. Regional GPU quotes and workload replay must replace these assumptions before choosing a provider.

Human review can dominate: a hypothetical 50,000 messages/month, 20% reviewed, three minutes each, at $40/hour costs $20,000/month in review labor. Improving safe automation coverage can matter more than saving a fraction of a cent on inference. Do not reduce review merely to improve cost metrics.

| Workload | First deployment to evaluate | Selection criterion |
|---|---|---|
| Sparse, bursty email; minutes of delay acceptable | SageMaker async versus Bedrock import | Measured total cost, cold-start delay, scoring compatibility |
| Steady traffic or strict response deadlines | Warm SageMaker/Azure ML endpoint | Throughput at the required latency and error budget |
| Azure is already the required operational environment | Azure ML custom container | Avoid a second cloud unless measured savings justify its operational and governance cost |
| Experimental low volume | Local GPU; hosted baseline for comparison if approved | Avoid premature always-on infrastructure |

Bedrock restores inactive models on demand and can return `ModelNotReadyException`; include retry and queue behavior in the trial. [Invocation documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/invoke-imported-model.html)

The final comparison should report cost per processed message, cost per correctly automated message, review cost, and the same risk/coverage target across providers. Neither SageMaker nor Bedrock is declared universally cheapest in advance.

## 10. Production boundaries

The following are proposed engineering requirements, not a statement of regulatory certification:

- Start in shadow mode, then assist humans, then automate explicitly approved low-risk routes.
- A model decision never authenticates a sender, authorizes account access, or performs a financial action on its own.
- Keep tenant isolation, regional processing, encryption, access controls, retention, and audit permissions outside model discretion. Avoid raw financial content in general telemetry.
- Pin artifacts and dependencies; record parser, catalog, and policy versions; support rollback as a complete bundle.
- Validate all outputs. Unknown categories, unsupported languages, absent evidence, truncated threads, runtime failures, and stale calibration go to review or retry.
- Bound processing time and output length; use idempotent jobs, dead-letter handling, backpressure, and a deterministic outage fallback.
- Audit a random sample of automated decisions as well as reviewed cases. Reviewing only uncertain cases gives a biased estimate of deployed accuracy.
- Monitor changing language/category mix, evidence failures, acceptance rates, and realized errors. Material drift triggers re-evaluation or suspended automation.

Specialist review must decide applicable financial-sector and privacy obligations before real deployment. This concept does not attempt that legal assessment.

## 11. Bounded implementation sequence

These are future work packages, not work performed in this research. Effort ranges are planning estimates for experienced ML/backend staff with accessible data and domain reviewers; security approvals and annotation collection can dominate elapsed time.

| Phase | Deliverable and exit condition | Indicative effort |
|---|---|---|
| 1. Definitions and data | Written intent/priority rules, catalog contract, failure costs, adjudicated sample, leakage-safe split plan | 1–2 weeks, plus data access |
| 2. Local feasibility comparison | Untuned baselines on identical cases; standard-runtime loading; local memory/latency; ordinary-API scoring tests; GGUF conversion trial | 1–2 weeks |
| 3. Local adaptation and packaging | First SFT/QLoRA models; merged checkpoint and GGUF; vLLM/LM Studio/Ollama smoke tests; evidence and unknown-case tests; English improvement without unacceptable German regression | 2–3 weeks, partly parallel with annotation |
| 4a. Local calibration | Locked ordinary-API scoring method; calibrated thresholds; untouched audit; no cloud dependency | About 1 week after enough labels exist |
| 4b. Later cloud deployment trial | Deploy the validated checkpoint through a supported custom-model route; repeat calibration checks and measure cost/latency | About 1 week, subject to access and quotas |
| 5. Production shadow pilot | Operations, privacy/security review, failure drills, reviewer feedback, release decision | 2–4 weeks or longer depending on approvals |

A credible first experiment can fit into roughly 2–4 weeks if data is ready. A limited production pilot is more plausibly 8–12 weeks with overlapping work; enterprise release timing cannot be promised before the data and acceptance criteria exist.

Annotation is a major budget item: 10,000 threads at five minutes each require about 833 reviewer-hours for one pass, before disagreement resolution. Start smaller, measure actual review time, and label deliberately rather than generating a large unverified corpus.

Before automatic routing, require a frozen audit showing the agreed accepted-error bound and urgent-case recall, adequate coverage, acceptable slice results, supported evidence, latency/cost within budget, and tested abstention/rollback behavior. If evidence is insufficient, continue assisted operation; do not lower the standard to declare the model production-ready.

## 12. Decisions made and questions left for the experiment

The concept can proceed without further architecture clarification: multilingual foundation; English then German; current dense and mixture-of-experts candidates tested within the 24 GB inference budget; supervised adaptation; evidence records; empirical calibration; deterministic execution; unmodified local inference engines; validated checkpoint and desktop packaging; and a later custom-model cloud deployment comparison. Current regulations, product terms, and reporting figures remain versioned external evidence rather than facts to trust from model memory.

The first local experiment must determine the winning checkpoint, whether reasoning helps, the amount of labeled data required, calibration quality, runtime and conversion compatibility, quantization impact, throughput, and cost. Cloud import compatibility and economics follow that local milestone. These are empirical unknowns, not facts established by this research.

Business inputs still needed before deployment are expected message volume and burst shape, response-time requirements, catalog size, allowed regions, error costs by branch, and the availability of legally usable historical threads. They affect sizing and release thresholds, but do not block this proposed research direction.

Research completion: primary model, training, calibration, and hosting sources were reviewed; arithmetic examples were calculated; no models were trained, benchmarked, downloaded, or deployed, and no paid inference was performed.

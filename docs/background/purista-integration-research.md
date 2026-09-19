# Laya and SemIf: optional semantic classification for Harness and PURISTA

**Decision report · 19 September 2026 · Research only, not an approved implementation plan**

## Recommendation

Explore this, but start with a small evaluation and an application-level integration. Do not add either project as a dependency of PURISTA Core or Harness Core, and do not make either a default security guardrail.

The valuable capability is **turning unstructured text into a bounded decision that ordinary application code can use**. This can support routing, document processing, quality checks, and escalation without running a generative agent for every step. It complements our existing workflows; it does not require a new workflow engine, AgentBuilder, registry, or orchestration language.

Prioritize **Laya for an optional, self-hosted classification experiment**. Treat **SemIf, the project behind openjev.com, as a research reference and comparison backend**. Both need workload-specific evaluation. Neither currently establishes the accuracy, serving behavior, or calibration needed for a security boundary.

The first experiment should classify support requests and route uncertain cases to the existing structured-output agent. A second, separately evaluated experiment can apply content checks to retrieval or guardrail phases. Keep these decisions reversible, observable, and independent of business authorization.

## Scope and evidence

Reviewed public sources, downloaded source snapshots, current local implementation, relevant specifications, and handbook examples. Three independent research agents investigated Laya, SemIf, and local integration architecture; the report's central findings were checked against the cited sources.

Local implementation baselines:

| Repository | Reviewed commit |
|---|---|
| PURISTA | `0e1f6ad6ed2015745deb66f74aaf694f65a2513d` |
| Harness | `b5e09a532b81f1b9cc640cd6c0dfdf9182689f95` |

External source provenance: Laya archive root `NandhaKishorM-laya-0ff1050`, SHA-256 `b42850b8da94429d7dfdcf517a36e9d8c68188cf302d9214a6c898d7b67eeb64`; SemIf `master` archive, SHA-256 `0d273ea22e0e1d2329da8e14c70f6c6ae42889c77b63216731e09a288388da16`. The SemIf archive did not identify a commit SHA; its digest identifies the inspected snapshot. Public links can move, so pin full code and model revisions before any implementation.

**Not performed:** model downloads or inference, reproduced benchmarks, deployment/load tests, paid API calls, a complete supply-chain/legal audit, or an unrelated whole-repository review. No source code, skills, tutorials, or runtime configuration were changed. Voyage is excluded. Reported external measurements are their authors' results, not our measurements.

## What the projects actually provide

| Aspect | Laya | SemIf / openjev.com |
|---|---|---|
| Core idea | Fine-tuned encoder plus decision heads | Read allowed answer-token logits from an existing causal model |
| Output | Choice distribution, ordinal score, or boolean score | Conditional distribution over supplied choices |
| Authoring | State plus named questions and criteria | State, question, and described options |
| Runtime | Python/PyTorch library | Python research CLI and local browser demo |
| Best initial role | Small self-hosted classification backend | Experimental direct-scoring comparison, especially repeated-state tasks |
| Integration ownership | We must supply the service boundary and operations | We must supply the service boundary and operations |
| Workflow engine? | No | No |

Laya's package metadata declares version `0.2.1`, Beta status, and dependencies including PyTorch and Transformers. Its public API batches questions about one state; this should not be mistaken for a concurrent request-serving system. [Package metadata](https://github.com/NandhaKishorM/laya/blob/main/pyproject.toml), [prediction implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py#L208-L313).

SemIf's source defines the local `semif-score` CLI and a substantial Python model stack. The inspected source does not provide the production HTTP service we would need. Its native direct scorer validates single-token answer slots, rejects oversized prompts rather than truncating them, and scores only the allowed choices. [Package metadata](https://github.com/TheoLeeCJ/SemIf/blob/master/pyproject.toml), [direct scorer](https://github.com/TheoLeeCJ/SemIf/blob/master/src/semif_phase1/direct.py).

**Identity matters:** openjev.com now calls itself SemIf and explicitly disclaims affiliation with TypeSafe. It is not TypeSafe's hosted Jev product. Its browser demo downloads models of roughly 639 MB to 3.01 GB; this is a meaningful client deployment cost. [Current site](https://openjev.com/). SemIf reproduces an interface pattern, not the undisclosed Jev training method or architecture. [Method](https://github.com/TheoLeeCJ/SemIf/blob/master/docs/METHOD.md).

The inspected repository licenses are Apache-2.0 for Laya and MIT for SemIf. These do not settle every weight, dataset, or redistribution condition. SemIf explicitly lists separately governed third-party artifacts. Verify the exact selected model and dataset lineage before distributing a container or fine-tuned weights. [Laya license](https://github.com/NandhaKishorM/laya/blob/main/LICENSE), [SemIf license](https://github.com/TheoLeeCJ/SemIf/blob/master/LICENSE), [third-party inventory](https://github.com/TheoLeeCJ/SemIf/blob/master/THIRD_PARTY.md).

## Evidence that limits adoption

### Classification correctness is the main risk

Laya's English model card reports 69.8% accuracy on a held-out prompt-injection set of 116 examples. Its generic checkpoint achieves 36.2% on typed decisions, below the 46.1% majority baseline; the 76.6% specialist result follows fine-tuning on that benchmark's training split. It also acknowledges overconfidence. These figures do not establish production attack recall or performance on our applications. The 22.7% multilingual-test macro accuracy reported on the English card is for the **English checkpoint**, not the multilingual checkpoint. [Model card and limitations](https://huggingface.co/convaiinnovations/laya).

SemIf reports 81.3% balanced accuracy on authored cases but 63.7% on WANLI. Reversing options changed ten decisions across 36 base cases. Its repeated-state benchmark improves throughput but changes six of 777 choices relative to fresh scoring. The authors caution against treating the scores as calibrated confidence; their Jev comparison uses published results, not a live head-to-head. These are promising research observations, not service-level guarantees. [Results](https://github.com/TheoLeeCJ/SemIf/blob/master/docs/RESULTS.md).

Our inference: **fixed output shape prevents malformed prose; it does not prevent wrong decisions**. A valid enum can still select the wrong department, approve bad content, or discard the only relevant document. A sharply peaked distribution can be confidently wrong. Unknown languages, missing evidence, misleading instructions, and changed label wording must be evaluated explicitly.

### Input and runtime behavior need a wrapper

Laya's formatter allocates a fixed budget to instructions/options, caps option text, and takes only the beginning of the state by default. No truncation flag is returned by the prediction API. Its `confidence` for choices is based on normalized entropy, not simply the probability that the selected answer is correct. [Formatting and confidence implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/common.py#L49-L86).

This makes silent truncation unacceptable for a safety check: an attack or necessary qualification near the end can disappear. The integration must reject oversized input or use an explicitly evaluated chunking strategy. Summarizing first is not a neutral fix; the summary can omit exactly the evidence the check needs.

Laya inference is synchronous. Its loader downloads an unpinned model revision unless supplied a prepared local path, and its OOM fallback mutates the model/device. The inspected API offers no cooperative cancellation argument. [Loader and inference](https://github.com/NandhaKishorM/laya/blob/main/laya/agent.py). A TypeScript timeout can stop waiting without stopping GPU work. A production wrapper needs bounded workers, pinned artifacts, readiness checks, input limits, and a deliberate cancellation/overload policy.

SemIf's browser worker uses a separate constrained scoring path and pinned quantized artifacts. Do not assume native-model quality or native fresh-scoring behavior applies unchanged in the browser. [Browser worker](https://github.com/TheoLeeCJ/SemIf/blob/master/webgpu-demo/worker.js). Client-side decisions must remain advisory because the client is under the user's control.

## Fit with the current architecture

The existing ownership boundary is suitable: Harness owns agent/workflow execution; PURISTA owns addressed calls, identity propagation, resources, queues, events, and business guards. See the [integration specification](/Users/sebastianwessel/projekte/@purista/specs/20-agents/88-harness-first-service-integration.md:14).

| Need | Existing extension point | Consequence |
|---|---|---|
| Call a classifier from a command | Typed service resource | No Core feature needed |
| Call it from a mounted Harness workflow | Service-owned host tool | Reuse resource typing, identity context, and declared outgoing capabilities |
| Use it in native standalone Harness | Application-composed native tool | No dependency on PURISTA |
| Inspect content at agent boundaries | `defineGuardrailAction` | Optional action factory can adapt an external classifier |
| Route background work | Existing queues, commands, events, subscriptions | No second pipeline runtime |
| Preserve completed decisions on replay | Durable steps / managed tool checkpoints | Stable call IDs and separately idempotent side effects remain necessary |

Verified seams: [resource declaration](/Users/sebastianwessel/projekte/@purista/purista/packages/core/src/ServiceBuilder/ServiceBuilder.impl.ts:505), [host tool builder](/Users/sebastianwessel/projekte/@purista/purista/packages/core/src/ServiceBuilder/ServiceBuilder.impl.ts:369), [workflow context](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness/src/definitions/types.ts:689), [guardrail action definition](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness-guardrails/src/action.ts:64).

There are two important DX boundaries:

1. A portable workflow does **not** have arbitrary `context.resources`. Mounted workflows reach a service resource through a declared host tool. A standalone application composes a native tool with its application-owned client.
2. A guardrail callback does **not** receive the PURISTA command context, tenant/principal, or arbitrary resources. Its context contains protected values, correlation, cancellation/deadline, and explicitly selected object-model handles. An action factory can capture an application-supplied detector; the existing sensitive-data addon demonstrates this composition pattern. Client lifecycle and per-instance isolation then remain application responsibilities. Do not invent `ai.classifiers` or an implicit service resource binding and present it as shipped API.

Evidence: [guardrail context](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness-guardrails/src/rails.ts:83), [injected detector interface](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness-guardrails/src/sensitive-data.ts:51), [action factory](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness-guardrails/src/sensitive-data.ts:140). If reusable static definitions later require a typed runtime detector binding, treat that as a separate, explicit design decision after the experiment.

### Do not force this into ModelProvider

The current model port includes structured output, embeddings, and reranking, but no classification/logit operation. A bounded-choice backend cannot promise arbitrary JSON-schema generation. Wrapping either project as a general `object` provider would misrepresent its capabilities. [Current capabilities](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness/src/ports/model-provider.ts:10).

Use an ordinary resource/tool first. Consider a dedicated Harness classification capability only if multiple production adapters and consumers demonstrate a shared contract that benefits from automatic requirements inference, model-call accounting, and runtime binding. Such a change would require specs, contract/type tests, provider adapters, mounting/export integration, examples, CLI review, handbook, and skills—not just another union member.

## Ranked integration opportunities

Effort is relative: S = a few focused engineering days; M = roughly one to two weeks; L = several weeks or more. These are planning ranges, excluding data collection, model training, and organizational approval. Risk describes adoption risk. Confidence describes architectural fit, not unmeasured model quality.

| Priority | Optional integration | Value | Effort | Risk | Confidence | Decision |
|---|---|---|---|---|---|---|
| 1 | Support routing with explicit abstention and agent fallback | Avoid generative calls for repetitive, bounded classification | M | Medium | High fit; value unproven | Evaluate first |
| 2 | Retrieval/ingestion quality checks | Prevent unsuitable content entering the answer path | M | Medium | High fit; quality unproven | Second experiment |
| 3 | Additional semantic guardrail actions | Local content signal at existing interception points | M–L | High if enforcing | High fit; low security assurance | Shadow first |
| 4 | Batched operational review of events/agent outcomes | Find recurring failures without delaying requests | M | Low–medium | Medium | Useful alternative pilot |
| 5 | Browser-side suggestions using SemIf ideas | Local drafting/category hints | M–L | Medium | Medium | Defer unless client demand exists |

### 1. Support routing and a bounded fallback workflow

Suggested flow:

```text
authenticated request → business guard → classify a support message
  accepted category → fixed queue/handler mapping
  uncertain or unsupported input → structured-output agent or human review
  dependency failure → explicit fallback or deferred retry, never a fabricated category
```

This builds on the [current classification agent](/Users/sebastianwessel/projekte/@purista/purista/examples/banking/chapters/classification-agent/src/service/support/v1/harness/agent/classifySupportMessage/classifySupportMessageAgent.ts:9). Preserve the existing agent as the baseline and fallback. Classify department first; urgency and other tasks can be added only if their labels and evaluation are independently useful.

Application code maps a validated enum to an allowlisted command/queue. Never let returned text choose a service address, tenant, credential, tool, or model alias. Addressed service calls continue through EventBridge; durable work uses QueueBridge. Bind alternative agent targets explicitly rather than mutating a model alias inside a guardrail.

A concern: the existing agent returns a textual reason, while these backends do not generate explanations. Do not invent a reason or claim a label description explains the model's reasoning. Either make the application contract's reason a fixed policy code, or retain the agent when an actual generated explanation is required.

### 2. Retrieval and ingestion quality checks

Suggested flow:

```text
authorized document → format/size validation → quality/category check
  accepted → chunk → Harness embeddings → database/vector resource
  uncertain → review or quarantine

authorized retrieval → relevance/content checks → answer agent → output rail
```

The [existing ingestion workflow](/Users/sebastianwessel/projekte/@purista/purista/examples/banking/chapters/retrieval-ingestion/src/service/knowledge/v1/harness/workflow/ingestKnowledge/ingestKnowledgeWorkflow.ts:13) already owns embedding and storage orchestration. Add a classifier tool around that flow; do not replace Harness embedding support. Retrieval rails already have an explicit [chunk-filtering entry point](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness-guardrails/src/rails.ts:259).

Potentially useful questions include “is this a support policy?”, “is this chunk relevant to the question?”, and “does the supplied passage support this proposed claim?”. Each is a separate task with its own false-accept/false-reject cost. Compare retrieval relevance against a real reranker; do not assume a generic classifier is better. Preserve authorized source IDs for citations. Never use a relevance score to establish document ownership or permission.

Avoid immediately deleting low-scored material. Quarantine during evaluation and measure whether the check removes necessary evidence. Long documents and cross-chunk reasoning make this harder than short support routing.

### 3. Optional guardrail actions, with clear limits

An addon could expose an application-composed factory that adapts classifier results into the existing allow/block outcomes. Start with input, selected tool-output, or retrieval checks in a separate shadow evaluation path. Do not add an invented `shadow` option to the existing guardrail API.

Keep the classifier and enforcement policy separate: the backend estimates a class; deterministic application policy decides what that estimate means. Business authorization remains in PURISTA guards; tool approval remains in Harness governance. Guardrail uncertainty is not a new approval protocol. If human review is required, route through the existing interrupted/approval or workflow wait mechanisms at the appropriate orchestration boundary.

**Streaming constraint:** use the existing output rail/`beforeOutput` boundary when inspected answer content must not escape. It buffers output before release. `afterModel` alone or a Framework after-guard cannot retract streamed text. Accept the latency trade-off; do not advertise both full-answer protection and immediate uninspected token delivery. [Buffering and release](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness/src/agents/standard-loop.ts:230), [unbuffered deltas](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness/src/agents/standard-loop.ts:391).

Direct workflow tool calls do not automatically inherit an agent's guardrails or approval policy. Explicitly apply the required checks in those workflows. This is an existing ownership boundary, not an integration defect. [Execution boundaries](/Users/sebastianwessel/projekte/@purista/ai-harness/specs/01-architecture.md:126).

### 4. Asynchronous operational quality review

Subscribe to an authorized, minimized completion event and enqueue a bounded analysis job. Classify recurring answer failures, incomplete tool results, or support categories; aggregate the results for operators. This can test practical value without increasing foreground latency or allowing a wrong score to authorize an action.

Use normal command-result events for successful completion facts; manually emit events only for meaningful intermediate facts. Keep original sensitive records in application-owned storage, not the state store or telemetry. This is a framework demonstration of subscriptions, queues, resources, and metrics—not a reason to create a special AI event system.

### 5. Optional browser suggestions

SemIf suggests a local-first form assistant: propose a support category or flag a possibly incomplete request before submit. Keep it optional, lazy-loaded, and advisory. Provide a normal form when hardware, download budget, or browser support is unsuitable. Server-side validation and authorization remain authoritative. It does not replace AI Elements, standard streaming, or our existing UI protocol.

## Minimal contract direction

The following is a **proposed application contract**, not a new exported Framework/Harness API and not implementation code. Start with one task, not a general registry:

```ts
type SupportCategory = 'account_access' | 'card' | 'transfer' | 'other'

type SupportClassification =
  | {
      kind: 'classified'
      category: SupportCategory
      modelRevision: string
      taskVersion: string
      policyVersion: string
    }
  | {
      kind: 'abstained'
      reason: 'insufficient_evidence' | 'unsupported_input' | 'uncertain'
      taskVersion: string
      policyVersion: string
    }

interface SupportClassifier {
  classify(
    input: { text: string },
    execution: { signal: AbortSignal; deadline: number },
  ): Promise<SupportClassification>
}
```

The adapter validates raw labels and scores; an evaluated application policy produces `classified` or `abstained`. Transport failure, malformed output, and cancellation remain typed failures rather than becoming `other`. Keep raw conditional scores in access-controlled evaluation records when needed; avoid a universal `confidence` field whose meaning differs by backend.

Bind this as a PURISTA service resource and expose it through a host tool when a workflow needs it. For standalone Harness, use an application-owned tool factory. A future shared low-level client may support multiple named questions per state, but do not standardize that until both backends' semantics and real consumers justify it.

## Operational rules before enforcement

| Concern | Required behavior |
|---|---|
| Input budget | Token-aware validation; no silent truncation; explicit supported languages and option limits |
| Output validation | Known labels, finite values, expected cardinality; score normalization checked with rounding tolerance |
| Abstention | Explicit insufficient/other alternatives plus evaluated policy; a closed choice set alone cannot recognize unknowns |
| Deadlines | End-to-end budget includes queue and network time; linked cancellation; document whether computation continues after disconnect |
| Capacity | Bounded worker pool, queue depth, overload response, warmup/readiness; no unbounded concurrent model loading |
| Retries | Bounded transport retries only where appropriate; do not retry a semantic disagreement until a favorable answer appears |
| Tenant isolation | Propagate identity through normal Framework context; authorize before fetching data; restrict sidecar access and cross-tenant caches |
| Logs | Stable reason/task/model identifiers and aggregate metrics; no raw prompts, document text, or model responses by default |
| Reproducibility | Pin code, weights, tokenizer, prompt/label ordering, calibration policy, and serving/batch configuration |
| Replay | Persist accepted decision/version in a durable step; replay it rather than recomputing after an upgrade |
| Effects | Reauthorize current business state and use idempotent downstream operations; checkpoints do not create exactly-once effects |
| Client outcomes | Known abstention/review and controlled dependency failures; no new streaming protocol or fake generated explanation |

The replay requirement follows the current [step implementation](/Users/sebastianwessel/projekte/@purista/ai-harness/packages/harness/src/runtime/steps.ts:287): a committed result is reused, but the callback executes before its checkpoint is committed. A crash in between can repeat external work.

For required security checks, failure must not silently allow the protected action. For advisory routing, an explicit fallback can be appropriate. These are different policies and must not share an accidental default. Laya's score types and SemIf's conditional scores should not be compared against one universal threshold.

## Evaluation and optional delivery sequence

### Stage A — establish whether there is value

Estimated engineering effort: 3–5 days once a labeled dataset and target hardware are available. Dataset creation may dominate.

Compare deterministic rules, the existing Harness structured-output agent, pinned Laya, and SemIf fresh scoring. Include a small conventional classifier if the label set is stable; runtime-defined criteria are only valuable when we actually need them. Compare shared-state SemIf scoring separately, since batching can change outputs.

Use a frozen test set separate from calibration/tuning data, with ambiguous requests, unknown categories, supported languages, long inputs, suffix attacks, label-order changes, misleading instructions, and empty/malformed inputs. Split by source/customer/document where appropriate to avoid near-duplicate leakage.

Measure per-class precision/recall, accepted-decision error rate, abstention coverage, false accepts/rejects, and calibration where meaningful. Accuracy alone can hide rare dangerous failures. Include uncertainty intervals; zero observed failures in a small sample is not proof of safety.

Measure cold start, warm p50/p95/p99 latency, memory, throughput under concurrency, timeout/overload behavior, and cost per **correctly resolved request**, including fallback and human review. Compare full service paths on the deployment hardware rather than headline forward-pass timings.

Before running, record task-specific acceptance thresholds and acceptable trade-offs. Stop if the candidate fails the required quality bound or offers no material cost/latency/privacy benefit over the simpler baseline. This report intentionally does not invent a universal safe probability cutoff.

### Stage B — one optional end-to-end reference

Conditional on Stage A: roughly 5–10 engineering days for a bounded prototype, not a production-support commitment.

Build one PURISTA classifier resource and command, one host tool/workflow, and a standalone Harness consumer using the same client boundary. Provide a separate Python service/container, pinned weights, local Compose setup, and deterministic fake backend for tests without GPU or network. Verify normal invocation, tenant propagation, cancellation, overload, fallback, replay, and duplicate delivery.

Initially keep real classifications advisory. Document the npm-facing TypeScript setup and the separately deployed model service honestly. Do not introduce package-copy instructions, a mandatory GPU dependency in the beginner tutorial, or changes to starter/CLI before the pattern proves useful.

### Stage C — choose what deserves maintenance

After evidence and an explicit owner decision, choose one of:

1. Keep the integration as an optional application example: lowest maintenance, adequate if only one workload benefits.
2. Publish an optional client/action addon with contract tests: justified by repeated use and a supported serving contract.
3. Propose a native Harness classification capability: only if automatic binding, inference, metering, and multiple backend contracts clearly outperform ordinary tools/resources.

If promoted, update the relevant specs, public API documentation, handbook, examples, tutorials, and canonical skills. Add CLI/template support only where it improves a common task. Run applicable Harness type/contract/integration/failure tests and PURISTA unit tests; run `npm run audit:skills` and `npm run audit:knowledge` when changing shared guidance. These are future implementation gates, not tests claimed as executed by this research.

## Alternatives considered and rejected for now

| Option | Why not now |
|---|---|
| Default-on Laya prompt-injection guard | Present evidence does not establish the required security performance; truncation adds risk |
| SemIf as a production dependency | We would take on serving, versioning, operational, and quality commitments not established by the research project |
| A generic `object` model adapter | Bounded-choice scoring is not arbitrary structured generation |
| A new semantic workflow DSL | Existing Harness workflows and PURISTA primitives already provide orchestration |
| MCP as the primary internal transport | Adds discovery/tool plumbing to a fixed typed call; consider it only for external MCP consumers |
| Reusing the sensitive-data detector interface | That contract requires entity spans/offsets, not classification choices |
| Using scores for authorization, automatic money movement, or destructive approval | A probabilistic content estimate is not permission or verified business state |
| Browser results as trusted server decisions | Client-controlled output cannot establish an authoritative decision |
| Building a calibration registry/control plane immediately | Premature scope; task configuration and versioned evaluation records are enough for the experiment |
| Assuming generation-free means cheaper | GPU utilization, cold starts, fallbacks, and maintenance can erase inference savings |

A legitimate outcome is **no integration**: retain deterministic rules and the current structured-output agents if the experiments do not show enough benefit. The recommended first investment is evidence, not a new public API.

## Decision requested

Approve only Stage A if this direction is useful: a support-routing evaluation, with Laya as the primary experimental backend and SemIf as a research comparator. Keep guardrail enforcement and new public abstractions out of that scope. Use the resulting quality, latency, and operating-cost evidence to decide whether Stage B is worth building.

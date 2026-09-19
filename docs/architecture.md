# Architecture proposal

Date: 2026-09-19. Status: proposed implementation design. The user's approved direction is the separation of model building, customization, serving, and a configurable modular workflow service. Language choice and the YAML syntax below remain recommendations, not implemented public APIs.

## 1. Four independent concerns

1. **Shared model development:** select an upstream checkpoint, perform shared financial-domain/task adaptation where justified, evaluate, and publish a versioned Foliqant model. This is what `model/base/` means; it is not foundation-model pretraining from scratch.
2. **Customization:** adapt a particular released Foliqant model for an organization/domain using separately authorized data. Record the parent model and evaluate shared capability retention. Configuration and retrieval should be tried before training.
3. **Inference:** load a released model in an ordinary supported server. Expose its standard HTTP API. No custom inference engine, heads, or runtime forks.
4. **Workflow service:** load a validated process definition, ask the model bounded questions, validate answers and evidence, apply deterministic decisions, and deliver outcomes through adapters. The service must not import the training environment or load model weights.

See [local training and model lineage](local-training-and-model-lineage.md) for the M1/M5 development profiles and shared-to-customer adaptation chain.

Each release should identify its upstream/model revision, tokenizer/template, adaptation lineage, data-manifest references, evaluation/calibration profile, and export/quantization details. Store large artifacts outside Git. A new model or quantization does not inherit an earlier model's calibration claim.

## 2. Language recommendation

Use **Python for model development/evaluation** and **strict TypeScript on Node.js LTS for the service**. This is an engineering recommendation based on the existing team's TypeScript experience and the service's integration-heavy workload, not a benchmark result.

| Choice | Strength | Tradeoff for this project |
|---|---|---|
| Python service | One language across training and orchestration; mature ML tooling | Typed Python is viable, but gradual typing needs enforcement. Keep GPU libraries out of the service environment regardless. |
| TypeScript + Node.js | Typed adapter authoring, async I/O, familiar ecosystem, straightforward JSON/YAML contracts | Types disappear at runtime; external configuration and model output still require schema validation. A Node runtime is part of deployment. |
| TypeScript + Bun | Similar authoring experience, standalone executable packaging | The executable includes Bun; it is not runtime-free. Qualify adapter, streaming, tracing, TLS, and shutdown behavior before adopting it. |
| Go | Compiled interfaces, efficient concurrency, a compact single-binary deployment path | Attractive if deployment footprint is the dominant requirement. Runtime-configured workflows still need validation; dynamic shared-library plugins introduce compatibility constraints. |

Choose Go instead if a small self-contained service binary or a firm memory budget is a hard requirement. With a remote model endpoint, model latency and execution policy are likely to matter more than the host language's raw throughput; measure before optimizing. Do not maintain Node and Go implementations in parallel.

Node 24 is currently LTS; use a supported patched LTS release when implementation begins. Separate Python and service dependency locks/images. For Python, use type checking and validation rather than treating the language as inherently untyped. No dependencies or runtime versions are installed by this scaffold.

Primary sources: [Node releases](https://nodejs.org/en/about/previous-releases), [TypeScript runtime/type distinction](https://www.typescriptlang.org/docs/handbook/2/classes), [Bun executable packaging](https://bun.com/docs/bundler/executables), [Go plugin limitations](https://pkg.go.dev/plugin).

## 3. Core and adapters

```text
HTTP / Redis Streams / future input adapter
  -> authenticated request envelope + validated workflow input
  -> workflow core
       -> model port -> configurable inference endpoint
       -> validated answers -> deterministic decision rules
       -> execution store / delivery ports when durable execution is enabled
  -> HTTP response / Redis Streams / future output adapter
```

Start with a small set of interfaces: model access, execution storage, output delivery, and registered step handlers. Input transports call the core; the core must not import Hono, Redis clients, framework request objects, or provider SDK types. Give the core explicit dependencies, deadlines, cancellation, and trusted execution context. Keep transport-specific acknowledgment and retry details in the adapters.

Use an explicit, typed registry of trusted adapters assembled by the application. Configuration selects a registered adapter ID and supplies validated options. It must not install packages, load arbitrary module paths/URLs, execute shell commands, or evaluate code. In-process adapters are trusted application code, not a security sandbox. Untrusted customization would require a separately designed isolation boundary.

An OpenAI-compatible model adapter is a good first implementation, but compatible endpoints differ in JSON-schema support, streaming, log-probabilities, reasoning settings, and tool calling. Declare required capabilities per workflow and reject unsupported combinations at startup. Use the actual inference endpoint; `/v1/models` is discovery, not an inference call. [vLLM serving documentation](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)

## 4. Configuration boundary

Keep two separate artifacts:

- **Workflow bundle:** version, input/output contracts, prompt files, bounded model tasks, deterministic route rules, and terminal outcomes. No secrets, ports, Redis connection strings, or deployment-specific model IDs.
- **Service configuration:** endpoint aliases, adapter bindings, credentials by environment reference, resource limits, and enabled workflow bundles. The alias names are user-defined; no reserved primary/default alias is required.

YAML is an authoring format, not an untyped escape hatch. Parse using a safe YAML subset, reject duplicate keys/custom tags, limit size/depth/alias expansion, validate against versioned JSON Schema, and perform semantic checks before activation. File references must remain inside the approved bundle/config roots after canonicalization, including symlinks. Environment substitution is restricted to explicit secret/connection-reference fields, not arbitrary recursive text expansion.

Start with a small bounded graph: `model`, `decision`, and `finish` nodes. Custom behavior can use reviewed registered step handlers later. Avoid arbitrary expressions, scripting, unbounded loops, and a full BPMN engine in the first release. Decision conditions use a small set of typed operators against explicit JSON Pointer paths. The first matching case wins; every decision needs a default outcome. Require acyclic graphs initially, all nodes reachable, all paths terminal, unique IDs, compatible data references, and bounded execution budgets.

The included YAML is an illustrative proposal only. Prompts are separate Markdown files; request text is serialized as untrusted user data, not interpolated into privileged instructions. A `model` node receives the mapped input as one user data message plus the referenced instruction document. A `decision` reads only validated prior outputs. A `finish` returns the explicitly mapped output.

Schemas validate structure, not truth. Verify evidence spans against supplied messages/documents and check allowed catalog IDs separately. A schema-valid but unsupported decision must not be treated as safe merely because it is well-formed.

## 5. Execution and reliability requirements

These are implementation requirements, not capabilities of this scaffold:

- Propagate a run ID, correlation ID, trusted tenant/principal context, workflow revision, and deadline independently from untrusted input. Never trust a tenant ID merely because it appears in a payload.
- Keep proposed business destinations as allowlisted route values. The model cannot choose arbitrary endpoints or execute financial actions.
- Separate business `needs-review` outcomes from malformed output, timeouts, endpoint errors, and delivery failures. Invalid model output must not silently become a successful route. Retries are bounded and apply only to classified retryable failures.
- Enforce request size, concurrency, token, step, and wall-clock budgets. Cancellation is best effort after an external call; it is not rollback.
- HTTP's first slice may be synchronous and explicitly non-durable. Async HTTP or Redis delivery requires a durable execution design before release.
- Redis processing is at-least-once. Deduplicate using a tenant-scoped request key plus workflow revision; reject mismatched payload reuse. Define consumer recovery, pending-entry reclaim, retry limits, and dead-letter handling. Acknowledge only after durable outcome/delivery intent is recorded.
- Persist a terminal result and pending delivery together when possible; deliver via an outbox, then mark delivery. If a crash causes repeated publication, sinks must deduplicate. Do not promise exactly-once execution across independent systems.
- Freeze the workflow bundle revision for a run, record validated node outputs, and define resume semantics before enabling retries after process crashes. Do not silently rerun side-effecting handlers.
- Default logs contain identifiers and redacted diagnostics, not email bodies, prompts, retrieved documents, secrets, or full model output. Sensitive trace retention requires explicit configuration and access control.
- The final decision is structured. Future progress/streaming must distinguish provisional updates from the validated terminal outcome; a token stream is not authorization to route.

Storage selection, a complete workflow schema, approval/resume protocols, and production transport configuration are still open implementation decisions. They must be resolved before claiming enterprise readiness.

## 6. Incremental implementation

1. Define the versioned bundle schema and compile validated configuration into an immutable execution plan. Test rejection of malformed graphs and unsupported capabilities.
2. Implement the core against a fake model adapter; validate schemas, evidence, defaults, error paths, cancellation, and budgets.
3. Add one real model endpoint adapter and a local HTTP binding. Publish precise capability/compatibility tests.
4. Select durable storage and implement request deduplication, checkpoint/recovery semantics, and outbox delivery.
5. Add Redis Streams ingress/egress and crash/redelivery tests. Reuse the same workflow fixtures across both transports.
6. Add extension points only where a second implementation demonstrates a need. Train/customize models in a separate workstream once evaluation/data rights are ready.

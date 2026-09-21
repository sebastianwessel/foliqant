# Earlier architecture proposal

Superseded for python-package language, envelopes, authoring and provider/MCP
integration by the [2026-09-21 modular service proposal](modular-workflow-service.md).
This file retains the earlier rationale and example history; its Go and YAML
recommendations are not the current proposed implementation direction. The model
lifecycle remains separate and unchanged.

Date: 2026-09-19. Status: proposed implementation design. The user's approved direction is the separation of model building, customization, serving, and a configurable modular workflow service. Language choice and the YAML syntax below remain recommendations, not implemented public APIs.

## 1. Four independent concerns

1. **Shared model development:** select an upstream checkpoint, perform shared financial-domain/task adaptation where justified, evaluate, and publish a versioned Foliqant model. This is the shared-adaptation stage in the model-development guide; it is not foundation-model pretraining from scratch.
2. **Customization:** adapt a particular released Foliqant model for an organization/domain using separately authorized data. Record the parent model and evaluate shared capability retention. Configuration and retrieval should be tried before training.
3. **Inference:** load a released model in an ordinary supported server. Expose its standard HTTP API. No custom inference engine, heads, or runtime forks.
4. **Workflow service:** load a validated process definition, ask the model bounded questions, validate answers and evidence, apply deterministic decisions, and deliver outcomes through adapters. The service must not import the training environment or load model weights.

See [local training and model lineage](apple-silicon.md) for the M1/M5 development profiles and shared-to-customer adaptation chain.

Each release should identify its upstream/model revision, tokenizer/template, adaptation lineage, data-manifest references, evaluation/calibration profile, and export/quantization details. Store large artifacts outside Git. A new model or quantization does not inherit an earlier model's calibration claim.

## 2. Language recommendation

Use **Python for model development/evaluation** and recommend **Go for the production workflow service**. This recommendation reflects the requested small deployment, strong interfaces, efficient concurrency, and independent adapters. It is not a benchmark result or authorization to implement the service in the current model-lifecycle workstream. Do not maintain parallel Go and Node implementations.

| Concern | Recommended implementation | Boundary |
|---|---|---|
| Training, customization, evaluation, export | Python with an isolated locked environment | Produces versioned model artifacts; never imported by the workflow service. |
| Workflow execution and deterministic decisions | Go core library | Standard-library types and explicit interfaces; no transport, storage client, inference SDK, or telemetry SDK imports. |
| HTTP, Redis, model endpoint, persistence, telemetry | Go adapter packages | Compiled into the application and assembled by its startup code. |
| Model inference | A supported standard inference server | Separate lifecycle and deployment; reached through an endpoint adapter. |

Recommend one Go process in one application container initially, containing both the adapter layer and core library. These are independently testable modules, not necessarily independent services. Direct typed calls avoid an internal network hop and protocol. If independent scaling or isolation later requires separate gateway and worker processes, prefer separate containers using the same codebase; specify the delivery protocol before implementing that topology. Two processes inside one container remain possible, but require explicit supervision and share a container failure and deployment boundary. They do not by themselves provide fault tolerance.

The Go executable includes its runtime. A self-contained Linux binary requires compatible dependencies/build settings; TLS trust roots and configuration still need to be supplied. No concrete image-size, memory, or throughput claims are accepted without measurement. Endpoint latency, queueing, batching and concurrency limits will probably matter more than language throughput; benchmark before optimization.

Adapters use ordinary Go interfaces and explicit build-time registration. Avoid Go shared-library plugins as the default extension mechanism because of their platform and toolchain compatibility constraints. Workflow YAML selects registered capabilities and validated options, not arbitrary code.

Primary sources: [Go executable/runtime behavior](https://go.dev/doc/faq#Why_is_my_trivial_program_such_a_large_binary), [Go plugin limitations](https://pkg.go.dev/plugin), [Docker process lifecycle](https://docs.docker.com/engine/containers/multi-service_container/).

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

### Observability from the first implementation

Pass `context.Context` through every core operation and port for cancellation, deadlines and trace propagation. To keep the core free of external dependencies, expose small typed lifecycle observation hooks at meaningful workflow/step boundaries; the OpenTelemetry adapter implements these hooks and wraps model/storage calls. Do not build a second generic telemetry API or expose SDK-specific types through the core interface.

Application startup owns OpenTelemetry SDK/exporter setup and shutdown. Use standard propagation and OTLP export, with bounded buffering and export timeouts. Trace acceptance, workflow steps, model calls, persistence and delivery; correlate structured logs and expose latency, failures, queue age and in-flight counts. Async processing must persist safe trace context and connect processing spans across retries. Do not use trace baggage as authenticated tenant/principal identity. Avoid customer content, credentials and unbounded identifiers in metric labels. Operational telemetry is separate from any required durable business audit trail.

OpenTelemetry Go currently marks traces and metrics stable and logs release candidate. Keep the log bridge isolated and choose pinned versions when implementing the service. Telemetry delivery failures must not block business processing. [OpenTelemetry Go status](https://opentelemetry.io/docs/languages/go/)

### Durability is separate from module separation

For durable acceptance, persist the request before acknowledging it, use bounded workers with recoverable claims/leases, checkpoint defined execution boundaries, and persist terminal outcome plus delivery intent atomically where possible. Use bounded retries, deduplication and an outbox. In-memory channels only schedule work; they do not survive process termination. External model calls may repeat after a crash, even when completed business results are deduplicated. Do not claim exactly-once end-to-end execution.

The durable database or broker, acknowledgment boundary, lease/fencing rules and recovery tests remain future service-design decisions. One application container may still require external durable infrastructure. This proposal does not add those dependencies to model training.

## 4. Configuration boundary

The [business-process target specification](../10-business-decisions-and-processes.md)
extends this proposal with one parent process and multiple configured child
tasks/subflows. The small single-route example below remains illustrative; it
does not implement or demonstrate fan-out/join. The model never generates the
execution graph. A configured dispatcher maps validated request instances to
allowlisted subflow definitions, with a persisted complete child set, explicit
dependency/concurrency limits, an all-required-children join and crash/retry
semantics. Conditional alternatives are not parallel confirmed requests. A later
email must not recreate completed child work. Wire syntax, durable storage and
reconciliation rules require a separate implementation review.

Keep two separate artifacts:

- **Workflow bundle:** version, input/output contracts, prompt files, bounded model tasks, deterministic route rules, and terminal outcomes. No secrets, ports, Redis connection strings, or deployment-specific model IDs.
- **Service configuration:** endpoint aliases, adapter bindings, credentials by environment reference, resource limits, and enabled workflow bundles. The alias names are user-defined; no reserved primary/default alias is required.

YAML is an authoring format, not an untyped escape hatch. Parse using a safe YAML subset, reject duplicate keys/custom tags, limit size/depth/alias expansion, validate against versioned JSON Schema, and perform semantic checks before activation. File references must remain inside the approved bundle/config roots after canonicalization, including symlinks. Environment substitution is restricted to explicit secret/connection-reference fields, not arbitrary recursive text expansion.

The illustrative single-route slice uses `model`, `decision`, and `finish` nodes. Custom behavior can use reviewed registered step handlers later. Avoid arbitrary expressions, scripting, unbounded loops, and a full BPMN engine in the first release. Decision conditions use a small set of typed operators against explicit JSON Pointer paths. The first matching case wins within an exclusive decision; every decision needs a default outcome. That rule must not discard secondary request instances when implementing specification 10's separate bounded fan-out/join contract. Require acyclic graphs initially, all nodes reachable, all paths terminal, unique IDs, compatible data references, and bounded execution budgets.

The included YAML is an illustrative proposal only. Prompts are separate Markdown files; request text is serialized as untrusted user data, not interpolated into privileged instructions. A `model` node receives the mapped input as one user data message plus the referenced instruction document. A `decision` reads only validated prior outputs. A `finish` returns the explicitly mapped output.

Schemas validate structure, not truth. Verify evidence spans against supplied messages/documents and check allowed catalog IDs separately. A schema-valid but unsupported decision must not be treated as safe merely because it is well-formed.

### Input answerability and complete answers

Use the [input-answerability proposal](input-answerability-and-reliability.md) when defining model nodes. Each question declares its scope, cardinality, options or rubric, permitted evidence, and required facts. Separate predicted input sufficiency from the adequacy of the returned answer and from permission to execute an action. A concentrated label distribution does not establish any of these by itself.

Preserve all active request units, including multiple requests in the same category and their conditions, order and dependencies. Several clear requests call for decomposition, while one ambiguous request calls for clarification. Retrieve missing evidence only when its source is authorized, available, and policy permits retrieval; sufficient evidence with solver uncertainty may justify bounded reasoning or review. Do not repeatedly ask the model to reason about a fact that is absent. Partial execution requires an explicit policy establishing that resolved units can proceed independently. Mutually exclusive branches are not independent actions.

The solver provides proposed answers, cited evidence, explanations, and issue candidates. The input assessor owns predicted answerability; the answer assessor evaluates adequacy. These roles may share a checkpoint but retain distinct inputs and profiles. A declared check/review policy handles disagreements; predictions cannot override deterministic contract failures. Application code validates observable facts, attaches separately validated calibration metadata, and applies deterministic disposition rules. Unqualified numeric estimates remain null. An honest unknown can be an adequate answer even when the input cannot support a substantive choice. Business information gaps are distinct from technical endpoint failures; neither should silently become a successful automatic route.

No assessor, clarification loop, or new workflow node is implemented by this research update. Their versioned contracts and bounded execution behavior must be specified before runtime work.

## 5. Execution and reliability requirements

These are implementation requirements, not capabilities of this scaffold:

- Propagate a run ID, correlation ID, trusted tenant/principal context, workflow revision, and deadline independently from untrusted input. Never trust a tenant ID merely because it appears in a payload.
- Keep proposed business destinations as allowlisted route values. The model cannot choose arbitrary endpoints or execute financial actions.
- Separate business `needs-review` outcomes from malformed output, timeouts, endpoint errors, and delivery failures. Invalid model output must not silently become a successful route. Retries are bounded and apply only to classified retryable failures.
- Enforce request size, concurrency, token, step, and wall-clock budgets. Cancellation is best effort after an external call; it is not rollback.
- HTTP's first slice may be synchronous and explicitly non-durable. Async HTTP or Redis delivery requires a durable execution design before release.
- Redis processing is at-least-once. Deduplicate delivery using a tenant-scoped request key and reject mismatched payload reuse; record the selected workflow revision separately. Business action identities remain stable across plan revisions and reconciliation checks the parent action history, so a revision change cannot repeat completed work. Define consumer recovery, pending-entry reclaim, retry limits, and dead-letter handling. Acknowledge only after durable outcome/delivery intent is recorded.
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

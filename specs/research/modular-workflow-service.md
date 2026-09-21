# Modular workflow service proposal

Date: 2026-09-21. Status: design rationale, superseded for implementation by
[specification 11](../11-python-package.md). The owner selected Python and
PydanticAI; this document is not a competing runtime contract. This replaces the earlier service
language and authoring recommendation in [the architecture proposal](workflow-service-proposal.md).
No model-generation code, environment, active run, provider configuration or
training contract changes are part of this proposal.

## Recommendation and scope

Build a small Python workflow engine, a strict configuration compiler, and
separate transport, model, MCP, persistence and telemetry adapters. Start with
one process and direct typed calls between modules. A second internal service
would add a protocol, deployments and failure modes without establishing
durability. Keep training and model serving as separate installations/processes.

Use YAML for configuration and Markdown with YAML frontmatter for prompt-bearing
steps. The application is a directory; step files have stable names and stay
next to their schemas and tests. Take the colocation and progressive configuration
ideas from [Next.js](https://nextjs.org/docs/app/getting-started/project-structure)
and [Eve](https://vercel.com/eve). Do not copy an autonomous agent's responsibility
for selecting the business process: models provide observations; configured
rules own transitions and authorization. Eve's default sandbox and implicit
tool discovery are not defaults for this service.

| Choice | Assessment |
|---|---|
| Python | Recommended first implementation: existing expertise, schema tooling and model/MCP ecosystem; strict typing and isolation still require discipline. Endpoint latency should be measured before optimizing language overhead. |
| Go | Strong alternative when measured CPU/memory or packaging constraints dominate; not necessary to gain modularity. Avoid a simultaneous second implementation. |
| TypeScript/Node | Strong option for an existing TypeScript application and AI SDK integration; no inherent advantage for this team's current Python-first lifecycle. Bun is not required. |

Use a separate service environment and dependency lock, initially the repository's
supported CPython baseline. The service must not import MLX, Transformers, model
training code or the curation runner. Reuse versioned decision JSON Schemas and
their semantics; do not duplicate task enums or create another classifier taxonomy.

## Core, ports and ownership

```text
HTTP / Redis / CLI / embedded caller
        -> input adapter: decode, authenticate, validate, extract trace context
             -> acknowledge acceptance after the durable commit, when applicable
        -> Engine.run(workflow, envelope, trusted_context)
             -> named steps and deterministic routing
             -> model port / MCP port / registered handler / execution store
        -> complete execution result
        -> output adapter: encode, deliver, record delivery outcome
```

Core types and ports use typed dataclasses, enums and
[Python Protocols](https://typing.python.org/en/latest/spec/protocol.html).
The compiler and boundary validators may use Pydantic strict validation and
JSON Schema. HTTP, Redis, provider SDK and OTel SDK imports stay in adapters.
Do not recreate a general validation library to claim that the entire package
has zero dependencies. Core execution can remain standard-library-only while
its validators and I/O implementations are injected.

Proposed module layout, not new executable packages:

```text
src/foliqant/
  core/             execution, routing, bounded fan-out, lifecycle
  contracts/        envelopes, plans, results, errors, protocol types
  compiler/         frontmatter/YAML loading, schemas, graph validation
  ports/            model, tools, store, observation, registered handlers
  adapters/
    models/         OpenAI, Anthropic, Foundry, Bedrock
    mcp/            host-owned MCP client
    transports/    HTTP, Redis, CLI
    storage/       memory development store, durable store
    telemetry/     OTel implementation and privacy filtering
  settings.py       typed configuration and documented defaults
  constants.py      shared protocol limits and canonical enum/version values
  bootstrap.py      explicit adapter registration and lifecycle
```

Adapter packages are installed ahead of time and explicitly registered at startup;
configuration selects trusted IDs. It does not load arbitrary modules or fetch
executable plugins. Python entry points are an optional discovery mechanism for
installed trusted extensions, not automatic execution of every installed plugin.
[Packaging guidance](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)

Ports are asynchronous; provider adapters with blocking SDKs must use bounded
workers rather than block the event loop. Cancellation is cooperative and does
not undo a request already sent to another system.

Static typing covers core interfaces and Python extensions. Arbitrary runtime
YAML cannot acquire compile-time Python types by assertion: compiler validation,
JSON Schema and checked result accessors cover the dynamic authoring boundary.

## Envelope and results

Input has exactly the user's two business fields:

```json
{
  "payload": {"message": "I cannot access my account."},
  "metadata": {"correlation_id": "case-123"}
}
```

`payload` is JSON data constrained by the selected workflow's input schema;
`metadata` is a bounded JSON object. Preserve both by default. Never merge model
output into either field implicitly. Optional final payload projection is
explicit workflow configuration. Metadata is immutable throughout execution,
including to custom handlers. Preservation means JSON-value equality, not the
original HTTP byte formatting.

Return `payload`, `metadata`, and `decisions` keyed by step ID. Recommend one
additional `execution` field for run identity, workflow revision, lifecycle
status and usage. This avoids modifying user metadata or disguising a failed
or incomplete process as a successful decision. Lifecycle statuses are
`completed`, `needs_review`, `failed`, and `cancelled`; technical failure is not
an input-answerability issue. Invalid input before execution returns a typed
boundary error rather than pretending a workflow ran.

Each `decisions[step_id]` has `status` and a typed `result`, or a safe structured
error. Non-selected steps are `skipped`, with no invented result. A completed
decision contains the existing native decision result, including answerability,
answer and evidence-backed explanation. For a one-question decision step,
unwrap the validated single result from `DecisionOutput`; its question ID is the
step ID. Preserve the complete native wrapper for explicit multi-question steps.
LLM steps hold validated text/JSON and MCP steps hold validated tool results.
`decisions` therefore means the workflow's named step results, not just classifiers.
It could be called `steps` before freezing the API, but there should not be two
parallel collections containing the same values.

For example, routing can read
`/decisions/initial_triage/result/answer/optionId` without parsing an explanation.
Model/provider diagnostics and raw request traces do not belong in this result.
Consumers get measured usage where available; a missing count is null/unavailable,
never an invented zero. No self-reported model confidence becomes a calibrated
probability.

An embedded application calls the same core as a transport adapter. Illustrative
typed call; no HTTP server or Redis client is required:

```python
result = await engine.run(
    workflow="inbox",
    envelope=Envelope(payload={"message": message}, metadata=metadata),
    context=trusted_context,
)
```

Keep trusted authentication, tenant/principal identity, deadlines and cancellation
in a separate `RunContext` supplied by the adapter. Caller metadata cannot grant
permissions, choose a provider, turn on debug, or change workflow configuration.
Pass only explicitly selected metadata into prompts/tools. By default, model
steps receive payload inputs, not the metadata object.

## Authoring: small first, explicit as it grows

All syntax and CLI examples below are proposals, not currently runnable commands.

```text
foliqant.yaml
.env                         # local secrets, ignored by Git
workflows/inbox/
  workflow.yaml
  steps/
    initial_triage.md
    detect_priority.md
    lookup_information.md
    done.yaml
    review.yaml
  schemas/answer.schema.json
  tests/                     # synthetic examples, never customer mail
```

The file stem is the step ID unless frontmatter supplies an explicit `name`.
Use stable snake_case names, reject duplicate names, and never infer execution
order from filenames. An explicit name permits moving a file without changing
its identity. Renaming an ID creates a new workflow revision. No automatic
filesystem discovery exposes a network endpoint.

`workflow.yaml`:

```yaml
version: 1
name: inbox
start: initial_triage
defaults:
  model: local
```

`steps/initial_triage.md`:

```markdown
---
type: decision
sources:
  message: /payload/message
question:
  type: choice
  options:
    incident: An existing service is broken or unavailable.
    information_request: The sender asks for facts or instructions, without reporting a failure.
    confirmation: The sender only confirms a prior decision or completed action.
    other: An explicit request outside the categories above.
on_answer:
  incident: detect_priority
  information_request: lookup_information
  confirmation: done
  other: review
on_unresolved: review
---
Identify the current request from the supplied message.
Choose one category only when the evidence supports exactly one.
Do not use other for missing information or ambiguous alternatives.
```

The compiler expands option descriptions, source IDs, the question ID, the
existing answerability contract and response schema. The body supplies the
question prompt, not a second serialized model contract. Source content is
separate untrusted input, never interpolated into privileged Markdown. Source
bindings for decision steps must resolve to supported text/source structures;
the compiler does not silently stringify arbitrary objects as cited text.

`detect_priority.md` uses the same decision type with an `ordinal` question and
explicit `levels` plus criteria. Its body must provide the actual business rubric;
the system must not invent urgency from a generic prompt. It can use `next: done`
for answerable results and `on_unresolved: review`. `on_answer` and `next` are
mutually exclusive. Unsupported or partial single-choice results do not enter
an automatic route. With no explicit unresolved target, execution ends in
`needs_review`; an unrouted completed step terminates successfully.

Terminal files are small YAML steps:

```yaml
# steps/review.yaml; done.yaml uses outcome: completed
type: finish
outcome: needs_review
```

For ordinary generation, `lookup_information.md` can use:

```markdown
---
type: llm
model: assistant
input:
  message: /payload/message
tools:
  server: policies
  allow: [search_policy]
  choice: required
output:
  schema: ../schemas/answer.schema.json
next: done
---
Find the applicable policy using the supplied tool, then answer the message.
Base the answer on returned evidence. State when that evidence is insufficient.
```

Use `output: text` for text instead of a JSON Schema reference. Without tools this
is the same LLM step, not a second near-identical implementation. A typed tool
success does not establish that its contents support the answer; business
validation still applies. The schema must allow an honest insufficient-evidence
answer rather than forcing a fabricated response.

Paths resolve relative to the declaring file inside the approved workflow root;
reject escapes, symlink escapes, unsafe YAML features, duplicate keys and remote
schema loading. JSON Pointer selects data; no JavaScript/Python eval or full
template language is needed. Metadata selection, when required, is explicit.

Use a direct `type: mcp` step when the exact tool and arguments are already known:

```yaml
type: mcp
server: policies
tool: get_policy
arguments:
  policy_id:
    from: /payload/policy_id
next: done
```

No LLM call is needed to execute a deterministic lookup. The host validates
arguments and structured tool output against their declared schemas. A tool
returning only text is not silently represented as a validated JSON object.

Add `type: handler` only for trusted registered custom functions. For multiple
confirmed request instances, add a bounded `dispatch` over validated request
units with an allowlisted category-to-subworkflow mapping and explicit join.
Keep child results under item IDs; do not overwrite one result when two requests
share a category. Ambiguous alternatives never become parallel side effects.
This must retain the [existing branching semantics](../10-business-decisions-and-processes.md),
including partial-answer holds, authorization per child and withdrawal handling.

## Provider configuration and native options

Model names and settings live in deployment profiles, not prompt files. Aliases
are user-defined; workflow `defaults.model` supplies an explicit default.
There is no reserved default model name or silent paid-cloud fallback.

```yaml
version: 1
mode: production
models:
  local:
    provider: openai_compatible
    api: chat_completions
    base_url: ${LOCAL_MODEL_BASE_URL}
    model: ${LOCAL_MODEL_ID}
    options:
      reasoning_effort: low
      temperature: 0.1
      max_tokens: 8192
  assistant:
    provider: openai
    api: responses
    api_key: ${OPENAI_API_KEY}
    model: ${OPENAI_MODEL_ID}
    options:
      reasoning: {effort: low}
      max_output_tokens: 2048
mcp:
  policies:
    transport: streamable_http
    url: ${POLICY_MCP_URL}
telemetry:
  otlp_endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
limits:
  ai_request_timeout_sec: 60
  mcp_request_timeout_sec: 30
  run_timeout_sec: 300
  max_steps_per_run: 32
  max_ai_calls_per_step: 4
  max_tool_calls_per_step: 3
```

These parameter selections assume the chosen models support them; they are not
universal defaults. In particular, temperature and reasoning settings are not
interchangeable across providers or models. Timeout/budget numbers are proposed
starter bounds, not measured latency or production SLAs. Model-native parameter
names remain exactly native even when they use different naming conventions.

Distinguish provider/authentication from API dialect. The proposed coverage is:

| Provider/API | Adapter responsibility |
|---|---|
| [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) | Client tools; structured output through `response_format`; model-dependent `max_completion_tokens`, `reasoning_effort` and temperature. |
| [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create) | Client tools or separately governed hosted tools; structured output through `text.format`; `max_output_tokens` and nested `reasoning`. |
| [Anthropic Messages](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) | Native tool-choice mapping including `any`; `output_config.format` and strict tool schemas; Anthropic-native options rather than an OpenAI parameter rewrite. |
| [Azure OpenAI](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs) / [Foundry project Responses](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/responses-api) | Explicit API flavor, deployment, authentication and supported region/model; managed identity support belongs in the adapter. |
| [AWS Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) | AWS credential chain and region; `inferenceConfig` plus `additionalModelRequestFields`; model-specific forced-tool modes and [structured-output support](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html). |

Foundry is not one uniform API for every model. Legacy text Completions, if
actually needed, is a separate adapter capability, not an alias for Chat
Completions. Local OpenAI-compatible servers get their own profile; claiming the
same API shape is not evidence of feature parity.

`options` passes native generation settings through the selected adapter; it is
not an arbitrary replacement request body. The engine owns input/messages,
tools and tool choice, output schema, streaming, credentials, endpoint routing,
timeouts, retry budgets, trace metadata and provider storage/privacy policy.
Reject attempts to override those fields. Explicit data-retention policy belongs
to the deployment; use provider storage-disable controls where supported, without
claiming that this establishes a provider-wide zero-retention contract. Preserve
unknown non-reserved generation options only where the adapter can transmit them
faithfully; otherwise reject clearly. Never silently remove unsupported options.
Switching profiles replaces the complete provider configuration; do not inherit
another provider's temperature/reasoning fields. Optional step overrides apply
only within the selected profile; nested option objects replace, not deep-merge.

Use PydanticAI behind the model port, as selected in specification 11, with
provider SDKs constructed by the adapter. Verify native options, usage, tool
control and failure semantics with conformance tests. Do not run two
independent agent loops or retry owners. Disable hidden SDK retries when the
execution layer owns the attempt policy.

Capability checks are keyed by provider, API flavor, exact model/deployment and
region/API version when relevant. Distinguish client tools from hosted MCP,
required from specific tool choice, strict tool arguments from JSON final output,
and the ability to combine tools with that output format. A required tool-call
request is not evidence of successful tool execution.

Static validation uses declared adapter/model capabilities. A separate explicit
connected doctor/probe can verify deployment support. There is no per-request
`/models` discovery; configured identity is sufficient. Do not claim every
OpenAI-compatible server supports strict schemas or forced tool choice.

## MCP enforcement and bounded tool loops

The host owns MCP sessions for stdio and Streamable HTTP, tool allowlists,
authentication, timeouts, schema validation and execution records. It maps tools
to the selected provider's function-tool format. Provider-hosted remote MCP is
an optional future adapter, not the portability baseline; it may expose data or
execute tools outside the host's authorization boundary.

For `choice: required`, at least one allowed tool must successfully execute and
its result must enter model context before the step can complete. `required`
does not mean every allowed tool, nor does mere model emission of a tool call
satisfy the requirement. A named-tool choice narrows the same postcondition to
that tool. Provider tool-choice settings help enforce selection but are not
sufficient proof of execution or success.

After the required success, the runtime may allow a final answer; do not require
tools forever on every round. Bound model calls, tool calls, tool-output bytes,
total duration and concurrency. Enforce the final text/schema contract in host
code. If a provider cannot combine tool use and structured output, expose an
explicit bounded tool phase followed by a schema-output phase in the plan and
telemetry, or reject the combination. Never silently fall back to best-effort
prompt-only enforcement. Refusal, truncation and unsupported schema are distinct
failures, not usable business outputs.

Untrusted documents and tool results cannot change allowed servers, tools or
routes. Keep credentials outside prompts; disallow arbitrary destinations and
stdio commands from input. Tool annotations do not establish authorization,
read-only behavior or idempotency. Side-effecting tools need host policy and a
verified idempotency/reconciliation contract; uncertain timeout outcomes must
not trigger blind retries.

## Telemetry and privacy

An explicitly configured OTLP endpoint enables tracing/metrics. An absent or
empty optional endpoint creates no exporter and makes no telemetry network
calls; SDK localhost defaults must not enable export accidentally. Resolve
supported general/per-signal OTLP settings consistently. `mode` defaults to
`production` and can only be changed by trusted operator configuration.

Use one workflow span, named step spans, GenAI client spans for AI operations,
tool spans and adapter spans. Long durable waits use resumed processing spans
and links, not one permanently open root. Trace context in
`metadata.telemetry.traceparent`/`tracestate` is a carrier, not business identity.
Valid transport context takes precedence over the envelope carrier as a whole;
never merge two parents. Preserve incoming metadata exactly, but inject current
trace context into outgoing transport headers or separate queue fields. Invalid
context starts a new trace. Baggage is disabled or explicitly allowlisted and
filtered at trust boundaries. Sampling flags cannot override local limits.

Production records configured step/workflow names, revisions, safe status/error
codes, timings, provider/model identity, usage and cache state. It does **not**
record arbitrary response values: extracted names, account data, explanations,
quotes and even sensitive categories can reveal confidential information.
Optional result projection must allowlist both paths and finite approved values
for that workflow; nothing is content-safe simply because it is an output.

Debug enables bounded payload/prompt/response capture after pre-export filtering.
It remains sensitive, requires controlled access/retention, and should use
approved development data. Never log credentials, authorization headers,
cookies, session tokens, connection secrets or private model reasoning. Redaction
cannot guarantee removal of all confidential material in arbitrary text; hashing
or truncating content does not anonymize it. Collector filtering is additional
defense, not the first point where secrets are removed.

Use standard GenAI names where available: `gen_ai.usage.input_tokens`,
`output_tokens`, `cache_read.input_tokens`, `cache_write.input_tokens` and
`reasoning.output_tokens`. Cache usage is an input subset and reasoning an output
subset; never add them again to totals. Missing provider usage is unavailable,
not zero. Keep application result-cache hits separate from provider prompt-cache
hits; a local hit makes no fictional inference call or new token charge. Include
all actual attempts in run costs without double-counting wrapper spans.

Use seconds for standard operation-duration metrics. Distinguish queue wait,
step duration, inference duration and delivery duration. Metrics have bounded
labels, never arbitrary metadata, record IDs or extracted values. Export buffers
and timeouts are bounded; telemetry failures do not change business outcomes.
Operational traces are not the durable business audit ledger.

Current GenAI conventions are Development and live in the dedicated
`open-telemetry/semantic-conventions-genai` repository. Pin a reviewed revision;
do not copy old `cache_creation` attribute names or claim the schema is stable.

## Configuration, reliability and developer experience

Centralize user-facing defaults and validation in typed settings; generate
reference documentation and JSON Schema from that definition. Runtime code
receives resolved immutable configuration rather than reading environment
variables independently. Keep shared constants named and documented; do not
turn every module-specific literal into a giant global dependency file.
Examples: `ai_request_timeout_sec` covers one operation deadline;
`run_timeout_sec` covers the whole process; `max_tool_result_bytes` bounds a tool
reply. Persist the absolute run deadline: retries and resumed workers do not
restart it. Comments describe scope, units and why the bound exists.

Keep provider options separate from execution safety limits. Precedence is
built-in defaults, deployment settings, then allowed workflow/step refinements
within deployment ceilings. Secret environment substitution is explicit and
limited to designated configuration fields. Local `.env` loading is bootstrap
behavior, not a prompt or per-request capability.

Validate the entire bundle before activation: IDs and normalized catalog
collisions, graph reachability/termination, schemas, exhaustive choice routes,
unresolved behavior, available data on every route, adapter capabilities and
budgets. Reject references to a step that may not execute on that path unless
an explicit optional binding or join handles the absence. Freeze bundle and
effective configuration identity per run; development reload affects new runs
only. Configuration failure keeps the last valid deployed bundle active.

Proposed authoring commands: `foliqant init`, `validate`, `explain`, `run`, `serve`
and `test`. `validate` is offline and reports file/line and fixable contract errors;
`explain` shows the resolved graph, model bindings and call budgets; `run` reads
one JSON envelope; `serve` adds a configured transport. Offline tests inject fake
ports; live model tests require an explicit option and are not implied by lint.
These are proposed service commands, not aliases for existing `foliqant-model`.

Transport bindings are deployment configuration, not step definitions. For
example, the same `inbox` workflow can be exposed through either binding below.
Authentication, connection secrets and listener settings belong to the referenced
adapters; these abbreviated bindings are not public production endpoints:

```yaml
bindings:
  inbox_http:
    workflow: inbox
    input:
      adapter: http
      method: POST
      path: /inbox
    output:
      adapter: http_response
  inbox_events:
    workflow: inbox
    input:
      adapter: redis_stream
      connection: business_events
      stream: inbox_requests
    output:
      adapter: redis_stream
      connection: business_events
      stream: inbox_results
```

Direct HTTP response output requires a live HTTP request. HTTP-to-Redis may use
an acceptance receipt; Redis-to-HTTP delivery requires a configured webhook
adapter, not the synchronous `http_response` adapter. Validate binding compatibility
up front rather than leaking those transport constraints into workflow steps.

Development CLI/embedded execution may use an explicitly non-durable memory
store. For production async HTTP/Redis or side effects, recommend a durable
execution store (Postgres first candidate): inbox/idempotency, frozen revision,
checkpoints, recoverable leases/fencing, and transactional outcome/outbox.
HTTP/Redis input and output remain independently configurable. The input adapter
acknowledges durable acceptance only after the relevant commit, independently of
later result delivery. Delivery retry must not rerun completed workflow steps.
Deduplicate within trusted
tenant scope and reject conflicting reuse of a request key. External calls can
repeat after crashes; do not promise exactly-once effects. Async HTTP 202 means
accepted, not finished: return a separate acceptance receipt with execution ID
and status `accepted`, not the final envelope with fabricated decisions. Retrieval
or the configured output transport later supplies the terminal result. Cancellation
prevents further work but does not prove that an in-flight external effect was
cancelled; an uncertain effect requires reconciliation/review. A JSON decision
is not authorization to act.

No full BPMN system, arbitrary loops, automatic package installation, mandatory
sandbox, hosted UI, self-modifying graph or custom model server is required.
Expose progress through optional typed observations, but never route on partial
streaming output. Add token streaming to transport adapters only when needed.

## Readiness and evaluation before implementation

The proposal is complete enough for a design decision, not a production-readiness
claim. Implementation must freeze the envelope/settings/step schemas and their
generation map, dependency locks, provider capability matrix, durable-store
contract, authorization, retention, redaction and idempotency policies.

| Requirement | Acceptance evidence to require |
|---|---|
| Envelope and transport separation | Same synthetic workflow through embedded, HTTP and Redis adapters; unchanged metadata; identical validated decisions; no I/O imports in core. |
| Authoring and routing | Minimal project walkthrough; actionable file/line errors; all four routes plus unresolved cases; cycles, collisions and unavailable-output references rejected. |
| Models and MCP | Real deployment conformance for each declared API; native options preserved; reserved override rejected; forced-tool omission/failure never succeeds; final schema and budget enforcement. |
| Privacy and OTel | Incoming parent continuity, fan-out links, correct usage accounting; production exports contain no planted secrets/free text; debug redaction and limits; no endpoint means zero exports. |
| Reliability | Cancellation, partial failures, crash/resume and broker redelivery; uncertain MCP writes never blindly retried; durable acknowledgment/outbox and child joins tested. |
| Maintainability | One settings source, generated schemas/docs, typed extension tests, pinned workflow revisions and no training-runtime dependency. |

First implement an offline compiler and embedded core, then a real local model
profile and HTTP adapter, then host-owned MCP and provider conformance, then
durable store/Redis/fan-out. Telemetry hooks and privacy tests belong in the first
slice; export and content filtering cannot be bolted on after logs are designed.
The subsequent owner mandate authorizes implementation under specification 11.
This earlier proposal alone does not establish implemented or qualified behavior.

## Primary-source research

Checked 2026-09-21; provider feature support remains deployment/model-specific.

- [Eve](https://vercel.com/eve): directory-based instructions/tools/channels and durable agent experience; inspiration, not a dependency selection.
- [Next.js project organization](https://nextjs.org/docs/app/getting-started/project-structure): colocation and explicit file conventions.
- [Python protocols](https://typing.python.org/en/latest/spec/protocol.html) and [strict Pydantic validation](https://docs.pydantic.dev/latest/concepts/strict_mode/): typed ports and runtime boundaries have different responsibilities.
- [MCP Python SDK](https://pypi.org/project/mcp/): inspected stable 2.2.0, with 2026-07-28 protocol support. Pin a tested release during implementation, not an unbounded latest dependency.
- [W3C Trace Context](https://www.w3.org/TR/trace-context/) and [Baggage](https://www.w3.org/TR/baggage/): standard propagation, not authentication.
- [OTLP exporter configuration](https://opentelemetry.io/docs/specs/otel/protocol/exporter/) and [sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/): explicit enablement and filtering boundaries.
- [GenAI attribute registry](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md), [spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md), [metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md): current Development conventions and subset usage accounting.
- [Messaging spans](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-spans/): asynchronous processing and trace links.

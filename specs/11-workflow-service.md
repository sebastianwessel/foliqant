# Python workflow service

Status: implementation mandate accepted by the project owner on 2026-09-21;
implementation and acceptance evidence are tracked separately. This specification
supersedes the runtime recommendations in the modular service research proposal.
It does not declare an unimplemented feature production-qualified.

## Scope and authority

The owner selected Python, PydanticAI, uv, current maintained dependencies,
independent production/development installations, MCP including OAuth, protected
identity and trace metadata, OpenTelemetry, secure JSON logging, examples, docs,
skills, tests and independent review. Routine implementation decisions are
delegated within those constraints. No cloud deployment, paid inference,
customer-data use, model retraining, or publishing is implied.

The service interprets data through configured decision/LLM/tool steps and follows
a deterministic process. It never lets a model define the graph, authorization,
destinations, executable plugins or side-effect policy. One process hosts the
engine and its adapters. The model endpoint and production database/broker remain
separate services. The complete target includes embedded/CLI and HTTP entrypoints,
Redis intake/output, durable recovery, configured child workflows and joins.

Existing native decision tasks are reused unchanged. Additional per-label
evidence, calibrated confidence and new training task versions in specification
10 remain separate model-contract work; the service must not fabricate them.
Generic structured extraction is available through a caller-supplied JSON Schema
on an LLM step, without claiming calibrated accuracy or a new native task enum.

## Modules and reuse

`service/` is an independent Python 3.12 project with `pyproject.toml`, `uv.lock`
and `.venv`. The distribution is `foliqant-service`, import package `foliqant`,
and console command `foliqant`. Runtime dependencies exclude training, curation,
MLX, Torch, Transformers, datasets and Hugging Face model downloads.

`packages/decision-contracts/` owns a small reusable `foliqant-decisions` Python
package: the existing native decision models, semantic validator, category
catalog and their scalar definitions. Extract these from the model tooling
without changing wire schemas, validator behavior or existing artifact digests.
Update repository imports to the new canonical package rather than maintaining
two implementations. Generation-only settings remain in model tooling. Native
schema generation continues to produce `contracts/model/` from the same types.
The service does not import `foliqant_model`.

Service module ownership:

| Module | Responsibility | Dependency boundary |
| --- | --- | --- |
| `core/` | Immutable values, execution state, deterministic routes, child join | Standard library and ports only; no SDK/framework I/O imports |
| `ports/` | Async model/tool, validator, store, authorizer, observation and handler protocols | Standard library core values; no concrete adapters |
| `contracts/` | Strict Pydantic envelope, deployment, workflow and step boundary types | Pydantic and shared decision contracts |
| `compiler/` | YAML/frontmatter, schemas, graph and dataflow checks; immutable compiled plan | Contracts and core; no endpoint discovery |
| `adapters/models/` | PydanticAI provider construction, structured output, tool loop, usage | PydanticAI and selected provider SDKs |
| `adapters/mcp/` | MCP transport, OAuth, allowlists, schema checks, bounded execution | Maintained MCP client; no handwritten protocol/OAuth implementation |
| `adapters/storage/` | Memory development store; PostgreSQL durable inbox/checkpoint/outbox | Store protocol, database SDK |
| `adapters/transports/` | HTTP, Redis and CLI decoding/authentication/delivery | Engine, ports and selected transport SDK |
| `adapters/telemetry/` | Safe JSON events, OTel propagation, spans and metrics | Observation port and OTel SDK |
| `bootstrap.py` | Resolve settings/secrets; explicit trusted adapter registry and cleanup | Composition root only |

No arbitrary dotted import or executable plugin is accepted from YAML. Installed
Python integrations explicitly register handlers and ports. Runtime configuration
is frozen per execution; its canonical digest includes workflow files, schemas
and non-secret effective settings. Secret values never enter public revision IDs.

### Async concurrency and scaling

All execution, model, MCP, store, transport and authorization ports are async.
Use native async HTTP/database/Redis APIs. A blocking-only SDK requires a bounded
executor with explicit ownership and shutdown; cancelling the await does not
prove the underlying call stopped. A started blocking call retains its capacity
until the worker actually finishes, even if its caller times out or cancels.
Waiting cancelled calls must never start later. Copy task-local context into
the worker, never store caller identity on a shared client. SDK/socket timeouts
remain mandatory because Python cannot forcibly terminate a running thread.
Bounded shutdown reports an incomplete drain instead of claiming termination.
Never hold a database transaction while
awaiting a model, tool, webhook or another child. No per-request event-loop
creation, `asyncio.run`, blocking sleep, synchronous network client or unbounded
`gather` inside the runtime. Pure bounded validation/compilation stays synchronous
and compilation occurs before activation, not on the hot request path.

Deployment bounds active runs, model requests, MCP calls, child work and queued
intake independently. Semaphores are scoped to the actual scarce resource, not
one global execution lock; acquire them within the original deadline. Saturated
HTTP intake rejects with 429 and Retry-After before acceptance; broker consumers
stop claiming excess work. Durable jobs persist across replica/process restarts,
with leases/fencing deciding ownership. In-process limits are per replica; a
provider-wide rate quota across replicas requires a configured shared admission
adapter and is not implied by multiplying local semaphores.

No mutable per-request identity, credential or trace state lives on a shared
adapter singleton. Pools may be shared when their authentication scope is equal;
MCP sessions and credentials remain scoped by authenticated identity/resource.
Structured cancellation waits for owned tasks to finish or records uncertain
external outcomes. Shutdown stops claims/intake before draining tasks and closing
pools. Tests must demonstrate overlapping independent runs with a heartbeat
remaining responsive, bounded admission, cancellation while waiting, and no
cross-run identity/token/trace/result leakage. Measure real throughput separately;
async syntax alone is not scalability evidence.

## Envelope, identity and result

Input is a closed object with `payload` (any finite JSON value) and `metadata`
(JSON object, default `{}`). Duplicate JSON/YAML keys, non-finite numbers and
excessive nesting/size are rejected. Business payload is validated against the
workflow input schema. Output preserves the input payload and metadata by JSON
value, except authenticated identity enrichment described below and explicit
configured final payload projection. Model results never implicitly merge into
either field.

Protected metadata keys are `tenant_id`, `principal_id` and `telemetry`.
Identity values are optional, independently present, nonblank strings of at most
256 characters without control characters. Omitted means unavailable; explicit
null is not an identity. Tenant means organization, principal means user; principal
alone is valid. Both remain absent if unavailable. IDs never establish authority.

An ingress authenticator returns a typed trusted identity. Supplied protected IDs
must equal that identity; a caller cannot claim an ID the authenticator did not
establish. Missing fields may be populated from the trusted identity. Embedded
callers supply that same explicit trusted context; CLI input is not silently
authenticated by copying its body. No unverified X-Tenant/X-Principal headers.
All handlers/tools/children receive read-only identity; they cannot change it.
The normalized accepted envelope is persisted and preserved thereafter.

`metadata.telemetry` is a closed carrier with optional `traceparent` and
`tracestate`. Use W3C parsing, not custom trace IDs. Valid transport context takes
precedence as a complete carrier, otherwise valid metadata context is used;
invalid context starts a new trace. Incoming carriers are preserved, not mutated
to outgoing span IDs. Outgoing adapters inject current context separately. Baggage
is not forwarded by default. Trace context is neither authentication nor a way to
enable debug or bypass local sampling policy.

Result has `payload`, `metadata`, `decisions` keyed by step ID and `execution`.
`execution` contains host-generated ID, workflow name/revision, terminal status,
measured usage and a safe `error` for failed/cancelled execution. Status is
`completed`, `needs_review`, `failed` or `cancelled`.
Each step has `status` (`completed`, `skipped`, `failed`, `needs_review`, `cancelled`), a result
only when produced, and a safe error only when applicable. Single-question native
steps expose the native result directly, with question ID equal to the step ID;
multi-question steps retain `DecisionOutput`. Other steps expose validated JSON
or text. Nonselected steps are skipped, never synthetic successful answers.
The singular `question` form is always one unwrapped result with the step ID;
the explicit `questions` form requires at least two questions and always retains
the wrapper and authored IDs. A successful finish step has explicit null result.

Final payload projection applies to completed/review business outcomes. Static
binding checks include implicit review exits when `on_unresolved` is absent.
Technical failure and cancellation preserve the accepted input payload. If final
projection fails at runtime, return failed execution with a safe missing-binding
error and the accepted payload; retain actual step results unchanged. Do not
rewrite a completed step as failed merely to attach a run-level error. Cancellation
still propagates through async callers after bounded persistence/cleanup; a stored
cancelled result is for later retrieval, not suppression of task cancellation.

Known business uncertainty routes to review and returns a usable result. Technical
failure is not `missing_information`. Pre-execution invalid/authentication inputs
are boundary errors. Errors carry a stable code, retryability and safe message,
never exception text, model response, prompt, authorization header or stack locals.
Codes distinguish invalid configuration/input/output, unauthenticated, forbidden,
missing binding, timeout, budget exhaustion, dependency failure, conflict,
uncertain effect and cancellation. HTTP uses RFC 9457 problem details and proper
400/401/403/404/409/413/422/429/503/504 status where applicable, not blanket 500.

## Configuration and deterministic execution

Root `foliqant.yaml` selects named model, MCP, transport, storage and telemetry
profiles plus workflow directories. `.env` is bootstrap-only, ignored by Git;
existing process environment wins. Substitution is allowed only for designated
connection/authentication fields, never in prompt bodies or arbitrary input.
Secrets use secret-bearing types and are excluded from repr, diagnostics and
generated examples. Configuration does not read environment variables per step.

Workflow directory contains `workflow.yaml`, `steps/*.md` or `steps/*.yaml`,
`schemas/` and optional synthetic `tests/`. Markdown frontmatter owns structured
step settings; body owns instructions. YAML-only steps use `instructions` when
needed. Filename stem is the ID unless `name` is explicit. Stable step/workflow
IDs are lowercase snake_case and duplicates fail. Catalog authoring uses the
existing deterministic normalization and collision checks; never fuzzy-match
unknown output categories. Step file order does not determine execution order.

Workflow fields: `version: 1`, `name`, `start`, optional `defaults.model`, optional
`input_schema`, optional `output` payload projection. Paths must remain in the
bundle after symlink resolution. JSON Schema references are local and confined;
no network retrieval. YAML uses a safe loader; duplicate keys and custom tags fail.
YAML aliases are unsupported and rejected before expansion. Schema documents are
limited to JSON depth 64 and 10,000 schema nodes. The initial schema subset rejects
`$id` at schema positions so reference scope cannot silently differ from the
bundle-relative paths. Local files and fragments are supported for workflow
schemas; declared tool schemas must be self-contained and use internal fragments
only. Keyword-looking objects inside `const`, `default` or examples remain data.
Compiled plans retain all root/transitive schema resources and their base paths.
Revision hashes use the exact bytes already parsed for every dependency, never
a second read that could identify different file contents.

Step kinds:

- `decision`: source ID → JSON Pointer bindings, caller-authored native question
  or questions, instructions, optional model override. Compile shorthand catalogs
  to shared native types; validate complete output semantically, including evidence.
- `llm`: selected input bindings, instructions, model, text or JSON Schema output,
  optional allowed MCP tools and tool-choice policy. No workflow transitions before
  the final validated result.
- `mcp`: named configured server/tool and arguments consisting of literal JSON or
  explicit tagged bindings defined below. Executes directly without an LLM.
- `handler`: named trusted Python handler with declared input/output schemas and
  effect policy, using the same run context and bounded execution.
- `dispatch`: request-unit result pointer, category → child workflow mapping,
  explicit child input bindings and all-required-success join.
- `finish`: `outcome: completed|needs_review`; no model call.

`next` is a deterministic transition; decision steps may instead use `on_answer`
with exhaustive keys for their choice/ordinal/predicate catalog and an
`on_unresolved` review route. Omitted unresolved routing terminates needs_review.
Multiselect/request-unit results use configured dispatch or ordinary `next`, not
implicit multi-route execution. `next` and `on_answer` are mutually exclusive.
A validated answerable result may complete a leaf step; unresolved leaves review.
Answerability is evaluated before `next`: not_answerable, undetermined and
partially_answerable route to `on_unresolved` or terminate needs_review. This
includes multiselect/request-unit results. Multi-question steps prohibit
`on_answer`; every result must be answerable before `next`. Predicate answer routes
use `true` and `false` string keys; `unknown` uses the unresolved path. Quoted YAML
keys are required where a loader would otherwise parse a boolean.

### First-slice closed boundary shapes

All unspecified fields are rejected. JSON leaves explicitly permit arbitrary
finite JSON; closed control objects never use arbitrary extra fields.

| Shape | Fields and defaults |
| --- | --- |
| Binding | Exactly `{pointer: <RFC 6901 pointer>, optional: false}` or `{literal: <JSON>}`. A pointer with `optional: true` must have explicit `default: <JSON>`; default is forbidden otherwise. No implicit interpretation of a literal object. |
| Workflow | `version: 1`, `name`, `start`, `defaults: {model?: alias}`, `input_schema?: relative path`, `output?: Binding`. Absent output preserves payload. |
| Common step | `name?: ID`, `type`, `next?: ID`, `on_unresolved?: ID`; no body/unknown fields on non-prompt steps. |
| Decision step | Common fields plus `model?: alias`, `sources: {source_id: Binding}`, exactly one of `question` or `questions`, `instructions: string`, `on_answer?: {answer_key: step_id}`. Sources resolve to strings. Shorthand question omits id/prompt/allowedSourceIds: id is step ID, body supplies prompt, all bound sources are allowed. Explicit questions retain their declared IDs, prompt, criteria and allowedSourceIds. |
| LLM step | Common fields plus `model?: alias`, `input: {name: Binding}`, `instructions: nonblank string`, `output: text` or `{schema: relative path}`, `tools?: {server: alias, allow: [tool_name], choice: auto|required|{name: tool_name}}`. |
| MCP step | Common fields plus `server`, `tool`, `arguments: {name: Binding}`. Schema/effect policy belongs to the configured server catalog, not the model. |
| Handler step | Common fields plus `handler: registered name`, `input: {name: Binding}`. Registry owns schemas/effect policy; configuration cannot redefine them. |
| Finish step | Only `name?`, `type: finish`, `outcome: completed|needs_review`; no outgoing edges. |
| SafeError | `code`, fixed safe `message`, `retryable: bool`; optional bounded `location` identifies configured file/step, never source values. |
| StepResult | `status`, `result?: JSON`, `error?: SafeError`. completed requires result (including explicit JSON null); skipped/cancelled omit result; failed requires error. needs_review may retain a validated uncertain result but never a fabricated one. |
| Usage | `model_requests: nonnegative int`, `tool_calls: nonnegative int`; `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_write_input_tokens`, `reasoning_output_tokens`: nonnegative int or null. Missing any constituent count makes that aggregate null; measured zero remains zero. |
| ExecutionResult | `payload`, `metadata`, `decisions: {step_id: StepResult}`, `execution: {id, workflow, revision, status, usage: Usage, error?: SafeError}`. Failed/cancelled requires error; completed/review omits it. |
| AcceptanceReceipt | `execution_id`, `status: accepted`; no decisions/payload/model result. |

The decision shorthand requires authored `criteria`; catalog maps compile into
the shared `DecisionOption` list. The shorthand is authoring convenience only;
the resolved prompt sent to a model uses the shared native input contract.
The initial source-binding form accepts nonblank strings and represents each as
a native `document` source; it does not infer source kinds from text or filenames.
Core values are frozen dataclasses with recursively frozen JSON (read-only maps
and tuples). Boundary mapping preserves every JSON field, uses no lossy string
coercion and creates independent values. A validator port returns validated JSON
and explicit answerability/route facts to core; Pydantic models never enter core.
Generation command is `uv run --project service python service/scripts/generate_schemas.py`;
it produces `contracts/service/*.schema.json` and supports `--check`. Native
contracts retain the existing root schema generator and byte-for-byte snapshots.

Configured MCP profiles contain a declared catalog of allowed tool names, input
schema, optional output schema and host effect policy. Offline compilation checks
against this catalog only. Before first use, discover and compare schema digests
to the declared catalog; missing/changed schemas fail closed. Freeze validated
catalog identity per run and recheck on a new connection/resume. `doctor` can
report a new catalog for explicit operator review, never silently authorize it.

Compile rejects missing/unreachable nodes, cycles, incomplete answer routes,
unknown model/server/tool/handler IDs, dangling pointers, incompatible output
binding and references to prior steps not available on every path. Explicit
optional bindings have an authored literal fallback; missing mandatory input
never silently becomes null. Runtime checks actual input/output shape. Failed
reload leaves the last valid bundle active; no mutation of running revisions.

Bindings resolve against an immutable object with `payload`, `metadata` and
`steps` (step ID to StepResult). `/steps/classify/result` selects an earlier
result; public responses call that same step map `decisions`. Apply RFC 6901
escaping and array indices exactly; explicit null is a present value, not a
missing value. Defaults apply only to unresolved optional pointers, never to an
invalid pointer or a failed output schema. Binding errors expose no source data.

## Models and tools

### Embedded execution boundary

The embedded runner accepts a compiled plan, accepted envelope and explicit trusted
identity. It rechecks their identity agreement and validates the workflow input
before admission. Each execution owns its step results and budgets; no request
state lives on the shared runner. The step adapter receives resolved immutable
arguments, identity/metadata, a host execution ID, a monotonic deadline and an
attempt budget. Reserve model/tool attempts before I/O, including attempts that
fail. Missing usage for any reserved model attempt makes its token aggregate
unknown. No model call is charged for a finish or pure handler step.

The first embedded runner is explicitly nondurable. It exposes the same bounded
step execution boundary that durable workers will use, but does not pretend that
in-memory budgets or cancellation records survive process termination. Cancellation
propagates; no detached business tasks remain. Untrusted adapter exceptions map to
fixed dependency failures. An adapter that performs an external mutation must
translate uncertain timeout/cancellation outcomes through the effect-store policy
before production mutation support is enabled.

Use `pydantic-ai-slim` with only selected provider extras. PydanticAI owns the
agent/tool conversation and typed output; Foliqant owns business routing, budgets,
authorization, durable state and transport. Do not maintain a second model loop.
Support local OpenAI-compatible Chat Completions, OpenAI Chat/Responses,
Anthropic Messages, Azure OpenAI/Foundry explicit API flavors and Bedrock Converse
through maintained adapters; conformance claims are per tested deployment.

User-defined model aliases bind explicit model IDs; no reserved alias, implicit
model choice, paid fallback or per-request `/models` call. Provider-specific
generation options must be transmitted faithfully or rejected. Reserved overrides
include messages/input, tools/tool choice, output format/schema, credentials,
URLs, retries, streaming and privacy controls. Retention controls such as OpenAI
`store=false` are explicit adapter policy, not a claim of provider-wide zero retention.
Capability checks happen offline from declared profiles; optional live probes
require an explicit operator command. Unsupported combinations fail clearly.

### Model profile boundary

`ModelProfiles` is a closed `models: {alias: profile}` boundary. Every profile
names a model and explicitly selects `output_mode: native|tool`; it also declares
text/schema support, per-profile concurrency/queue bounds and an SDK request
timeout. It does not grant permission for tools. Native/schema support is a
deployment assertion to verify against the chosen server, not inferred from an
endpoint probe. No alias is reserved. The shared maximum-output default is 4096
tokens; unspecified sampling settings are left to the provider.

OpenAI explicitly selects Chat Completions or Responses. Compatible profiles
require a base URL, an explicit opt-in for plain HTTP and select the server's
`max_tokens` versus `max_completion_tokens` field. Azure distinguishes its
versioned resource-root API (explicit version required) from `/openai/v1`
(version forbidden). This is not blanket support for all Foundry catalog APIs.
API keys are referenced by environment-variable name, never embedded in profile
documents. The composition root passes one resolved environment snapshot;
request handlers do not read environment variables. An absent compatible-server
key reference means explicitly unauthenticated; it never borrows another
provider's ambient key.

Provider option models are closed. Reject options the SDK would silently discard
(such as a Responses seed or incompatible sampling/reasoning settings), rather
than accepting an ineffective setting. OpenAI retention settings are host-owned
and use `store=false`. Disable all SDK retries. Provider clients are owned by an
async application context and are cleaned up on partial startup failure as well
as normal exit. No SDK construction discovers models.

The first execution adapter uses explicit NativeOutput/ToolOutput, zero automatic
output repairs and independently validates native decision semantics or authored
JSON Schema before returning route facts. Each actual request is measured by a
run-scoped wrapper and charged before I/O. SDK usage field defaults are not
evidence that a provider measured zero. Missing reports remain unavailable.
Provider schemas fully inline frozen local references and wrap the authored value in
an object; public results expose the unwrapped value only after host validation.
Dynamic reference scopes and recursive schemas unsupported by StructuredDict
fail before inference. Reference siblings remain intersecting constraints, not
overwriting dictionary keys. Use non-strict provider output for authored LLM
schemas, retaining original host validation. Providers that require strict native
schemas must reject this combination before I/O; explicitly configured tool mode
is the supported alternative for Anthropic authored schemas. Canonical native
decisions use their fixed strict output definition and independent semantic
validation. Provider preflight precedes admission and attempt reservation.
Bound reference expansion before passing schemas to the
SDK, preserving literal annotation values and the original host validator.
Instrumented spans and MCP tools are added through their dedicated integrations;
until those are present, model instrumentation is explicitly disabled and
tool-bearing steps fail before making a request. Bedrock remains disabled until
credential discovery and its worker-thread lifecycle are explicitly bounded.

One owner enforces retry budgets: disable provider SDK retries. PydanticAI may
perform bounded output-validation repairs within the model request budget; it
must not repeat uncertain side effects. All actual attempts count toward usage
and deadlines. No implicit retry for authorization, invalid configuration,
refusal, truncation or uncertain mutation. Transient read-only failures may use
bounded backoff within the original absolute deadline.

Host-owned MCP supports current Streamable HTTP and stdio with current SDK
protocol negotiation. Pin tested releases; do not implement a second MCP wire
stack or force legacy session behavior. Stdio commands, arguments, working
directory and minimal environment come only from trusted deployment settings.
HTTP servers and authorization issuers are configured/validated, redirects are
not arbitrary credential-forwarding paths, and production uses HTTPS.

Tools are allowlisted at each step and authorized before each invocation. Validate
arguments and structured results against discovered schemas; text results remain
text. Bound output bytes, request count, concurrency and deadline. Tool annotations
are hints, not proof of read-only safety. Mutation requires a host effect policy
and stable operation identity; uncertain completion enters review/reconciliation.

Tool choice `required` means at least one allowed tool successfully executed and
its result entered model context. A named choice additionally requires that tool.
A failed call or mere emitted call does not satisfy the postcondition. After
success allow a final answer rather than forcing tools forever. Reject unsupported
tool-plus-schema combinations or use a declared bounded two-phase plan; no hidden
prompt-only substitute for required execution.

OAuth uses the maintained SDK's protected-resource/authorization-server discovery,
resource/audience binding and PKCE state validation. Interactive authorization is
an explicit operator action, never a request-time browser launch. Noninteractive
production uses a configured supported grant or an injected credential provider.
Tokens are stored outside Git, excluded from logs, protected at rest by the
selected store and keyed by server plus tenant/principal/authentication scope.
Never reuse a user token across identities or accept input-body access tokens.
For SDK server verification explicitly enable audience/resource checking; do not
rely on defaults. OAuth client_id is application identity, not user principal.
Credential providers reauthorize resumed actions; historical persisted IDs do not
preserve permission indefinitely. Each run/identity receives its own authenticated
MCP client session/toolset; concurrent callers cannot inherit another opener's token.

Pass trusted optional IDs and current trace carrier to the MCP adapter and its
credential-provider hook. On-behalf-of token exchange is a future pluggable
credential policy, not impersonation by headers. Identity forwarding to a trusted
server is explicit configuration with documented names; IDs cannot be overwritten
by model arguments. No token enters model messages, tool arguments or metadata.
For approved identity forwarding, use the configured domain-qualified `_meta`
key (deployment owns the domain) with only present `tenant_id`/`principal_id`
fields. This is a documented application extension, not MCP authorization.
Trace propagation uses standard unprefixed `traceparent`/`tracestate` in MCP
`params._meta`, as well as transport propagation where applicable. Configure
the SDK propagator without baggage. A modern MCP input-required response becomes
needs_review; do not automatically run unbudgeted interaction/sampling rounds.

## Reliability, transport and child execution

Defaults: run deadline 300 seconds, model operation 60 seconds, MCP operation
30 seconds, 32 visited steps, four model requests and three tool calls per step,
1 MiB input envelope and tool-result limits, JSON nesting 64, 32 child tasks,
child depth one and concurrency four. They are bounded configuration defaults,
not SLAs. Step refinements cannot exceed deployment ceilings. Absolute deadlines
survive retries/restarts. Shutdown stops intake, drains bounded in-flight work,
releases clients/leases and flushes bounded telemetry; cancellation does not
prove a remote effect stopped.

Memory storage is explicit nondurable development/embedded use. PostgreSQL is
the production execution store. Store schema versions are migrated explicitly
by an operator, not silently modified by request handlers. Persist immutable
accepted input/revision, trusted identity, scoped idempotency key/input digest,
absolute deadline, state/version, step checkpoints and result/outbox atomically.
Never persist credentials in execution records. Tenant and principal scopes are
explicit even when one or both are absent; anonymous durable production intake
is disabled unless deployment explicitly authorizes it.

Duplicate same-scope key/same input returns the existing execution; different
input conflicts. Workers claim leases with monotonically increasing fencing
versions and heartbeat; stale writers cannot commit. Persist external-effect
intent before calling. A crashed read-only step may be retried within budget;
an uncertain mutation cannot be repeated without a verified idempotent or
reconciliation adapter. Completed checkpoints are not rerun for delivery failure.
Persist consumed model/tool attempts before issuing calls; a crash never resets
the budget. A tool operation identity is assigned before execution and persists
across PydanticAI output repairs. Completed effects return the stored tool result
instead of executing again. Changed arguments under the same operation identity
conflict; a new tool-call ID is not permission to repeat a completed obligation.

HTTP synchronous execution returns a terminal result. Async intake commits then
returns 202 with execution ID and `status: accepted`; authenticated retrieval
uses the same identity scope and never reveals another tenant's existence.
HTTP authentication is a replaceable port; built-in deployment bearer bindings
and verified JWT issuer/audience/algorithm/JWKS policies establish identity.
Unauthenticated development mode is explicit and loopback-only.

Redis Streams input uses consumer groups and bounded pending-message recovery.
Acknowledge only after durable intake/deduplication, not before the database
commit. A separately leased worker runs accepted work. Transactional outbox
delivery has bounded retry/backoff and a visible exhausted state; consumers receive
stable event IDs and may deduplicate at-least-once delivery. Never rerun completed
business work to resend a result. HTTP response, Redis output and webhook output
have explicit binding compatibility; webhook authentication/URL is deployment
configuration and SSRF-safe, not a payload-provided destination.
Poison/unauthorized input is recorded as a safe rejection code plus opaque source
message ID in a bounded rejection stream; never copy its body or credentials.
Only acknowledge after that durable record succeeds. Apply input-size limits
before deserialization. Rejection/delivery retry exhaustion is observable and
requires an operator action, never an infinite hot loop.

HTTP defaults use `POST /workflows/{name}/runs`, `GET /runs/{id}` and
`POST /runs/{id}/cancel`; health/readiness expose no configuration or secrets.
GET returns a receipt with accepted/running state until a terminal result exists.
Internal states accepted/running/terminal are separate from delivery
pending/delivered/exhausted. Review is a terminal business hold. An authorized
reconciliation operation records an external outcome before any resumed action;
no API accepts an arbitrary edited model response as a verified result. Exact
durable SQL/operation schemas are frozen in the durability slice before coding.

Dispatch follows specification 10: persist one parent and distinct host-owned
child/action IDs per request instance, including same-category units. Hold partial,
unmatched, ambiguous or unresolved conditional work by default. Authorize each
child; evaluate prerequisites, mutual exclusion and shared-subject ordering before
dispatch. Only explicitly independent tasks run concurrently. All required
children must succeed; retain failures and sibling outcomes. Bounds reject whole
plans rather than truncate. Accepted corrections may retire only unstarted work;
completed/uncertain changed obligations require reconciliation and cannot replay.
No automatic compensation or exactly-once external-effect claim.

Production operators configure retention for payloads/checkpoints and protected
token storage. Provide tenant-scoped execution deletion after terminal delivery,
with an explicit idempotency-retention window: deleting deduplication identity
too early can permit replay. Backups and database access/encryption are deployment
responsibilities documented in the runbook. Raw prompts/responses are not a
separate default diagnostic archive.

## Observability and safe logging

Default `mode: production`, log level INFO, one JSON record per event. Explicit
debug changes verbosity, not permission to print arbitrary data. Use fixed event
codes and typed allowlisted fields, bounded counts/durations, trace/span IDs,
safe error codes and configured nonsecret names. Drop raw exception messages,
stack locals, arbitrary extras, URLs with query/userinfo, payloads, identity
values, prompts, tool arguments/results and private reasoning. Inspect the
genai-classifier formatter as a design reference but do not copy its unrestricted
message/exception/extra handling. Third-party logger output is filtered at the
same sink; enabling SDK DEBUG must not bypass the policy.
Apply the same allowlist before export to span names, status descriptions and
events, including SDK exception events. Disabling message-content capture alone
does not suppress raw exception text or unknown tool names in third-party spans.

OTLP is enabled only with an explicit endpoint; absent/empty means no exporter
or telemetry network traffic. Support configured tracing/metrics endpoints,
headers via secret settings, bounded exporter queues/timeouts and nonfatal failure.
Use W3C propagation through ingress, model/tool context and outbound MCP/transport.
Workflow and step spans surround PydanticAI GenAI client operations; MCP client
operations have standard MCP spans. Avoid duplicate inference spans and token
counts. Durable resumes use spans/links, not indefinitely open parent spans.

Follow current official GenAI and MCP semantic conventions, pinned to reviewed
versions in dependency evidence. Missing usage is unavailable, not zero. Cache
read/write tokens are subsets of input; reasoning tokens are a subset of output.
Metrics use seconds and bounded attributes, never tenant IDs, record IDs or
free-text output. No-exporter and exporter-failure tests must prove business work
still completes. Debug content capture is off unless separately enabled with
explicit field allowlists and bounds; credentials/reasoning remain excluded.

## Developer experience, artifacts and acceptance

CLI commands: `init`, `validate`, `explain`, `run`, `serve`, `test`, `doctor`,
plus explicit storage migration/worker operations. Offline validation/explain/test
do not discover endpoints or invoke a model. Live tests require explicit selection.
Errors identify file and line where available without echoing source content.
CLI stdout is JSON result/report; diagnostics go to safe stderr. Embedded users
can inject ports without a web server or broker.

`examples/inbox/` is a minimal complete application with Markdown steps, catalogs,
schemas, `.env.example`, a run command and expected safe output shape. Include
Compose dependencies for durable Redis/PostgreSQL/MCP/OTel demonstrations and
separate quickstart from operations. No downloaded model weights or real data.
Document production install with `uv sync --locked --no-dev` and selected extras,
dev install/tests/lint/typecheck/schema drift separately, and a minimal runtime
container built without dev/training dependencies. Repo-local service skill
teaches only implemented commands and safe integration patterns.

Acceptance families (each has success, rejection and recovery evidence):

| Requirement / capability / acceptance suffix | Required evidence |
| --- | --- |
| SERVICE-CONTRACTS | Strict independent optional identity fields; protected metadata; native schema/semantic equality after extraction; no training imports; generated schemas and architecture checks |
| SERVICE-COMPILER | Complete runnable example; graph/dataflow/schema/route validation; duplicate keys and path escapes rejected; no I/O during validation |
| SERVICE-RUNTIME | Native decisions, text/JSON LLM, direct MCP, handler, deterministic routes, embedded/HTTP/Redis equivalence; bounded cancellation/recovery |
| SERVICE-MCP | Current SDK protocol, stdio/HTTP, OAuth discovery/PKCE/resource binding/token isolation, required successful tool call, schema/budget/authorization enforcement and protected identity propagation |
| SERVICE-DURABILITY | Real PostgreSQL/Redis crash/redelivery/fencing/outbox tests, duplicate conflict, no rerun on delivery retry, uncertain-effect review, two-child join/correction isolation |
| SERVICE-PRIVACY | Secret/PII sentinel tests across JSON logs, third-party errors and exported spans; MCP trace continuity; token subset accounting; no endpoint means no export; telemetry failure nonfatal |
| SERVICE-DX | Installed wheel/production dependency inspection, CLI help/example execution, docs/skill/schema drift checks, independent review and fixes |

Use corresponding `REQ-`, `CAP-`, `PATH-...-SUCCESS`, `PATH-...-FAILURE`, and
`ACCEPT-` IDs in the traceability register. Tests with protocol servers establish
wire behavior, not real financial accuracy. Record actual live provider validation
separately and never claim untested provider/model deployments qualified. Completion
requires the entire service scope above; intermediate slices remain incomplete.

## Dependency evidence

[Current package/API research](research/workflow-service-dependencies.md) records
2026-09-21 primary-source and installed-signature evidence. The service lock is
the exact dependency authority. Use the official MCP SDK behind the tool port;
PydanticAI Tool.from_schema and StructuredDict both require independent host
JSON Schema validation. PydanticAI content instrumentation flags default on and
its tool-definition attribute can reveal private schemas even when content flags
are disabled; the production exporter removes it. No PydanticAI Harness or
FastMCP dependency is required for this deterministic workflow scope.

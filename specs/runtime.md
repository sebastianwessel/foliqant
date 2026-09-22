# Foliqant Python package

Status: active. This specification defines the reusable in-memory Python package,
its configuration, deterministic execution and public integration boundaries.

## Scope and authority

Foliqant is an installable Python 3.12 package built with PydanticAI, Pydantic,
the maintained MCP SDK and uv. It compiles local workflow bundles and runs one
bounded asynchronous invocation in memory. A workflow owns a graph of named sequential flows. Each flow contains decision,
LLM, read-only MCP, trusted handler or bounded flow collection steps. The graph, routes,
model aliases, tool allowlists and output bindings come from reviewed local
configuration; model output cannot invent executable structure or authority.

The package does **not** own persistence, queues, background workers, durable child jobs,
application authentication, HTTP/Redis transports, admin APIs or cloud
deployment. It does not promise recovery after process loss or caller return.
A small runnable HTTP example may adapt one request to the public in-memory API,
but that example is not a transport framework or package-level service contract.

Runtime decision assessments are defined in `foliqant.contracts.decisions`.
Shared question types and category catalogs live in `foliqant.decisions`.
Runtime settings and responses expose one current format without version fields,
compatibility loaders or migration aliases. External publishing and customer-data handling remain host responsibilities.

## Architecture and reuse

The root project builds `src/foliqant/`. Provider, MCP and telemetry integrations
are optional installation extras. `uv.lock` defines the tested dependency set.

| Module | Responsibility | Dependency boundary |
| --- | --- | --- |
| `core/` | Immutable accepted values, execution records, budgets and deterministic transitions | Standard library and ports only; no Pydantic, SDK, transport or persistence imports |
| `ports/` | Async model, MCP/tool, validator, observer and handler protocols | Core values only; no concrete adapters |
| `contracts/` | Strict Pydantic envelope, deployment, workflow, step and result boundary types | Pydantic, core and shared decision contracts |
| `compiler/` | Safe YAML/Markdown loading, local schema checks, graph/dataflow validation and immutable plans | Contracts and core; no endpoint discovery or execution |
| `adapters/models/` | PydanticAI provider bindings, structured output and measured usage | Selected provider SDKs behind ports |
| `adapters/mcp/` | Maintained MCP client, OAuth, declared catalogs, authorization and schema validation | MCP SDK behind ports; no handwritten protocol stack |
| `adapters/telemetry/` | Safe logging and optional privacy-filtered observations | Observer port and reviewed telemetry SDK surface |
| `bootstrap.py` | Resolve environment references once and own adapter lifetimes | Composition only; no hidden global application state |
| `evaluation/` | Immutable golden suites, isolated/full-pipeline checks and variant reports | Public result contracts and standard-library scoring; no automatic judge calls |

No arbitrary dotted import, executable plugin or environment interpolation is
accepted from workflow YAML. Hosts explicitly register handlers and authorization
hooks. Workflow files and local schema resources are read once during preparation;
request-time execution performs no filesystem or schema-network lookup.

The package root lazily exports `Envelope`, `ExecutionResult`, `Identity`,
`PreparedApplication`, `RuntimePlugins`, `WorkflowApplication`,
`load_environment`, `prepare_application` and `open_application`. Embedding code
uses `load_environment` only when selecting an additional environment location;
importing the package alone does not load configuration or initialize clients.

## Packaged JSON Schemas

Python boundary types are the source of truth and runtime validation uses those
types directly. Generated JSON Schemas ship as non-Python resources under
`src/foliqant/schemas/` in the same distribution. Editors and external tools may
read them with `importlib.resources.files("foliqant").joinpath("schemas", name)`.
No separate schema package or runtime filesystem load is required. Schemas have
self-contained definitions and stable URN identifiers, not fabricated web URLs.
CI checks generated files against the types and verifies installed-wheel access.
Business-specific schemas remain beside their workflow, flow or step definitions.

## Input, identity and output

The public envelope is a closed object with `payload` containing any finite JSON
value and `metadata` containing a JSON object, default `{}`. Decode untrusted JSON
with bounded byte/depth checks. Duplicate keys, non-finite numbers and malformed
UTF-8 fail. Validate payload against the compiled workflow input schema before
admission.

`tenant_id` and `principal_id` are independently optional context metadata.
Each present value is a nonblank bounded string without control characters.
When the caller omits an explicit `Identity`, the application derives invocation
context from the validated envelope fields. A caller may instead pass an explicit
`Identity`; body values must match it and absent values may be populated from it.
This is consistency checking and enrichment, not identity verification. These
values never authenticate or authorize. The package contains no application
authenticator, token verifier, workflow-grant system or identity-header
convention. A host that exposes the runner remotely owns those concerns.

An optional closed W3C carrier contains `traceparent` and `tracestate`. Valid
transport context takes precedence as one carrier; otherwise valid metadata
context may be used. Baggage, credentials and business metadata are not forwarded.
Trace context cannot grant permission, enable debugging or change execution.

One invocation returns one `ExecutionResult` with projected `payload`, accepted
`metadata`, `flows` keyed by flow instance ID, selected `transitions` and terminal
`execution` information. A flow report contains status, local `steps`, optional
projected `result`, safe error and measured time/usage. Step IDs are scoped to
that flow. No flattened result alias is exposed. Workflow output defaults to its
accepted input; flow output defaults to its bound input when no projection exists.
Output absence is distinct from explicit null.
Statuses are `completed`, `needs_review`, `failed` or `cancelled`. Results contain
the caller's accepted business data, validated step outputs, measured usage and
only canonical safe errors. Error and diagnostic fields never contain raw exception
text, stack locals, credentials or unrelated SDK content. Technical
failure preserves the accepted input and completed step records. Caller cancellation
propagates without a returned result.
Known business uncertainty produces `needs_review`; it is not a technical failure.

## Deployment and authoring

Public YAML examples and scaffolded files use block-style mappings and lists for
readability. Empty collections may use `{}` or `[]`; business JSON Schemas remain
JSON. This authoring convention does not restrict the YAML syntax accepted by the
existing safe loader.

Deployment configuration contains optional `workflows`, optional `models`,
optional `mcp`, `execution` and optional `telemetry`. Paths are relative to the
configuration file and must remain below its directory after symlink resolution.
Unknown fields, duplicate keys, aliases, custom tags and unrecognized union tags
fail. Supported deployment fields accept a complete `$VARIABLE_NAME` reference;
`$$` escapes a literal dollar. No shell expressions, recursive expansion or
substring interpolation is accepted. Workflow prompts, schemas and customer data
are never environment-expanded. Missing required values fail startup locally.
Credential values never enter diagnostics, public configuration exports or revision IDs.
Offline compilation checks references without requiring environment values.
Bootstrap loads the adjacent `.env` once, with supplied process values taking
precedence, and validates resolved settings before opening adapters. It does not
test credentials or probe provider endpoints.

Execution configuration bounds local run concurrency and waiting capacity plus
run/model/tool timeouts, visited steps and model/tool attempts per step. Limits
are local admission controls, not distributed quotas or service-level promises.
Configuration and workflow revisions are deterministic across processes for the
same nonsecret semantic input. Files compile before adapters open.

The recommended layout is `config/settings.yaml`, `config/<workflow>/workflow.yaml`,
`config/<workflow>/<flow>/flow.yaml`, and named step definitions. When `workflows`
is omitted, preparation discovers immediate nonhidden `*/workflow.yaml` files
below the settings directory; it does not recursively search or inspect gold.
An explicit workflow registry selects only its declared paths. Empty discovery,
invalid IDs, escaping symlinks or mismatched declared names fail compilation.
Configuration, prompts and business outputs use generic names; the actual
package/import/CLI identity remains `foliqant` until a replacement is chosen.

A workflow declares a `flows` mapping, optional `name`, `start`, `defaults.model`,
input schema and output binding. Omitted name derives from its directory. Start
is inferred only for one routed flow; multiple routed flows require an explicit start.
Callable flows are explicitly declared with `callable: true` and have no transition,
input bindings or unresolved route. See [collections](collections.md).
A routed flow instance declares named `input` bindings, required `transition`, optional
`on_unresolved`, and optional `definition` (inline or file). Omitted definition
resolves `<flow-id>/flow.yaml` beside workflow.yaml. A flow definition declares
an optional input schema, output binding and a nonempty ordered `steps` list.

A step list entry is an ID or `{id, definition?}`. Without an explicit definition,
resolve exactly one of `<id>.step.yaml`, `<id>.step.md`, `<id>/step.yaml`, or
`<id>/step.md` beside flow.yaml. Missing or ambiguous candidates fail; there is
no extension precedence. Explicit inline definitions and file references use
the same compiler and support reuse. Unlisted step files are not executed or
loaded. File discovery never determines business order, routes or data access.

The step kinds are:

- `decision`: one or several native questions and named sources, using an
  inherited or explicitly configured model; semantic validation is mandatory.
- `llm`: named inputs, instructions, optional prompt template, text or JSON Schema
  output and an optional allowlist of MCP tools.
- `mcp`: one configured server/tool with explicit literal or pointer arguments.
- `handler`: one trusted host registration with frozen input/output schemas.
- `flow_collection`: a bounded sequential list of explicitly planned callable-flow
  invocations, sharing the parent execution and retaining individual records.

Steps advance in list order; they declare no routes or terminal operations.
A flow transition is `{flow: id}`, `{outcome: completed|needs_review}`, or a
match object with `binding`, `cases` and a required `default` target. Case keys
match strings exactly; null/unmatched strings take default, other JSON types fail.
No coercion, expression evaluator or model-selected graph exists. Missing bindings
fail unless optional with an explicit default. All configured flow targets must
exist and the reachable graph must be acyclic; unused definitions are rejected.

Within a flow, pointers see `/payload` (that flow's bound input), `/metadata`,
and `/steps/<id>` (only local records). At workflow boundaries they see the
original `/payload`, `/metadata`, and `/flows/<id>/result`, not another flow's
internal step records. Routed flow input bindings and explicit collection item
inputs provide cross-flow data transfer. Callable results remain nested in their
collection ledger. IDs are stable lowercase snake case. Missing differs from explicit null.
The compiler validates earlier-step order, flow dominance, optional projections
on early review, known closed-schema paths and type compatibility. Open/complex
schemas remain runtime-checked. These checks do not prove business correctness.

Files resolve relative to their declaring file. Step/schema references stay
inside their flow bundle; workflow-to-flow and deployment-to-workflow references
stay inside the configuration root, after symlink resolution. Inline and file
forms use one compiler. Markdown steps have exactly one instruction source:
frontmatter or a nonempty body. Schemas may be inline or files. JSON Schema refs
remain local and confined; remote, dynamic and unbounded recursive resolution is
unsupported. Exact parsed dependency bytes are frozen for revision hashing.
Declared model capabilities must satisfy each step before adapters open.

Compiler failures contain a stable reason, authored file location, safe field
path and corrective hint. Raw Pydantic errors and authored values are never
rendered. CLI `validate` and Python `prepare_application` share this compiler;
there is no parallel validation engine or network preflight.

### Classification selection and unresolved policy

`DecisionOutput` in `foliqant.contracts.decisions` contains one result per question.
The runtime adapter orders validated results in the supplied question order for
predictable bindings and evaluation paths.
Each result preserves `questionId`, `type`, `answerability`, and the existing
answer shape, except request units omit `evidence`. It requires a nonblank
`reason` of at most 400 characters and `evidence_strength: limited|strong|null`.
There are no runtime explanation, citation, contrary-evidence or missing-fact
arrays. A non-null request-unit subject must occur verbatim in an allowed input
source; source IDs remain part of the input contract.

Strength assesses support for the whole reported conclusion: its answerability,
issues and any substantive answer. `strong` means decisive supplied support under
the authored criteria, including valid inference or a demonstrated inability to
answer. `limited` means weaker support for a permissible interpretation, without
inventing an essential missing fact. For collections, include material claims
about returned members and unresolved parts; many clear members cannot compensate
for a weak material claim. `null` means no strength assessment was made, not that
an answer is absent. The required strength field is independent of answer/status;
strong abstentions and unknown predicates are valid. Prompts request an assessment
whenever possible; validators cannot prove its semantic quality. Reason/strength
are qualitative model assessments, not confidence, calibrated probability or a
correctness guarantee. Rating urgency or severity itself is not rating support.
No automatic evidence threshold, fallback or review transition is added.

The issue domain remains exactly `no_supported_answer`, `conflicting_information`,
and `multiple_valid_options`. Missing details and requests outside the allowed
answers share the first code; explain the particular obstacle in `reason`.
Do not derive machine-readable issues by parsing rationale text.

A single-choice decision can declare `fallback: {category: {id, description?},
on: [issue, ...]}`. Category IDs use the same deterministic normalization as
catalog IDs and must not collide with model-selectable options. Descriptions
are optional, nonblank when supplied, and may contain multiline examples.
There are no separate positive/negative-example fields. The fallback definition
is not an option sent to the model.

Validated single-choice results expose a separate step `selection` containing
`category` and `origin: model|fallback`. The native `result` remains unchanged.
Fallback requires `not_answerable`, nonempty issues, and every reported issue in
the explicit allowlist. It never changes answerability, step review status or
technical failure. Omit selection when there is no supported selection; do not
serialize null. Expose selection through public step records and binding context.

A flow's `on_unresolved` accepts a target object or a map with `default` and
optional keys from the three issue codes. Only a decision operation's validated
issues select issue-specific targets. Resolve each issue through its entry or
default; follow a shared target only if all agree, otherwise use default. Missing
issues and nondecision review use default. Absent configuration ends with review.
A direct unresolved target cannot be `{outcome: completed}`. It may select an
explicit review-handling flow which later completes, retaining the earlier flow's
review report. Fallback selection never bypasses review. All targets participate
in graph/dominance validation; technical errors do not use unresolved routing.

### Per-step model selection and explicit context

Model steps may inherit the workflow default, use an existing profile alias,
use `{profile, model?, options?}` to override that profile, or supply an existing
complete provider-discriminated model configuration. Partial options inherit
unspecified values and validate against the effective provider's existing option
type. Do not copy incompatible settings between providers. Profile-derived
bindings share the source profile's admission limit; overrides must not multiply
allowed parallel requests. Inline credentials must be environment references.
Only designated model deployment fields are environment-expanded, never prompts
or bound context. Public plans retain binding identity without credentials.

Prior-step context is explicitly selected using existing `input`, `sources`, or
MCP `arguments` bindings. RFC 6901 pointers can select a complete result or named
fields; optional bindings require an explicit default and preserve missing/null
semantics. No prior history or caller envelope is forwarded implicitly. Compile
known type/dominance errors offline and validate tool arguments before I/O.

Decision source bindings may explicitly set `format: json`; otherwise they require
nonempty text. JSON sources serialize once, with stable keys, into native source
text. No input-format marker is sent to the runtime model. Keep original source documents
separate from prior model assessments, which are not independent corroboration.

LLM `prompt` templates replace the default JSON user message. `{{ name }}` accepts
only an exact declared input; every inserted value, including strings, is
JSON-encoded. `{{{{` and `}}}}` escape doubled braces. Unknown or malformed
placeholders fail compilation. Render once: no recursive substitution, expressions,
filters, attribute access, environment expansion or duplicate appended inputs.
Decision steps retain their canonical task message and cannot use this template.
An LLM step's `max_iterations` bounds logical model turns, including the final
answer turn. It defaults to 4 and accepts integers 1–1024. A turn is one model
request in the conversation; provider retries of that request remain the same
turn. The step fails with `budget_exhausted` before a turn beyond this bound.
The limit applies with or without tools and resets for every step invocation.
It is independent of `execution.model_requests_per_step`, which counts actual
provider attempts, including retries, and the tool-call attempt budget.

## In-memory execution

`WorkflowRunner.run` validates and accepts one envelope, acquires shared local
admission, then creates invocation-local state, budgets and task context. It walks
the immutable flow graph until a terminal outcome, review, failure or cancellation and maps
the internal result through the strict public result contract. No accepted state,
checkpoint, idempotency record or result survives process loss or is queryable
after the call returns.

A flow executes sequentially and stops immediately on review, failure or cancellation.
Success routing runs only after a completed flow; review uses `on_unresolved`.
All flows share one root admission slot, execution ID, monotonic deadline and
visited-step budget. Transitions do not reacquire admission or reset counters.
Root usage sums flow subtotals once; parent and child durations are not added.
Flow output may be projected on review, so references to unexecuted steps require
explicit optional/default bindings. Technical failure preserves accepted input
and completed records rather than publishing a success projection.

All model, MCP and handler operations are async. Use native async I/O. A blocking
SDK must run through an owned bounded executor or an owned shield-and-drain
wrapper around an SDK worker pool, bounded by local admission and SDK socket
timeouts. A started blocking call retains its capacity until it actually
finishes even if the caller stops waiting; cancellation never proves a remote
operation stopped. Waiting cancelled calls must not start later. Do not create
per-request event loops, call `asyncio.run` inside runtime code, use blocking
sleeps or launch unbounded tasks.

Reserve model/tool attempts immediately before external I/O. Failed or unreported
attempts remain charged for that invocation. Missing token measurements remain
unknown, not zero. Every timeout is bounded by the original monotonic run deadline.
Each invocation owns records and context; shared clients contain no mutable caller
identity, token, trace or result state. Shutdown stops admission, drains owned work
within configured bounds and reports incomplete cleanup safely.

Handler registrations are trusted Python objects supplied directly by the host;
YAML cannot import them. The in-memory runtime permits read-only integrations.
Writes requiring durable operation identity, reconciliation or retry guarantees
are outside scope and must fail before external I/O.

## Models and external tools

Model profiles select explicit provider/model IDs, endpoint/API flavor,
structured-output mode, capabilities, bounded timeouts/admission and environment
credential references. No `/models` discovery or hidden SDK retry is allowed.
Bootstrap owns one client lifespan and closes partially opened clients on failure.
Provider preflight occurs before admission and attempt reservation.

The native `google` profile targets the Gemini Developer API with an explicit
API-key reference. The native `bedrock` profile targets Converse with a required
region and the host's boto3 credential chain; the runtime environment mapping
resolves authored region/model references but does not inject AWS credentials.
Both expose common `max_tokens`, `temperature`, and `top_p` options, with no
arbitrary provider request dictionary or hidden SDK retry. Bedrock client
construction and cleanup run off the event loop. A started blocking Converse
call keeps its owned model admission through timeout or caller cancellation
until the SDK call actually ends. Native structured output and forced tool
choice are subject to the selected model's verified provider profile.

PydanticAI executes decision and text/schema LLM steps. Decisions use the strict
runtime output contract plus independent cross-field validation against the
compiled questions and supplied sources. Both native and tool output modes append generic contract guidance to
authored business instructions: allowed IDs, answerability/null rules, the
unchanged status/issue meanings, concise reasons (aim 160 characters, hard maximum
400), and evidence-strength semantics. This does not truncate responses, change
criteria, retry invalid answers, or weaken validation.
The generated predicate schema expresses true/false with answerable and unknown
with not_answerable/undetermined through complete object alternatives. Independent
Python validation retains the same answerability rules; provider schema support
alone does not establish semantic correctness.
Authored JSON Schemas are fully inlined from frozen local resources for providers,
then results are checked against the original host schema. Unsupported recursive
or dynamic schemas and unsupported provider/mode combinations fail before I/O;
there is no prompt-only structured-output fallback. SDK retries are disabled.

MCP profiles use current maintained SDK transports for Streamable HTTP or stdio,
one declared bounded catalog and read-only effects. Discovery must match the
declared names and schemas. Validate and freeze arguments before authorization and
I/O; validate results independently. Each step allowlists tools, and a host
`ToolAuthorizer` may apply resource-specific business rules. Required or named
tool choice is satisfied only by a successful validated call that entered model
context. Input-required becomes `needs_review`; automatic interaction rounds are
out of scope.

MCP OAuth delegates discovery, registration, PKCE/state, resource binding and
refresh to the SDK. Profiles contain a credential-hook ID, never a token. Allowed
authorization origins are explicit HTTPS origins. Interactive login is an
operator action, not request-time behavior. Credential storage is host-owned,
protected at rest and partitioned by complete server/resource/auth and optional
tenant/principal scope. Never place tokens in model messages, tool arguments,
metadata, logs or traces. Stdio receives a fixed safe baseline plus an explicit
environment overlay, never the whole process environment.

Optional caller-supplied identity context may be forwarded only through a configured
domain-qualified MCP `_meta` key. W3C `traceparent`/`tracestate` are propagated
separately without baggage. Tool authorization and OAuth authorize the external
tool connection; they do not add application authentication to the runner.

## Bounded transient retries

Model and MCP profiles accept `retry` with `max_attempts` (1–8, default 1),
`initial_delay_seconds` (0–60, default 0.25), and `max_delay_seconds`
(0–300, default 5, at least the initial delay). Attempts include the first call.
The default therefore sends one request with no recovery attempt.

Only explicitly classified completed HTTP responses with status 429, 500, 502,
503 or 529 qualify. A generic error's `retryable` flag alone never authorizes
re-execution. Timeouts, cancellation, disconnects, authentication failures,
invalid arguments and invalid model/tool outputs are terminal. The runtime does
not retry whole workflows, flows, agents or tool sessions. MCP calls remain
read-only and each attempt repeats argument authorization and budget reservation.
SDK automatic model retries and output-repair retries remain disabled.

Use capped exponential full-jitter backoff. A valid `Retry-After` is a minimum;
if it cannot be honored within the delay cap or remaining operation deadline,
return the safe failure rather than retrying early. Invalid `Retry-After` values
also prohibit retry. Model attempts reacquire provider capacity; MCP retains its
admitted authenticated session slot during backoff. Reserve an attempt before
every actual request. Backoff and subsequent
admission consume the same operation deadline and root execution budget. The
first model admission may precede the model-operation window, but never extends
the root deadline. Cancellation interrupts waiting without launching another call.
Record each actual attempt and unknown usage honestly; retries do not imply
remote idempotency or guarantee recovery.

## Privacy, logging and observations

Safe JSON logging uses fixed event codes and allowlisted bounded fields. It drops
payloads, identity values, prompts, responses, arguments/results, credentials,
raw exception text, arbitrary extras and URLs with query/userinfo. The same policy
applies to third-party logger output and debug mode. A bounded nonblocking sink
reports dropped events without blocking the event loop.

Optional telemetry is disabled when no explicit endpoint is configured. Sanitize
spans before queueing, including SDK exception events, tool definitions, resources,
scope/link attributes and status text. Use configured nonsecret workflow, flow, step,
model, provider and tool labels only. Missing usage stays absent. Observation and
export failure cannot replace a business result. Bootstrap owns installation and
bounded shutdown; embedded hosts are never silently given a new global provider.
Explicit owned providers preserve workflow → flow → step → model/tool attempt
parentage in embedded applications as well as the CLI. Flow duration and step
metrics retain flow identity. Lifecycle log events use the active trace IDs
without recording business data. OTLP requests do not follow redirects and do
not log or consume unbounded collector response bodies. Shutdown failures are
reported safely without replacing a completed business result.

## Entry points, example and acceptance

The package exposes embedded composition plus offline `init`, `validate`,
`explain`, `doctor`, foreground `run` and explicit `evaluate` commands. Evaluation
check/replay modes are offline; ordinary evaluation executes configured targets.
Offline commands do not open model/MCP endpoints. Commands default to
`config/settings.yaml` relative to the current directory; `--config PATH`
selects another explicit file without parent discovery. `init` atomically creates
a minimal local-model summary flow, its Markdown step, example envelope,
`config/.env.example` with `MODEL_ID`/`MODEL_BASE_URL`, and usage instructions.
Validation is offline; executing the generated flow requires the configured
endpoint and provider extra. Init does not install packages or start a backend.
Successful CLI output is one safe JSON object; failures use
one stable safe error and a nonzero exit. There is no durable lookup/cancel operation or packaged HTTP server.

The runnable HTTP example may use a small maintained ASGI library to decode one
bounded request, invoke the in-memory application and return the terminal result.
It must state that authentication, authorization, persistence, idempotency,
background execution, recovery and production hosting are the embedding
application's responsibility. It may not create detached work or expose accepted,
lookup or cancellation resources.

Acceptance families require success and failure evidence:

| Requirement / capability | Required evidence |
| --- | --- |
| `PACKAGE-CONTRACTS` | Strict envelopes/results, runtime reason/strength and one closed output shape, substantive/null/collection boundaries, subject occurrence, independent optional identity, W3C carrier, generated schema drift |
| `PACKAGE-COMPILER` | Safe deterministic bundle compilation, graph/dataflow/schema checks, duplicate/path escape rejection and no endpoint I/O |
| `PACKAGE-RUNTIME` | End-to-end in-memory decision/LLM/MCP/handler execution across sequential flows, bounded concurrency, cancellation and concurrent state isolation |
| `PACKAGE-MCP` | Current SDK HTTP/stdio behavior, OAuth isolation, declared catalog/schema checks, budgets, authorization and protected context propagation |
| `PACKAGE-PRIVACY` | Secret/PII sentinel checks across safe logs and optional observations; telemetry failure remains nonfatal |
| `PACKAGE-DX` | Locked install, public schema drift, CLI/example execution, documentation/skill checks and independent review |
| `PACKAGE-EVALUATION` | Isolated-step parity, immutable gold, exact/set/custom scoring, confusion/multilabel counts, failure/skip denominators, detailed private reports, offline validation/replay, startup independence and bounded cancellation |

Tests use synthetic inputs and protocol fixtures. They do not claim live model
accuracy, application security, durable recovery or production qualification.

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
`metadata`, `flows` keyed by flow instance ID, the workflow run's `start`
(`{flow, route: {kind: direct|route, index?}}`, omitted for isolated flow and
step runs, agreeing with the workflow span's `route.selected` event), selected
`transitions` (each with `route: {kind: direct|cases|route|review, index?,
case?}`) and terminal
`execution` information (with `trace` IDs when telemetry is enabled). A flow
report contains status, local `steps`, optional projected `result`, safe error,
measured time/usage and `attempt_count`; repeated and retry flows add
`attempts`, `attempts_usage`, `attempts_elapsed_seconds` and, for repeated
flows, `repeat.stopped_by`. Step IDs are scoped to
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
A technical failure is never a business outcome: a failed model request, tool
call or handler fails its step, flow and run with one precise code (the
[canonical codes](../docs/integration/errors.md#canonical-error-codes)), and no
`on_unresolved` route, review path, `fallback` category, binding `default` or
`first_of` alternative, repeat `until`/`continue_when`, retry flow or later
collection item acts on it. Only native abstention and an MCP server's
input-required result are review outcomes. `SafeError` carries `code`, its fixed
`message`, `retryable` (supplied by the failing boundary, never inferred from the
code) and optional content-free `reason` (`json_parse_error`, `schema_violation`,
`decision_contract`, `missing_output`, `reasoning_consumed_budget`,
`answer_exceeded_budget`), `location` (a JSON pointer in schema vocabulary or
`question:<id>`) and `constraint` (validator keyword or problem kind); absent
fields are omitted and values outside their safe pattern are dropped. Every usage
object has the required count `output_retries` after `tool_calls`: the model
requests, included in `model_requests`, that asked for a corrected output.
A provider stop before completion has its own code: `length` is
`output_limit_reached`, `content_filter` or a reported refusal is
`output_refused`, and a provider `error` stop is `dependency_failure`; all are
non-retryable, because a limit stop recurs with unchanged input and options.
Only a structurally complete response that fails validation is `invalid_output`.

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
`defaults.on_unresolved`, input schema and output binding. Omitted name derives
from its directory. Start is inferred only for one routed flow; multiple routed
flows require an explicit start. `start` names a flow or is an ordered `route`
whose conditions read only `/payload` and `/metadata` and whose targets are
flows. Callable flows are explicitly declared with `callable: true` and have no
transition, input bindings or unresolved route; a collection or one `repeat`
invokes them. See [collections](collections.md).
A routed flow instance declares named `input` bindings, required `transition`, optional
`on_unresolved`, optional `repeat`, and optional `definition` (inline or file).
Omitted definition resolves `<flow-id>/flow.yaml` beside workflow.yaml. A flow
definition declares optional `defaults.model` (precedence over the workflow
default for its steps), an optional input schema, output binding and a
nonempty ordered `steps` list. A flow without its own `on_unresolved` inherits
`defaults.on_unresolved`; an inherited flow target must be reachable from every
inheriting flow without a cycle (`invalid_default_review_route`), and a review
route never targets its own flow (`review_route_to_self`).

A step list entry is an ID or `{id, definition?, when?}`. Without an explicit definition,
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

Steps advance in list order; they declare no routes or terminal operations. A
step with `when` runs only when its condition holds; otherwise it is recorded as
`skipped`, consumes no step budget and the flow continues.
A flow transition is `{flow: id}`, `{outcome: completed|needs_review}`, a
match object with `binding`, `cases`, a required `default` target and optional
`default_covers`, or an ordered `route`. Case keys match strings exactly;
null/unmatched strings take default, other JSON types fail. `route` entries are
evaluated in order and the first true condition selects its target; every entry
but the last has `when`, the last has none (`route_without_otherwise`,
`misplaced_otherwise`). `on_unresolved` accepts a target, an issue map or a
`route`. No coercion, expression evaluator or model-selected graph exists.

A binding is `literal`, `pointer`, or `first_of` (1–16 pointer members, the
first that resolves to a non-null value). A pointer or `first_of` binding is
optional exactly when it declares `default` (also `null`); without one a
missing value fails with `missing_binding`. There is no `optional` flag. Flow
and workflow `output` may also be `fields`: an object with exactly these keys,
each any binding. All configured flow targets must exist and the reachable
graph must be acyclic; unused definitions are rejected, including flows
reachable only through `route` entries that can never be selected
(`unreachable_flow`). A review route never targets `outcome: completed`
(`review_completes_run`). A `cases` binding is a required pointer whose static
type, when known, is string or null on every path (`incompatible_route_type`).

### Conditions

A condition is a leaf with exactly one source (`binding` pointer or `first_of`
without default, or `literal`) and exactly one operator (`present`, `empty`,
`equals`, `not_equals`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `matches`,
`length`), or `all`/`any` (1–32 operands) or `not`, nested at most eight levels.
A pointer that does not resolve or resolves to `null` is absent; every operator
defines its result for absence, and absence equals `null` for `equals`/`in`.
Equality is deep JSON equality without coercion (numbers by value, booleans
distinct from numbers). Comparisons, `matches` (Python regular expression,
implicitly anchored, compiled offline) and `length` are false for incompatible
types and never raise; the runtime reports such mismatches as events.
A condition evaluates in bounded time: `matches` compares only strings of at
most 1024 characters (longer values are false, reported with reason
`value_too_long`), and the compiler walks each pattern's standard-library parse
tree and rejects unbounded backtracking with `unsafe_pattern`: an unbounded
quantifier on a group containing a backtracking unbounded quantifier, a
variable-length ambiguous part or an overlapping alternation, or estimated
choices above three independent unbounded quantifiers. Bounded quantifiers on a
group cost bound × inner work (ambiguous bounded iterations multiply);
fixed-width alternations with distinct first literals are deterministic;
possessive quantifiers do not backtrack.
Evaluation is pure and implemented once in `core/conditions.py`. Conditions
appear in `start.route`, `transition.route`, `on_unresolved.route`, step `when`,
`repeat.until` and `repeat.retry.continue_when`. They read bound data only and
never code, environment or prompt text.

### Bounded repetition

A routed flow (or a callable flow, per collection item; see
[collections](collections.md)) may declare `repeat` with `max_attempts` (2–64), a required
`until` condition, an optional callable `retry` flow with `input` bindings and
an optional `continue_when`, and `retry_input` overriding `input` keys for
attempts two and later. After an attempt, review stops (the flow follows
`on_unresolved`), failure fails the run, a true `until` stops, the last attempt
stops as exhausted; otherwise the retry flow runs (its review makes the repeated
flow `needs_review` with the retry flow's issues, its failure fails the run, a
false `continue_when` stops) and the next attempt runs. The flow result is the
last attempt and its transition is followed as usual; exhaustion is not review.
A retry flow whose input cannot be bound fails like a failing retry run; a next
attempt whose input cannot be bound, or that would start after the deadline,
fails the run before it starts. Either keeps the last attempt with
`stopped_by: failure`. Attempt entries readable at the boundary hold `attempt`,
`status`, `result` and `error` only.
Attempts and retry runs share the run deadline and step budget. A callable flow
serves at most one repeat; its `retry.input` is validated against its input
schema. `run_flow` executes one attempt.

Within a flow, pointers see `/payload` (that flow's bound input), `/metadata`,
and `/steps/<id>` (only local records). At workflow boundaries they see the
original `/payload`, `/metadata`, `/flows/<id>/result`, and for repeated or
retry flows `/flows/<id>/attempts`, not another flow's internal step records.
Routed flow input bindings, explicit collection item inputs and `retry.input`
provide cross-flow data transfer. Collection results remain nested in their
ledger; a retry flow's records appear under `/flows/<id>` and are readable only
with a default. Route conditions may read any flow that may have run before
them, but never a flow that can never have run (`unavailable_flow_reference`).
IDs are stable lowercase snake case. Missing differs from explicit null.
The compiler validates earlier-step order, flow dominance (with a virtual root
over all start candidates), defaults for results of later, reviewable or
conditional steps, `first_of` availability, known closed-schema paths and type
compatibility of every member. A required binding is also rejected when its
value is structurally missing on some path (`unavailable_value`): a repeat
attempt after the first, an attempt `error`, or a key that a flow result's
`default` lacks when that default can apply (the flow may have stopped for
review before the reader, or the defaulted pointer names a conditional step).
Step records expose `selection` only for single-choice decisions (and, needing
a default, handlers) and `kind` only for collections; `error` and
`partial_result` exist only on failure and are never bindable. A flow output
reads the first step's `selection` only with a default. Open/complex schemas,
schema-optional payload fields and all-null `first_of` members remain
runtime-checked. These checks do not prove business correctness.

### Diagnostics and static checks

Errors raise `CompilationError`; non-fatal findings are returned as
`Diagnostic(code, level: error|warning|info, location, message, field, hint)`
on `WorkflowPlan.diagnostics` and `PreparedApplication.diagnostics`, with line
and column locations; `field` is the safe key path inside the reported file.
`CompilationError.problems` lists the problems that invalidate the
configuration (the first compiler error, or every warning under `strict`) and
`.diagnostics` every finding; `reason`, `location`, `field`, `hint` and
`message` describe the first problem. `str(error)` renders one line per
problem: `<file>:<line>:<column>: <code> at <field>: <message> (hint: ...)`.
Messages name configured identifiers and authored configuration values (case
keys, allowed values), never runtime data, rejected keys, literal or default
data, or secrets. The compiler derives the allowed values of a
field from `enum`/`const` (including `anyOf`/`oneOf`), decision catalogs plus
fallback, predicate answers, handler/MCP/LLM output schemas and object outputs.
It reports `unmatched_case` (error), `uncovered_value` (warning, silenced by an
exact `default_covers`, else `default_covers_mismatch`), `case_on_unknown_type`
(info), `invalid_condition`, `unsafe_pattern`, `condition_type_mismatch` (errors),
`condition_always_false`/`condition_always_true`, `route_unreachable_entry`,
`repeat_without_retry`, `unused_llm_input`, `collection_budget` (warnings, the
latter when `max_items × worst(child)` with nested collections and item repeats
exceeds `execution.max_steps`), `run_budget` (warning: the most expensive path
from a start candidate, counting every step, repeat attempt, retry run and
collection item, exceeds `execution.max_steps`; not reported when a
`collection_budget` warning already explains it), `review_ends_run` (warning
when a flow without any review route would end the run in review and the host
would not receive that flow's projected result, info when the reviewing flow is
what the workflow output returns), `empty_text_source` (info), and
`invalid_repeat`, `repeat_budget` (errors, the latter when
`max_attempts × steps(flow) + (max_attempts − 1) × steps(retry)` exceeds
`execution.max_steps`). `prepare_application(..., strict=True)` and
`validate --strict` fail with every warning; a strict `PreparedApplication`
records `strict` and `open_application` refuses it while it carries a warning.
The public guide `docs/configuration/validation.md` lists every guarantee with
its code; `tests/test_config_guarantees.py` has one test per row and compiles
every example of the guide.

Files resolve relative to their declaring file. Conventional step discovery and
schema references stay inside their flow bundle; an explicit step `definition`,
workflow-to-flow and deployment-to-workflow references stay inside the
configuration root, after symlink resolution. A step file outside its flow
bundle keeps its own resources: its schemas resolve from the step file and stay
inside the step's directory. Inline and file
forms use one compiler. Markdown steps have exactly one instruction source:
frontmatter or a nonempty body. Schemas may be inline or files. JSON Schema refs
remain local and confined; remote, dynamic and unbounded recursive resolution is
unsupported. Exact parsed dependency bytes are frozen for revision hashing.
Declared model capabilities must satisfy each step before adapters open.

Compiler failures contain a stable reason, authored file location (validation
errors are mapped to the offending key), safe field path, message and
corrective hint. Raw Pydantic errors, rejected keys and data values are never
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
issues and a trusted handler's reported issues select issue-specific targets.
Resolve each issue through its entry or default; follow a shared target only if
all agree, otherwise use default. Missing issues use default. Absent
configuration (own or inherited) ends with review.
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
answer turn. It defaults to 8 and accepts integers 1–1024. A turn is one model
request in the conversation; provider retries and output corrections of that
request remain the same turn. The step fails with `iteration_limit_reached`
before a turn beyond this bound. The limit applies with or without tools and
resets for every step invocation. It is independent of
`execution.model_requests_per_step` (`model_request_limit_reached`), which counts
actual provider attempts, including retries and output corrections, and the
tool-call attempt budget (`tool_call_limit_reached`). `execution.max_steps`
fails with `step_limit_reached`; the run deadline, including admission waits and
a caller deadline, with `run_timeout`; a request's own timeout with
`request_timeout`. Execution defaults are `run_timeout` 900, `model_timeout`
300, `tool_timeout` 30, `max_steps` 128, `model_requests_per_step` 16 and
`tool_calls_per_step` 16. A model call to an unknown tool or with arguments that
violate its schema fails with `invalid_tool_call`.

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
Every usage object splits model requests by provider model ID (`by_model`), and
a profile's optional `pricing` adds a per-request cost estimate summed with the
same rules (see [usage and pricing](#usage-by-model-and-cost)).
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

Handler contracts (input/output schema files or inline objects and effect) are
declared under `handlers` in the deployment file and compile without host code.
Handler registrations are trusted Python objects supplied directly by the host;
YAML cannot import them. A registration for an undeclared handler is
`unknown_handler`; optional registration schemas and the effect must equal the
declaration (`handler_contract_mismatch`); a declared handler without
registration fails at `open_application` (`missing_handler_registration`).
Handler outcomes carry `selection` and `unresolved_issues` like decisions.
`StepContext` exposes `trace`, `attempt`, `collection_item` and `flow_role`.
The in-memory runtime permits read-only integrations.
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
unchanged status/issue meanings, concise reasons (aim 160 characters, at most
400), and evidence-strength semantics; the result contract accepts a `reason` of
up to 2000 characters. This does not truncate responses, change criteria or
weaken validation.
The generated predicate schema expresses true/false with answerable and unknown
with not_answerable/undetermined through complete object alternatives. Independent
Python validation retains the same answerability rules; provider schema support
alone does not establish semantic correctness.
Authored JSON Schemas are fully inlined from frozen local resources for providers,
then results are checked against the original host schema. Unsupported recursive
or dynamic schemas and unsupported provider/mode combinations fail before I/O;
there is no prompt-only structured-output fallback. SDK retries are disabled.

Invalid structured output of a decision or schema step, in native and tool output
mode (a JSON Schema violation, a decision contract violation or unparseable
JSON), is corrected up to the model profile's `output_retries` (0–8, default 1;
per step through a profile override): the next request returns the validator's
content-free problems about the model's own output to the model only. Each
correction is a model request against `model_requests_per_step`, counted in
`usage.output_retries`, and not a `max_iterations` turn. Exhausted corrections
fail with `invalid_output` and a `reason`. A length stop, refusal or provider
error is never corrected. Model `options.max_tokens` defaults to 32768 (reasoning
included); profile `request_timeout` defaults to 300 seconds.

MCP profiles use current maintained SDK transports for Streamable HTTP or stdio,
one declared bounded catalog and read-only effects. Discovery must match the
declared names and schemas (`tool_catalog_mismatch` otherwise). A result with
`isError` fails with `tool_error`, and one larger than `output_limit_bytes`
(default 1 MiB) with `tool_output_limit_exceeded`. Validate and freeze arguments before authorization and
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

Model and MCP profiles accept `retry` with `max_attempts` (1–8, default 4),
`initial_delay_seconds` (0–60, default 1), and `max_delay_seconds`
(0–300, default 30, at least the initial delay). Attempts include the first call.
The default therefore sends the first request plus up to three retries.

Only explicitly classified completed HTTP responses qualify: 408 and 504
(`request_timeout`), 429 (`rate_limited`), 500 and 502 (`dependency_failure`),
503 and 529 (`dependency_overloaded`); for models also a connection failure
without a response. A generic error's `retryable` flag alone never authorizes
re-execution. Client-side timeouts, cancellation, HTTP 400/422 (`request_rejected`),
context window overflow (`context_limit_exceeded`), 401/403/404,
`output_limit_reached`, `output_refused`, limits, tool errors, invalid tool calls
and handler failures are terminal. A retry refused by the step's request or tool
call limit reports the transient failure it would have retried (`retryable`); a
refused output correction reports the `invalid_output`. The runtime does not retry
whole workflows, flows, agents or tool sessions. MCP calls remain read-only and
each attempt repeats argument authorization and budget reservation. SDK
automatic model retries remain disabled; invalid output is corrected only through
`output_retries`.

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

## Usage by model and cost

The step budget reserves each model attempt for the provider model ID it is sent
to, together with the profile's pricing. Usage keeps unknown counts unknown:
an attempt without a report counts as a request with unknown tokens. `by_model`
(`requests`, `input_tokens`, `cached_input_tokens`, `output_tokens`,
`reasoning_tokens`) is present whenever a model request was made and its
requests sum to `model_requests`.

`pricing` (`currency: USD`, `input_per_million`, optional
`cached_input_per_million`, `output_per_million`, `reasoning_billed_as:
output|input`, optional `long_context` with `threshold_input_tokens` and tier
prices, optional `reference_model`) is strict configuration read as exact
decimals. A request costs uncached input × input price + cached input × cached
price (input price when absent) + output × output price, with reasoning tokens
(a subset of output) at the input price when `reasoning_billed_as: input`. The
long-context tier replaces all prices of a request whose input tokens exceed the
threshold. A count the formula needs but that was not reported leaves the cost
unknown. Costs sum unrounded; results expose `cost` rounded to six decimals,
`cost_complete`, `currency` and `reference_model`, and `cost: null` with
`cost_complete: false` when any contributing request is unknown or unpriced. Cost
fields are omitted when no priced request contributed. An override's `pricing`
replaces or (with `null`) clears the profile's; an override with another `model`
does not inherit it. `explain` and `doctor` show the configured pricing.

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
parentage in embedded applications as well as the CLI. Labels come from resolved
configuration; values that cannot be exported safely are dropped with one
warning event, never failing activation. Spans are named from configuration
IDs: `workflow <id>`, `flow <id>` (` [item <index>]` inside a collection,
` #<attempt>` from the second attempt), `step <id> (<type>)`, `chat <model>` and
`execute_tool <tool>`; a name that is not an allowlisted label is omitted, never
replaced by a runtime value. Flow spans carry the attempt, maximum
attempts and role (`routed`, `callable`, `retry`); step spans carry
`foliqant.step.kind`; skipped steps have a span
with `foliqant.step.skipped`. Model spans carry the reported OTel GenAI
`gen_ai.usage.*` counts (input, output, cache read, cache creation, reasoning),
`gen_ai.response.model` when it is the configured model or a dated snapshot of
it, `foliqant.usage.cost` when the request's estimate is known, the allowlisted
`gen_ai.response.finish_reasons`, `foliqant.request.attempt` (1-based),
`foliqant.request.output_retry: true` on a correction request, and, for a
`length` stop with both counts reported,
`foliqant.response.reasoning_consumed_budget`. Every failed model request, tool
call, step, flow and workflow span has ERROR status with `error.type` set to the
content-free failure code; `needs_review` is not an error, and a request retried
to success leaves ERROR spans for its failed attempts under an OK step. The request duration metric
classifies the stop inside the recorded region, so its `error.type` equals the
step's failure code. Fixed events report `route.selected`,
`repeat.stopped`, `step.skipped` (condensed condition: pointers and operators
only), `handler.review` and `condition.type_mismatch`; `condition.evaluated`
is opt-in through `telemetry.conditions`. `ExecutionResult.execution.trace`
returns the run span IDs. `RuntimePlugins(tracer_provider=...)` hands a
host-owned provider to the runtime: spans join it, the runtime creates no
provider, exporter or metrics of its own, never installs globals (combining it
with `install_global_telemetry` is `invalid_configuration`) and never shuts it
down; runtime spans and events are content-free at creation so they match the
exported form. Each streamable-HTTP MCP request carries the W3C
carrier of its own tool call as headers, derived from that request's `_meta`
so concurrent calls never exchange it. Flow duration and step metrics retain flow identity with
low-cardinality labels only. Lifecycle and routing log events use the active
trace IDs and the execution ID without recording business data. OTLP requests do not follow redirects and do
not log or consume unbounded collector response bodies. Shutdown failures are
reported safely without replacing a completed business result.

## Entry points, example and acceptance

The package exposes embedded composition plus offline `init`, `validate`
(`--strict`), `explain` (`--format json|mermaid|dot`, backed by
`foliqant.explain(prepared, workflow)`; `--all` renders every workflow, for
mermaid/dot as one Markdown document from `foliqant.graph.render_document`
with a `## <workflow>` section each holding its start, output, fenced diagram
and diagnostics after one legend; `--legend` adds the legend to a single
graph; `--output PATH` writes the rendering and `--check` compares it
instead, exiting `1` when stale), `doctor`, foreground `run` and explicit
`evaluate` commands. Graph labels show authored condition operands, which are
configuration like case keys (`equals found`, `in [a, b]`, `matches /…/`,
`present=false`), and complete repeat annotations (`repeat ≤ 2 until status
equals found`); literal bindings and default values are never shown, and
telemetry keeps operand-free condensed conditions. Evaluation
check/replay modes are offline; ordinary evaluation executes configured targets.
Offline commands do not open model/MCP endpoints. Commands default to
`config/settings.yaml` relative to the current directory; `--config PATH`
selects another explicit file without parent discovery. `init` atomically creates
a minimal local-model summary flow, its Markdown step, example envelope,
`config/.env.example` with `MODEL_ID`/`MODEL_BASE_URL`, and usage instructions.
Validation is offline; executing the generated flow requires the configured
endpoint and provider extra. Init does not install packages or start a backend.
Standard output carries one safe JSON object (the result, or the failure
status with `error`, `problems` and `diagnostics`) or the requested graph text;
standard error carries readable text, one rendered line per configuration
problem plus a summary. Exit codes: `0` success (including `needs_review`), `1`
gold mismatch or stale `explain --check` output, `2` invalid arguments, input or
configuration, including run failures with `invalid_configuration`,
`invalid_input`, `unauthenticated`, `forbidden`, `not_found`, `conflict`,
`model_not_found`, `request_rejected` or `context_limit_exceeded`, `3` missing
optional dependency, `4` runtime failure, `5` temporary runtime failure the
boundary marked `retryable`, `130` interruption. There is
no durable lookup/cancel operation or packaged HTTP server.

The runnable HTTP example may use a small maintained ASGI library to decode one
bounded request, invoke the in-memory application and return the terminal result.
A failed run returns a 5xx with the result body (503 for a `retryable` failure or
`capacity_exceeded`, 504 for `request_timeout`/`run_timeout`, 500 otherwise);
invalid input returns 400. It must state that authentication, authorization, persistence, idempotency,
background execution, recovery and production hosting are the embedding
application's responsibility. It may not create detached work or expose accepted,
lookup or cancellation resources.

Acceptance families require success and failure evidence:

| Requirement / capability | Required evidence |
| --- | --- |
| `PACKAGE-CONTRACTS` | Strict envelopes/results, runtime reason/strength and one closed output shape, substantive/null/collection boundaries, subject occurrence, independent optional identity, W3C carrier, generated schema drift |
| `PACKAGE-COMPILER` | Safe deterministic bundle compilation, graph/dataflow/schema checks, condition/route/repeat checks and diagnostics, duplicate/path escape rejection and no endpoint I/O; one located test per documented structural guarantee and compiled guide examples |
| `PACKAGE-RUNTIME` | End-to-end in-memory decision/LLM/MCP/handler execution across sequential flows, routed start, conditional routes and steps, bounded repeat with retry flows, bounded concurrency, cancellation and concurrent state isolation |
| `PACKAGE-MCP` | Current SDK HTTP/stdio behavior, OAuth isolation, declared catalog/schema checks, budgets, authorization and protected context propagation |
| `PACKAGE-PRIVACY` | Secret/PII sentinel checks across safe logs and optional observations; telemetry failure remains nonfatal |
| `PACKAGE-DX` | Locked install, public schema drift, CLI/example execution, documentation/skill checks and independent review |
| `PACKAGE-EVALUATION` | Isolated-step parity, immutable gold, exact/set/custom scoring, confusion/multilabel counts, failure/skip denominators, detailed private reports, offline validation/replay, startup independence and bounded cancellation |

Tests use synthetic inputs and protocol fixtures. They do not claim live model
accuracy, application security, durable recovery or production qualification.

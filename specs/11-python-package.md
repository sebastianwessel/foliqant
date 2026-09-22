# Foliqant Python package

Status: active implementation specification. The project owner requests one reusable
`foliqant` package, in-memory execution, step and pipeline evaluation, and runnable
business examples. Model training and curation remain a separate development tool.

## Scope and authority

Foliqant is an installable Python 3.12 package built with PydanticAI, Pydantic,
the maintained MCP SDK and uv. It compiles local workflow bundles and runs one
bounded asynchronous invocation in memory. A workflow owns a graph of named sequential flows. Each flow contains decision,
LLM, read-only MCP or trusted handler steps. The graph, routes,
model aliases, tool allowlists and output bindings come from reviewed local
configuration; model output cannot invent executable structure or authority.

The package does **not** own persistence, queues, background workers, child jobs,
application authentication, HTTP/Redis transports, admin APIs or cloud
deployment. It does not promise recovery after process loss or caller return.
A small runnable HTTP example may adapt one request to the public in-memory API,
but that example is not a transport framework or package-level service contract.

Runtime decision assessments are defined in `foliqant.contracts.decisions`.
Shared question types and separate training annotations live in `foliqant.decisions`.
Runtime settings and responses expose one current format without version fields,
compatibility loaders or migration aliases. Model training, data curation,
paid inference, customer data and publishing remain outside the library scope.

## Architecture and reuse

`src/foliqant/` belongs to the root project. `model/` has its own project and environment. The library's runtime dependencies exclude
training, curation, MLX, Torch, Transformers, datasets and downloaded model
weights. It never imports `foliqant_model`.

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
| `evaluation/` | Immutable golden suites, isolated/full-pipeline checks and variant reports | Public result contracts and standard-library scoring; no model-training imports or automatic judge calls |

No arbitrary dotted import, executable plugin or environment interpolation is
accepted from workflow YAML. Hosts explicitly register handlers and authorization
hooks. Workflow files and local schema resources are read once during preparation;
request-time execution performs no filesystem or schema-network lookup.

The package root lazily exports `Envelope`, `ExecutionResult`, `Identity`,
`PreparedApplication`, `RuntimePlugins`, `WorkflowApplication`,
`load_environment`, `prepare_application` and `open_application`. Embedding code
uses `load_environment` only when selecting an additional environment location;
importing the package alone does not load configuration or initialize clients.

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
is inferred only for one flow; multiple flows require an explicit start.
A flow instance declares named `input` bindings, required `transition`, optional
`on_unresolved`, and optional `definition` (inline or file). Omitted definition
resolves `<flow-id>/flow.yaml` beside workflow.yaml. A flow definition declares
an optional input schema, output binding and a nonempty ordered `steps` list.

A step list entry is an ID or `{id, definition?}`. Without an explicit definition,
resolve exactly one of `<id>.step.yaml`, `<id>.step.md`, `<id>/step.yaml`, or
`<id>/step.md` beside flow.yaml. Missing or ambiguous candidates fail; there is
no extension precedence. Explicit inline definitions and file references use
the same compiler and support reuse. Unlisted step files are not executed or
loaded. File discovery never determines business order, routes or data access.

The four step kinds are:

- `decision`: one or several native questions and named sources, using an
  inherited or explicitly configured model; semantic validation is mandatory.
- `llm`: named inputs, instructions, optional prompt template, text or JSON Schema
  output and an optional allowlist of MCP tools.
- `mcp`: one configured server/tool with explicit literal or pointer arguments.
- `handler`: one trusted host registration with frozen input/output schemas.

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
internal step records. Flow input bindings provide the sole cross-flow data
transfer. IDs are stable lowercase snake case. Missing differs from explicit null.
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
Do not derive machine-readable issues by parsing rationale text. Training
annotation contracts and previously generated artifacts are outside runtime
execution and remain deferred; runtime needs no format-version negotiation.

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
SDK must run through an owned bounded executor with an SDK timeout. A started
blocking call retains its capacity until it actually finishes even if the caller
stops waiting; cancellation never proves a remote operation stopped. Waiting
cancelled calls must not start later. Do not create per-request event loops, call
`asyncio.run` inside runtime code, use blocking sleeps or launch unbounded tasks.

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
one stable safe error and a nonzero exit. No `migrate`, queue `worker`, durable
lookup/cancel or packaged HTTP server command belongs to this scope.

The runnable HTTP example may use a small maintained ASGI library to decode one
bounded request, invoke the in-memory application and return the terminal result.
It must state that authentication, authorization, persistence, idempotency,
background execution, recovery and production hosting are the embedding
application's responsibility. It may not create detached work or expose accepted,
lookup or cancellation resources.

Acceptance families require success and failure evidence:

| Requirement / capability | Required evidence |
| --- | --- |
| `PACKAGE-CONTRACTS` | Strict envelopes/results, runtime reason/strength and training-contract isolation, substantive/null/collection boundaries, subject occurrence, independent optional identity, W3C carrier, generated schema drift and no model-tooling import |
| `PACKAGE-COMPILER` | Safe deterministic bundle compilation, graph/dataflow/schema checks, duplicate/path escape rejection and no endpoint I/O |
| `PACKAGE-RUNTIME` | End-to-end in-memory decision/LLM/MCP/handler execution across sequential flows, bounded concurrency, cancellation and concurrent state isolation |
| `PACKAGE-MCP` | Current SDK HTTP/stdio behavior, OAuth isolation, declared catalog/schema checks, budgets, authorization and protected context propagation |
| `PACKAGE-PRIVACY` | Secret/PII sentinel checks across safe logs and optional observations; telemetry failure remains nonfatal |
| `PACKAGE-DX` | Locked install, public schema drift, CLI/example execution, documentation/skill checks and independent review |
| `PACKAGE-EVALUATION` | Isolated-step parity, immutable gold, exact/set/custom scoring, confusion/multilabel counts, failure/skip denominators, detailed private reports, offline validation/replay, startup independence and bounded cancellation |

Tests use synthetic inputs and protocol fixtures. They do not claim live model
accuracy, application security, durable recovery or production qualification.

## Dependency evidence

[Current dependency research](research/workflow-service-dependencies.md) records
the reviewed package APIs; `uv.lock` is the exact dependency authority.
Use the official MCP SDK rather than a second wire implementation. PydanticAI
structured output still requires independent host JSON Schema validation. No
PydanticAI Harness, PURISTA, Voyage or FastMCP dependency is part of this scope.

## Evaluation and isolated execution

`WorkflowApplication.run_step(workflow, flow_id, step_id, envelope)` executes exactly one
configured step using the same runtime, executor, budgets, caller context and
adapter validation as full execution. Payload contains the explicitly resolved
input/source/argument keys for that step. It does not execute upstream steps,
follow routes or apply the pipeline's input/output projection. It returns only
the selected flow/step record and its successful/review result as payload; failed runs
retain their accepted resolved-input payload. Both retain the original plan
revision. Unknown step names and missing/extra inputs fail before model I/O.

`run_flow(workflow, flow_id, envelope)` executes one flow with its already-resolved
input and validates the flow input schema. It applies that flow's output projection,
but no upstream input bindings, workflow projections or boundary transitions.
Both isolation APIs retain original workflow identity and revision, caller/tool
policy and finite limits; they never silently run dependencies.

Each executed step returns measured monotonic `elapsed_seconds` and its own
attempt/token `usage`; skipped or unmeasured steps have no measurements. Failed
requests remain charged. Missing token observations remain unknown. Cancellation
propagates rather than producing fabricated completed measurements.

The optional `foliqant.evaluation` module evaluates explicit ground-truth cases
against an async pipeline or isolated-step callable. A case holds an input and
named expectations at RFC 6901 result paths. Exact equality is type-sensitive;
set comparison is explicit for unordered labels, not an implicit coercion.
Missing output, skipped steps and execution failures remain visible in coverage
and denominators. Custom scorers must be explicit host functions. No evaluator
calls an LLM judge, discovers endpoints, alters prompts or accepts its own output
as ground truth.

The module ships in the standard wheel and needs no evaluation extra or new
dependencies. Its Python entry points are `evaluate` and `compare_variants`, with
`EvaluationSuite`, `EvaluationCase`, `Expectation`, `EvaluationVariant`,
`RegisteredScorer` and `MetricSpec`. Immutable dataclasses remain the scoring
representation. The optional JSON boundary converts into those same values;
there is no second scheduler or evaluation framework.
Source-span comparison uses independently authored `input_path`, `required`, and
`allowed` gold. Ranges use Unicode code-point offsets with an exclusive end;
required is nonempty and nested inside allowed, both within the input string.
Candidates must match a verbatim occurrence containing required and contained
within allowed. No normalization, semantic judge or prediction-derived gold is
implied. Invalid ranges and nonstring/missing source pointers fail offline.

Set comparison ignores top-level array ordering and duplicates while retaining
JSON type distinctions. Custom scorers are registered async functions with an
explicit revision. `evaluate` defaults to one worker and a 300-second per-case
timeout including scoring; results retain suite order. `compare_variants` runs
variants sequentially. Hosts own ground truth and holdout selection. The library
neither certifies holdout separation nor optimizes prompts.

`repeat` defaults to 1 and requires a positive integer, excluding booleans.
Each source case runs that many times in case-major order. Original case IDs
remain unchanged; `repetition` identifies attempts from 1 through `repeat`.
`case_count`/`source_case_count` describe distinct authored cases and
`attempt_count` describes executions. Assertion, outcome, and classification
counts cover attempts; equal repetitions preserve equal weighting per source.
Repeated outcomes never become independent gold. Work is scheduled lazily with
the same bounded concurrency and cancellation ownership as ordinary evaluation.

Evaluation defaults to `evaluation/dataset.json` beside the `config/` directory.
An explicit `evaluation.dataset` overrides it with a literal path relative to the
settings file (absolute paths also work). Preparation, application startup, `validate` and `doctor` never stat or
open that dataset. The field is excluded from the runtime configuration digest;
changing private gold locations does not revise execution. Gold need not be
packaged or deployed. The reference is not an environment-expansion field.

The strict JSON dataset has `name`, `revision` and named `suites`.
Each suite declares a configured `workflow`, optional isolated `flow` and `step`,
optional `metrics`, and `cases`. `step` requires `flow`. Without either target,
run the whole workflow; with only flow, run that flow; with both, run one step. Cases may be a nonempty inline array or a path to a JSON
case array, relative to the main dataset file (absolute paths also work). Mixed
inline/separate step and pipeline files are supported through the same loader;
nested references are not. Every case contains `id`, an envelope `input`, and
named `expectations` (`path`, explicit `expected`, optional `comparison`
`exact|set|source_span`). Step inputs are already-resolved inputs, not upstream pipeline
inputs. File configuration never imports scorer functions or executable code.
The generated `evaluation-dataset.schema.json` describes this boundary. Loading
rejects duplicate keys, duplicate IDs/names, nonfinite values, invalid pointers,
unknown target workflows/flows/steps, invalid gold catalogs and over-limit files.
Static checks cover known result roots/flow/step references, not arbitrary dynamic
payload-field existence or the business correctness of ground truth.

`foliqant evaluate --check` loads and validates only. Ordinary `evaluate` opens
the configured application and runs suites sequentially with default one worker
and a 300-second case deadline, still subject to normal runtime limits.
`--max-concurrency` and `--timeout` change evaluator bounds only. Explicit
`--replay REPORT` scores saved complete results without constructing clients,
resolving credentials or calling models/tools. Replay requires matching dataset
identity, suite/case identity and order, input values, configuration/workflow
revisions and requested workflow/flow/step target, including all-error runs;
revised gold is permitted. Replay cannot measure a changed prompt. Its suite wall
time describes rescoring; saved case latency, step usage and measurements describe
source execution. Repetitions are inferred from the saved artifact unless an
explicit matching count is supplied. Each saved attempt is rescored exactly once.
Missing saved outcomes are rejected; recorded execution errors remain
errors. No replayed failure triggers inference.

The CLI writes a full report atomically to a new owner-readable file;
it never overwrites. Default destination is a unique
`.foliqant/evaluations/report-TIMESTAMP.json` under the config directory;
`--output` selects another new file. Stdout contains counts/status/path only.
Reports contain private input, gold, complete public execution results and
per-check actual/expected/presence/reason. Public reasons and evidence-strength
assessments are retained; internal provider reasoning is not collected.
Authored synthetic example gold is committed under each example's `evaluation/`
directory. Real customer gold, downloaded/training corpora and generated results
remain ignored and must not be uploaded as ordinary CI artifacts. Each dataset
file is limited to 64 MiB; report publication/replay share a 256 MiB bound.
Cancellation joins owned tasks and returns no fabricated complete report. Exit
codes are 0 for passing checks, 1 for disagreement, 2 for invalid config/data,
3 for missing dependencies, 4 for runtime failures (even if expected by gold),
130 for interrupt. The repository `scripts/evaluate` forwards to this command.

`evaluate --compare CANDIDATE --baseline BASELINE` and Python `compare_reports`
compare private saved reports offline without loading configuration or clients.
Require matching inputs, gold, scorers, metric catalogs, targets and complete
attempt identities; configuration/variant revisions may differ. Recompute
aggregates from validated observations. Retain mixed per-case changes and
operational failures. Completion and intended review have equal operational
rank; authored assertions determine correctness. Replay reports cannot establish
latency improvements. Comparison output is descriptive, not significance or
automatic acceptance policy.

`group_report` accepts a detailed Python report and an explicit input pointer.
Groups use scalar, type-sensitive keys, separating missing from null; retain
first-observed order and complete source repetitions. Reuse existing metrics
and measured latency/usage; never infer groups, rerun scorers or call endpoints.
Saved artifact loading is not part of this grouping API.

Metrics use explicit label catalogs and gold expectations at their declared
result pointer. Classification catalogs contain strings and may explicitly
include JSON null. If null is declared, null gold is valid and an actual null is
an observed classification outcome in matrix/per-label counts; otherwise actual
null is an abstention and null gold is invalid. Missing/skipped/error observations
never become null. Multilabel catalogs remain string-only. Classification matrices
have expected rows and predicted columns in catalog order. Multilabel reports
include per-label TP/FP/FN/TN and exact-set accuracy. Reports distinguish support (matching gold), excluded cases
(no matching gold), valid observed outputs, null abstention, missing, skipped,
errors and invalid predictions. Accuracy is correct/support, coverage is
observed/support; zero support gives null. Confusion and per-label counts cover
only valid observations and are read alongside coverage. Wrong labels/types do
not become valid predictions. No acceptance thresholds are inferred. An exact
array assertion still preserves ordering, independently of set-based metrics.

Each label also reports observed-only precision, recall, and F1. Precision and
recall are null on a zero denominator; F1 uses `2TP/(2TP+FP+FN)` and is null when
that denominator is zero. `micro` pools label counts. `macro` averages the whole
declared catalog for each measure, yielding null if any label has an undefined
value for that measure. Coverage and unobserved outcomes remain separate rather
than inventing label predictions. With repetition, `support`/`excluded` count
attempts and `source_support`/`source_excluded` identify authored cases.

Reports identify flow and local step separately; aggregate step measurements by
`(flow, step)`, never a colliding local name. Expectations use
`/flows/<flow>/steps/<step>/result` or a public flow projection. Flow-only checks
are attributed to the flow, not invented step records. Workflow, flow and step
usage are separate views of the same calls, not additive across levels.

Step summaries also count `model_selected_cases` and `fallback_selected_cases`.
`fallback_rate` is fallback selections divided by all observed step records,
including skipped/error records, or null with no records. Repeated attempts
count separately. These are policy-use measurements, not accuracy. Replay and
grouping preserve the selection origin from the validated public result.

Report and step summaries include measured latency count, unavailable count,
minimum, median, p95, and maximum in seconds. The median averages middle values
for an even sample; p95 uses nearest rank `ceil(0.95*n)`. No measured samples
means null statistics. Suite latency uses measured execution durations, including
errors; replay wall time remains replay measurement, not source model latency.
Usage summaries preserve observed/unknown counts and known totals separately:
the complete total is null when any observation is unknown, and even known-total
is null when there were no known observations. No unknown count becomes zero.

Every runnable example includes executable evaluation with explicit ground
truth. Model examples default to clearly labeled scripted wiring checks and
require `--live` for local-model measurements. Step suites use `run_step`, flow
suites use `run_flow`, and full suites use `run`. The HTTP wrapper reuses the same workflow suite through its
ASGI boundary; it does not duplicate business logic. Failed assertions result in
a nonzero evaluation command status. Example evaluators save a new private
report by default and print counts and its path; core Python evaluation remains
in memory unless the host explicitly writes it. No example's expected values are generated
from its observed response. Synthetic checks do not establish population accuracy.
Support fixtures cover each declared queue label and review, including English
and German inputs with unchanged English category keys. Committed synthetic JSON
gold is authored independently of scripted responses and available immediately
after cloning. Example reports stay private; gold is never required at startup.

Reports identify the suite and its content fingerprint, the variant/configuration
revision supplied by the caller and observed workflow revisions. They report
check/case pass rates, execution failures and review rates, measured latency and
available token usage. Compare prompt variants on the same suite, sequentially
by default; opt-in concurrency is bounded. Cancellation propagates and joins
evaluator-owned cooperative tasks; it does not prove remote or blocking work
stopped. Python reports omit business values unless `include_details=True`;
the explicit evaluation CLI enables details for its private report. Ordinary
application execution never persists evaluation data or results.

Prompt optimization means comparing explicitly authored variants using these
metrics, then validating a selected variant on a separate untouched holdout.
A small synthetic example is a wiring test, not a financial accuracy estimate.
No confidence/calibration, statistical significance or production reliability
claim follows from a pass rate alone. The existing model-training evaluation and
calibration toolchain remains separate and unchanged.

## Prompt security and cache acceptance

Trusted authored instructions and compiler-generated question definitions are
separate from source data. Bound inputs, tool output and previous model results
remain lower-trust data, even if labeled policy or metadata. JSON serialization
prevents structural delimiter breakout; neither encoding nor instruction text
provides a semantic security guarantee. Legitimate customer imperatives remain
evidence of intent; embedded requests to change criteria, reveal instructions,
expand permissions or choose a forced result must not control execution.

Fresh model conversations are the default for each step. Tool use within a step
retains valid SDK message ordering. Email chronology is business input, not a
persistent AI transcript. Only explicitly selected context is forwarded; previous
rationales are claims, not verified facts. No extra screening model or automatic
history/summarization store is introduced.

Host validation enforces schemas, IDs, cardinality and permitted operations;
it cannot prove a schema-valid answer is correct. Inference cannot change tool
allowlists or workflow topology. High-impact host actions require their own
business checks rather than relying solely on valid JSON or evidence strength.

Cache optimization preserves stable instructions, catalogs and schemas before
changing data where evaluation supports that layout. Keep source chronology and
permissions unchanged. Provider cache controls belong in provider adapters only
when measured; no semantic answer cache, padded instructions or per-request
random delimiter scheme is introduced. Cache hits alone do not prove accuracy,
latency improvement or privacy isolation.

Compare candidate layouts on identical family-separated EN/DE cases, including
benign imperative text, direct source overrides, fake delimiters/roles, poisoned
derived results, conflicts and multiple intents. Measure false rejection and
business accuracy before token/cache/latency benefits. Preserve source results
and errors privately, serialize local inference, and stop after timeout until
backend state is verified. Baseline retention is required when evidence is
inconclusive; synthetic checks cannot establish enterprise reliability. Model
curation/training/fine-tuning are deferred and unchanged by this refactor.

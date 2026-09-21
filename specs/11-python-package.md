# Foliqant Python package

Status: active implementation specification. The project owner requests one reusable
`foliqant` package, in-memory execution, step and pipeline evaluation, and runnable
business examples. Model training and curation remain a separate development tool.

## Scope and authority

Foliqant is an installable Python 3.12 package built with PydanticAI, Pydantic,
the maintained MCP SDK and uv. It compiles local workflow bundles and runs one
bounded asynchronous invocation in memory. Supported work is deterministic
decision, LLM, read-only MCP, trusted handler and finish steps. The graph, routes,
model aliases, tool allowlists and output bindings come from reviewed local
configuration; model output cannot invent executable structure or authority.

The package does **not** own persistence, queues, background workers, child jobs,
application authentication, HTTP/Redis transports, admin APIs or cloud
deployment. It does not promise recovery after process loss or caller return.
A small runnable HTTP example may adapt one request to the public in-memory API,
but that example is not a transport framework or package-level service contract.

Existing native decision contracts ship in `foliqant.decisions` without
changing their wire shapes or semantic validator. Model training, data curation,
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

One invocation returns one `ExecutionResult` with the accepted `payload`, accepted
`metadata`, `decisions` keyed by step ID and terminal `execution` information.
Statuses are `completed`, `needs_review`, `failed` or `cancelled`. Results contain
the caller's accepted business data, validated step outputs, measured usage and
only canonical safe errors. Error and diagnostic fields never contain raw exception
text, stack locals, credentials or unrelated SDK content. Technical
failure preserves the accepted input and completed step records. Caller cancellation
propagates without a returned result.
Known business uncertainty produces `needs_review`; it is not a technical failure.

## Deployment and authoring

Version is exactly integer `1`, never boolean `true`, a string or a float.
Version 1 deployment configuration contains `workflows`, optional `models`,
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

A workflow bundle contains `workflow.yaml` with either an inline `steps` mapping
or `steps/*.yaml`/`steps/*.md`, never both. Inline step keys own identity; nested
step names are rejected. Input and structured-output schemas may be inline
objects or confined file references. Both forms use the same schema validator,
frozen resource registry and execution plans. Markdown model steps have exactly
one instruction source: frontmatter instructions or a nonempty body, never both.
IDs are stable lowercase snake case. Safe
YAML parsing rejects duplicates, aliases and custom tags. JSON Schema references
are local and confined to the bundle; remote, dynamic and unbounded recursive
resolution is unsupported. Revisions hash the exact bytes already parsed for all
workflow dependencies.

Supported step kinds are:

- `decision`: a shared native question or questions, explicit inputs and an
  optional configured model alias; complete semantic validation is mandatory.
- `llm`: explicit bound inputs, instructions, model alias, text or confined JSON
  Schema output and an optional allowlist of MCP tools.
- `mcp`: one configured server/tool with literal or explicit pointer arguments.
- `handler`: one trusted host registration with frozen input/output schemas.
- `finish`: one explicit terminal status and optional final payload projection.

Every operation declares `next`, or a decision declares exhaustive `on_answer`
routes. Only `finish` is an authored success terminal; missing unresolved routes
still stop safely for review. Every nonterminal route is statically declared. Bindings use tagged literals or
RFC 6901 pointers into payload, metadata and earlier step results. Missing is
distinct from explicit null. Compilation proves referenced steps dominate uses,
routes target known nodes and the reachable graph terminates within runtime
bounds. Step file order has no execution meaning. Compile-time checks reject
provably missing closed-schema paths, scalar traversal and disjoint JSON types
at typed MCP/handler boundaries. Open or complex schemas remain runtime-checked;
validation is not a proof of business correctness or model accuracy. Declared
model capabilities must satisfy each referenced step's requirements.

Compiler failures contain a stable reason, authored file location, safe field
path and corrective hint. Raw Pydantic errors and authored values are never
rendered. CLI `validate` and Python `prepare_application` share this compiler;
there is no parallel validation engine or network preflight.

## In-memory execution

`WorkflowRunner.run` validates and accepts one envelope, acquires shared local
admission, then creates invocation-local state, budgets and task context. It walks
the immutable plan until a finish, review, safe failure or cancellation and maps
the internal result through the strict public result contract. No accepted state,
checkpoint, idempotency record or result survives process loss or is queryable
after the call returns.

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

PydanticAI executes native decisions and text/schema LLM steps. Native decisions
use the shared strict output contract plus independent semantic validation.
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
scope/link attributes and status text. Use configured nonsecret workflow, step,
model, provider and tool labels only. Missing usage stays absent. Observation and
export failure cannot replace a business result. Bootstrap owns installation and
bounded shutdown; embedded hosts are never silently given a new global provider.

## Entry points, example and acceptance

The package exposes embedded composition plus offline `init`, `validate`,
`explain`, `doctor` and foreground `run` commands. Offline commands do not open
model/MCP endpoints. `validate`, `explain`, `doctor` and `run` use `foliqant.yaml`
in the current directory unless `--config PATH` is supplied; no parent-directory
search or endpoint discovery occurs. `init` creates a model-free workflow and a
minimal version/workflows configuration and inline finish step; empty model and
MCP maps use typed defaults.
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
| `PACKAGE-CONTRACTS` | Strict envelopes/results, independent optional identity, W3C carrier, generated schema drift and no model-tooling import |
| `PACKAGE-COMPILER` | Safe deterministic bundle compilation, graph/dataflow/schema checks, duplicate/path escape rejection and no endpoint I/O |
| `PACKAGE-RUNTIME` | End-to-end in-memory decision/LLM/MCP/handler/finish execution, bounded concurrency, cancellation and concurrent state isolation |
| `PACKAGE-MCP` | Current SDK HTTP/stdio behavior, OAuth isolation, declared catalog/schema checks, budgets, authorization and protected context propagation |
| `PACKAGE-PRIVACY` | Secret/PII sentinel checks across safe logs and optional observations; telemetry failure remains nonfatal |
| `PACKAGE-DX` | Locked install, public schema drift, CLI/example execution, documentation/skill checks and independent review |
| `PACKAGE-EVALUATION` | Isolated-step parity, immutable ground truth, exact/set/custom scoring, failure/skip denominators, measured usage, privacy and bounded cancellation |

Tests use synthetic inputs and protocol fixtures. They do not claim live model
accuracy, application security, durable recovery or production qualification.

## Dependency evidence

[Current dependency research](research/workflow-service-dependencies.md) records
the reviewed package APIs; `uv.lock` is the exact dependency authority.
Use the official MCP SDK rather than a second wire implementation. PydanticAI
structured output still requires independent host JSON Schema validation. No
PydanticAI Harness, PURISTA, Voyage or FastMCP dependency is part of this scope.

## Evaluation and step isolation

`WorkflowApplication.run_step(workflow, step_id, envelope)` executes exactly one
configured step using the same runtime, executor, budgets, caller context and
adapter validation as full execution. Payload contains the explicitly resolved
input/source/argument keys for that step. It does not execute upstream steps,
follow routes or apply the pipeline's input/output projection. It returns only
the selected step record and its successful/review result as payload; failed runs
retain their accepted resolved-input payload. Both retain the original plan
revision. Unknown step names and missing/extra inputs fail before model I/O.

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

The module ships in the standard wheel and needs no evaluation extra. Its public
entry points are `evaluate` and `compare_variants`, with `EvaluationSuite`,
`EvaluationCase`, `Expectation`, `EvaluationVariant` and `RegisteredScorer`.
These are Python dataclasses, not CLI commands or a file-based suite loader.
Set comparison ignores top-level array ordering and duplicates while retaining
JSON type distinctions. Custom scorers are registered async functions with an
explicit revision. `evaluate` defaults to one worker and a 300-second per-case
timeout including scoring; results retain suite order. `compare_variants` runs
variants sequentially. Hosts own dataset loading, report persistence and holdout
selection. The library neither certifies holdout separation nor optimizes prompts.

Every runnable example includes executable evaluation with explicit ground
truth. Model examples default to clearly labeled scripted wiring checks and
require `--live` for local-model measurements. Step suites use `run_step`; full
suites use `run`. The HTTP wrapper reuses the same workflow suite through its
ASGI boundary; it does not duplicate business logic. Failed assertions result in
a nonzero evaluation command status. No example's expected values are generated
from its observed response. Synthetic checks do not establish population accuracy.

Reports identify the suite and its content fingerprint, the variant/configuration
revision supplied by the caller and observed workflow revisions. They report
check/case pass rates, execution failures and review rates, measured latency and
available token usage. Compare prompt variants on the same suite, sequentially
by default; opt-in concurrency is bounded. Cancellation propagates and joins
evaluator-owned cooperative tasks; it does not prove remote or blocking work stopped. Reports contain no automatic raw input/output dumps.
Caller-owned expected values and any explicitly retained results remain private.

Prompt optimization means comparing explicitly authored variants using these
metrics, then validating a selected variant on a separate untouched holdout.
A small synthetic example is a wiring test, not a financial accuracy estimate.
No confidence/calibration, statistical significance or production reliability
claim follows from a pass rate alone. The existing model-training evaluation and
calibration toolchain remains separate and unchanged.

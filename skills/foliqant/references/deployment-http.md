# Deployment, CLI, and HTTP example reference

Read the implemented contracts in `src/foliqant/contracts/deployment.py`
and the composition root in `src/foliqant/bootstrap.py` before changing
configuration. The package owns an in-memory application API. HTTP is demonstrated
only by the small runnable example maintained outside the package transport/core.

Embedding code may import `load_environment`, `prepare_application` and
`open_application` from `foliqant`. Preparation is offline and does not read
environment values. Opening the application loads the adjacent `.env` once,
overlays the supplied environment and resolves marked deployment references.
Use `load_environment` only to load an explicitly selected additional location.
Package import does not discover configuration or open clients.

Decision/LLM steps may select a declared model alias, a `{profile, model?,
options?}` override, or a complete provider configuration. Inline API keys must
be environment references. Compile effective profiles offline and resolve them
once at open; derived profiles share source admission. `explain` shows the
authored provider/model and optional source profile without credentials.

## Deployment contract

Version 1 requires `workflows` and accepts `models`, `mcp`, `execution` and
`telemetry`, plus optional `evaluation: {dataset: <JSON path>}`. Workflow paths are relative to the configuration file, remain below
its directory after resolution, and each key equals the compiled workflow name.
The safe YAML loader rejects unknown/duplicate fields, aliases, custom tags and
unrecognized unions. Deployment fields marked for environment use accept `$NAME`
as a complete reference and `$$` as a literal dollar. Model `api_key`, telemetry
headers and stdio environment values are secret-safe. Missing references fail
before clients open. Do not expand workflow prompts, schemas, documents or
customer values; do not add shell, recursive or partial-string interpolation.

Workflows use inline `steps` or separate step files, never both. Step IDs, start
and success transitions remain explicit. Schemas may be inline or file-based
through the same compiler. Reject ambiguous Markdown instruction sources. Reuse
compiler diagnostics and graph/schema checks; never create a second validator.

`foliqant init DEST` creates a model-free project without overwriting a path.
`validate`, `explain`, `doctor` and `run` default to `foliqant.yaml` in the
current directory; `--config PATH` overrides it without parent-directory search.
`validate`, `explain` and `doctor` compile offline; `doctor` inspects installed
capabilities without opening endpoints. `run` reads one bounded envelope and waits
for one terminal in-memory result. Public CLI output is one safe JSON object.
Caught exception or validation text must not escape.

`evaluate` defaults to `foliqant.yaml` too. Its dataset path resolves relative to
that file; ordinary preparation/startup/validate/doctor never open or stat the
optional reference. Evaluation metadata is excluded from the runtime digest.
`evaluate --check` checks dataset/targets/structural pointers without execution;
`--replay REPORT` scores saved full public results without opening SDK clients.
Live execution reuses the ordinary application API, sequential suites and one
case at a time by default. `--max-concurrency` and `--timeout` are explicit bounds.
Reports default to unique `.foliqant/evaluations/report-TIMESTAMP.json` files under
the config directory; `--output` may select a new path, never overwrite one.
Only a content-free summary goes to stdout. Full artifacts include inputs, gold,
and public result reasons/evidence strength, not private model reasoning. Keep them
ignored and private. See [evaluation setup](evaluation.md) for gold authoring.

Compilation errors expose safe file/field/reason/hint details, never authored
values. Static checks catch graph gaps and provable schema incompatibilities;
runtime validation and golden-case evaluation remain necessary for dynamic data
and business correctness.

There are no `storage`, `worker`, HTTP-auth or execution-mode profiles. There are
no `migrate`, queue-worker, accepted-job, lookup or cancellation commands. The
library does not persist state or recover work after process loss.

## Identity and tool permission

Optional `tenant_id` and `principal_id` are invocation context. Without an
explicit `Identity`, the application derives them from validated envelope
metadata. With an explicit `Identity`, values must match and missing values may be
enriched. Neither path authenticates or authorizes a caller. A remote host owns
application authentication and permission before invoking the package.

External MCP access remains separately protected. Compiled steps allowlist tools;
`McpRuntime` invokes the current `ToolAuthorizer` with caller-supplied optional
context and frozen validated arguments. MCP OAuth uses scoped host credential
storage and maintained SDK behavior. Tool authorization does not become
application authentication.

## HTTP example

The example may expose a single bounded POST that decodes an envelope, invokes the
same foreground application call and returns its terminal `ExecutionResult`.
The host owns disconnect handling; cancellation of a workflow call propagates.
It must not return an
accepted receipt, detach work, expose result lookup/cancel, implement application
authentication or claim durability. Production ingress, authorization, rate
control, idempotency and hosting remain responsibilities of the embedding system.

Use only the generated envelope and execution-result schemas. The example adds no
transport-specific public DTO. Keep optional W3C trace context separate from body
identity and permission, and do not forward baggage.

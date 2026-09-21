# Deployment, CLI, and HTTP example reference

Read the implemented contracts in `src/foliqant/contracts/deployment.py`
and the composition root in `src/foliqant/bootstrap.py` before changing
configuration. The package owns an in-memory application API. HTTP is demonstrated
only by the small runnable example maintained outside the package transport/core.

Embedding code may import `load_environment`, `prepare_application` and
`open_application` from `foliqant`. Environment loading is explicit: call
`load_environment(config_path, environment)` to merge the adjacent `.env` with
caller environment values, then pass the returned mapping to `open_application`.
Package import does not discover configuration or open clients.

## Deployment contract

Version 1 requires `workflows` and accepts `models`, `mcp`, `execution` and
`telemetry`. Workflow paths are relative to the configuration file, remain below
its directory after resolution, and each key equals the compiled workflow name.
The safe YAML loader rejects unknown/duplicate fields, aliases, custom tags and
unrecognized unions. Credential fields name environment variables; arbitrary YAML
strings do not interpolate the environment.

`foliqant init DEST` creates a model-free project without overwriting a path.
`validate`, `explain`, `doctor` and `run` default to `foliqant.yaml` in the
current directory; `--config PATH` overrides it without parent-directory search.
`validate`, `explain` and `doctor` compile offline; `doctor` inspects installed
capabilities without opening endpoints. `run` reads one bounded envelope and waits
for one terminal in-memory result. Public CLI output is one safe JSON object.
Caught exception or validation text must not escape.

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
Disconnect cancellation follows ordinary call cancellation. It must not return an
accepted receipt, detach work, expose result lookup/cancel, implement application
authentication or claim durability. Production ingress, authorization, rate
control, idempotency and hosting remain responsibilities of the embedding system.

Use only the generated envelope and execution-result schemas. The example adds no
transport-specific public DTO. Keep optional W3C trace context separate from body
identity and permission, and do not forward baggage.

# Deployment, CLI, and HTTP reference

Read the implemented contracts in `service/src/foliqant/contracts/deployment.py`,
`http.py`, and `auth.py` before changing configuration. Use
`examples/http-workflow` as the model-free runnable example.

## Deployment contract

Version 1 requires `workflows` and accepts `mode`, `models`, `mcp`, `execution`,
`http`, and `telemetry`. Workflow paths are relative to the configuration file,
must remain below its directory after resolution, and each mapping key must equal
the compiled workflow name. Configuration uses the repository's safe YAML loader:
unknown and duplicate fields, aliases, custom tags, and unrecognized union tags
fail. Credential fields name environment variables; arbitrary YAML strings do
not interpolate environment values.

`foliqant init DEST` creates a new model-free project and refuses to overwrite a
path. `validate`, `explain`, and `doctor` are offline compilation commands;
`doctor` checks installed extras without opening their endpoints. `run` accepts a
bounded envelope from a regular file or stdin and only trusts explicit operator
identity flags. `run` and `serve` own safe global process telemetry when it is
configured; `serve` also requires `http` and owns one application lifespan.
Public CLI output is one safe JSON object; do not expose caught exception or
validation text. Preparation rejects selected write-effect handlers because the
nondurable runtime cannot assign or reconcile durable operation identity.

## Authentication and permission

Bearer configuration maps names to `token_env`, optional `tenant_id` and
`principal_id`, and explicit workflow grants. The adapter snapshots environment
values at startup and never accepts identity from headers or the body. JWT uses a
fixed HTTPS issuer/JWKS URL, audience, asymmetric algorithm allowlist, identity
claim names, workflow grants, and bounded fetch/cache settings. JWT workflow
grants come from configuration, not token claims. Development authentication is
valid only in development mode on a literal loopback host.

HTTP authenticates before reading a request body, then checks the requested
workflow grant. That does not authorize MCP business data. Compiled steps provide
the tool allowlist, the built-in bootstrap policy permits declared `read` effects,
and `McpRuntime` invokes the current `ToolAuthorizer` with trusted identity and
validated frozen arguments before every tool call. Embed with
`RuntimePlugins(tool_authorizer=...)` when tenant, ownership, role, or record-level
policy requires more checks. Write effects remain rejected.

## Current HTTP behavior

The routes are `POST /workflows/{name}/runs`, `GET /health`, and `GET /ready`.
The POST returns a terminal result or safe RFC 9457 problem details. It is a
synchronous, nondurable invocation. There is no accepted-job response, status or
cancel endpoint, persisted budget, detached task, or restart recovery.

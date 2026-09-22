# Deployment, CLI, and HTTP example reference

Use the installed `foliqant.contracts.deployment` models,
`foliqant.contracts.schemas.runtime_schemas()`, and current CLI help when
checking configuration. The package owns an in-memory application API; HTTP is a
host concern outside package transport and core.

## Configuration and preparation

The default path is `config/settings.yaml`. When `workflows` is omitted,
preparation discovers only immediate nonhidden
`config/*/workflow.yaml` files. Explicit workflow maps remain available.
Configuration paths stay below the selected configuration directory.

`prepare_application` compiles offline without reading `.env`, resolving
credentials, importing configured code, or opening SDK clients.
`open_application` reads the selected configuration directory's `.env`,
overlays the supplied environment, resolves marked fields, and opens owned
adapters.

Environment expansion applies only to marked deployment fields. Never expand
workflow instructions, prompt templates, bindings, schemas, documents, or
customer values. Inline credentials are forbidden; use complete environment
references.

Conventional workflow, flow, and step lookup resolves definitions only.
Declared flow transitions and ordered step lists determine execution. Missing or
ambiguous conventional files fail compilation. Explicit paths and inline
definitions are supported for intentional customization.

## CLI

`foliqant init DEST` creates a runtime workflow project without overwriting a
path.
`validate`, `explain`, `doctor`, `run`, and configured `evaluate`
default to `config/settings.yaml`; `--config PATH` selects an exact
alternative without parent-directory search.

`validate`, `explain`, `doctor`, and `evaluate --check` are offline.
`run` reads one bounded envelope and returns one foreground result.
`evaluate --replay` and `--compare` operate on saved artifacts without
opening providers. Normal evaluation runs its configured pipeline, flow, or
operation targets.

Caught errors use fixed safe messages and optional sanitized locations. Never
expose authored values, credentials, prompts, or raw exceptions.

## Identity and tool permission

Optional tenant and principal IDs are invocation context. The host authenticates
and authorizes callers before invoking Foliqant. MCP access remains separately
protected by compiled allowlists, declared read-only effects, schema validation,
and an optional host `ToolAuthorizer`.

An MCP profile's optional `auth` value names a trusted credential hook. Supply
the same key through `RuntimePlugins.mcp_credentials`; the value is an
`McpCredentialProvider`, not a token from configuration. HTTP sessions request
fresh caller-scoped authorization from that provider. Stdio profiles cannot name
an auth hook.

## HTTP example

The HTTP example may decode one envelope, invoke the same foreground application
call, and return its `ExecutionResult`. The host owns authentication,
authorization, rate control, idempotency, and disconnect handling.

Do not add accepted receipts, detached work, result lookup, cancellation
endpoints, or durability claims. Use the generated envelope and execution-result
schemas rather than transport-specific duplicate DTOs.

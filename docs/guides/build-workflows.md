# Build a workflow

A deployment configuration names workflow bundle directories and adapter
profiles. Each bundle contains `workflow.yaml`, a `steps/` directory, and any
JSON Schemas referenced by the workflow.

## Start from a generated bundle

```sh
uv run --no-sync foliqant init /tmp/my-workflow
find /tmp/my-workflow -type f | sort
```

The generated deployment is:

```yaml
version: 1
workflows:
  demo: workflows/demo
models: {}
mcp: {}
```

Paths are relative to the deployment configuration. Absolute paths, `..`,
duplicate YAML keys, unknown fields, and paths escaping that directory fail
startup validation.

## Define the graph

`workflow.yaml` declares a version, stable name, start step, optional default
model, input schema, and output binding:

```yaml
version: 1
name: support_triage
start: classify
defaults:
  model: local_qwen
input_schema: schemas/input.json
output: {pointer: /steps/extract/result}
```

Put each step in `steps/<step-name>.yaml` or in a Markdown file with YAML
frontmatter. Step names use lower-case letters, numbers, and underscores.

| Step | Purpose | I/O owner |
| --- | --- | --- |
| `decision` | Ask one or more native evidence-backed questions and route on a configured answer | Model adapter |
| `llm` | Return text or a value matching an authored JSON Schema | Model adapter |
| `mcp` | Call one declared, read-only MCP tool | MCP adapter and host authorizer |
| `handler` | Call a registered async Python function | Embedding application |
| `finish` | End with `completed` or `needs_review` | Runtime |

Bindings use RFC 6901 JSON pointers. `/payload/message` reads the input;
`/steps/classify/result` reads a completed step. A missing pointer fails unless
it is explicitly optional with a default. JSON `null` is a present value, not a
missing value.

The [support triage bundle](../../examples/support_triage/workflow.yaml) shows a
decision followed by schema extraction. The
[public-request bundle](../../examples/public_request_mcp/workflow.yaml) shows a
declared MCP call.

## Compile before running

```sh
uv run --no-sync foliqant validate --config foliqant.yaml
uv run --no-sync foliqant explain --config foliqant.yaml
uv run --no-sync foliqant doctor --config foliqant.yaml
```

Compilation validates schemas, references, graph targets, adapter aliases, and
read-only tool declarations without contacting a model or MCP server. `doctor`
also reports whether selected optional dependencies are installed, still without
contacting an endpoint.

## Keep policy outside prompts

The workflow fixes route targets and tool allowlists. The model cannot invent a
new step or grant tool permission. The host must still authenticate callers and
authorize resources. Prompts and tool catalogs are configuration, not an
authorization boundary.

Write steps so missing or conflicting evidence reaches `needs_review`. Validate
all external input and output with bounded schemas, keep credentials in the
environment, and avoid write tools unless the application has a separate
reconciliation design.

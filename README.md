# Foliqant

Foliqant is a Python runtime for typed, in-memory workflows that combine model,
MCP, and application operations. It compiles local configuration before opening
clients, passes only explicitly bound context, validates operation results, and
returns one structured result to the caller.

## Install

Foliqant supports CPython 3.12. The source-checkout instructions below use
[uv](https://docs.astral.sh/uv/); the project does not currently claim a
package-index release. Install the locked environment from a reviewed checkout
and select the adapters your application needs:

```sh
git clone https://github.com/sebastianwessel/foliqant.git
cd foliqant
uv sync --locked --no-dev --extra openai
source .venv/bin/activate
```

Available extras include `openai`, `azure`, `anthropic`, `mcp`, and
`telemetry`.

## Quick start

Create a project and copy its environment template:

```sh
foliqant init /tmp/foliqant-demo
cp /tmp/foliqant-demo/config/.env.example /tmp/foliqant-demo/config/.env
```

Set the explicitly served model in `/tmp/foliqant-demo/config/.env`:

```dotenv
MODEL_ID=your-served-model-id
MODEL_BASE_URL=http://127.0.0.1:8000/v1
```

Compile offline, inspect the plan, then run one envelope:

```sh
cd /tmp/foliqant-demo
foliqant validate
foliqant explain --workflow demo
foliqant run \
  --workflow demo \
  --input envelope.json
```

Commands run from a workflow project use `config/settings.yaml` by default.
`validate`, `explain`, and `doctor` compile offline. `run` opens only the
model and tool clients declared by that project and prints one JSON result.

## Learn the runtime

| Goal | Guide |
| --- | --- |
| Install, scaffold, and embed Foliqant | [Install and run](docs/getting-started/runtime.md) |
| Understand workflows, flows, steps, and results | [Runtime concepts](docs/concepts/runtime.md) |
| Author files, bindings, prompts, routes, and schemas | [Build a workflow](docs/guides/build-workflows.md) |
| Author typed evidence-backed decisions | [Decision contracts](docs/guides/decision-contracts.md) |
| Configure providers, MCP, limits, telemetry, and CLI behavior | [Runtime configuration](docs/reference/runtime-configuration.md) |
| Test pipelines, flows, and steps against reviewed gold | [Testing and evaluation](docs/guides/testing-and-evaluation.md) |
| Help a coding agent configure an application | [Foliqant skill](skills/foliqant/SKILL.md) |

Browse the [documentation home](docs/index.md) or the runnable
[`examples/`](examples/) directory. Live model measurements are opt-in; small
synthetic fixtures and successful smoke runs establish wiring, not general
quality, security, latency, or cost.

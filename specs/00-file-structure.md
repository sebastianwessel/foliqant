# Repository structure and ownership

| Path | Owner and purpose |
| --- | --- |
| `pyproject.toml`, `uv.lock` | Installable `foliqant` runtime; provider extras and separate development groups |
| `src/foliqant/` | Reusable in-memory runtime, compiler, contracts, decision types, adapters and evaluation |
| `tests/` | Library unit, protocol and packaging tests; synthetic inputs constructed in code |
| `model/pyproject.toml`, `model/uv.lock` | Separate training/curation CLI project; depends on the package's decision contracts |
| `model/src/foliqant_model/` | Acquisition, datasets, artifacts, MLX backend, training, calibration and export |
| `model/tests/` | Model tooling checks and opt-in real native lifecycle tests |
| `model/examples/` | Reproducible setup/curation/training recipes, never downloaded or generated datasets |
| `examples/` | Runnable business workflows, local Qwen configuration, MCP and thin HTTP host examples |
| `contracts/foliqant/`, `contracts/model/` | Generated schemas; Pydantic source definitions are authoritative |
| `docs/` | End-user package and model-development guides |
| `skills/foliqant/`, `skills/foliqant-model/` | Maintained agent guidance for their separate scopes |
| `scripts/` | Thin command wrappers and schema/docs/data checks |
| `specs/`, `plans/` | Internal requirements/research and review evidence respectively |

There is no `service/`, root workflow deployment, inbound transport package or
placeholder infrastructure folder. Workflow bundles live below their runnable
examples. Core never imports model training. Native decision types live once in
`foliqant.decisions`; model tooling imports those definitions without duplicating
wire shapes. The package root keeps imports lazy so contract use does not load
provider SDKs, telemetry or training code.

Model weights, prepared datasets, evaluation outputs, `.env`, virtual environments,
caches and generated responses remain ignored and outside Git. The default data
workspace is outside the checkout. Refactoring code does not rewrite existing
immutable artifact identities, source rights, splits or training data.

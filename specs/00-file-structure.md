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
| `examples/` | Runnable business workflows with `config/settings.yaml`, referenced flows/steps, colocated schemas, MCP and thin HTTP hosts |
| `schemas/foliqant/runtime/`, `schemas/foliqant/decisions/` | Generated library schemas; their canonical Pydantic types live in the runtime and decision packages |
| `model/schemas/` | Generated model-tooling schemas; canonical types live under `model/src/foliqant_model/` |
| `docs/`, `mkdocs.yml` | Published end-user guides; `docs/index.md` homepage, strict MkDocs build and GitHub Pages publishing |
| `skills/foliqant/`, `skills/foliqant-model/` | Maintained agent guidance for their separate scopes |
| `scripts/` | Thin command wrappers and schema/docs/data checks |
| `specs/`, `plans/` | Internal requirements and research; implementation/review evidence respectively |

There is no `service/`, root workflow deployment, inbound transport package or
placeholder infrastructure folder. Workflow bundles live below their runnable
examples: `config/settings.yaml` discovers immediate nonhidden
`config/<workflow-id>/workflow.yaml` entries when its registry is omitted.
Workflow names default to their directory; a sole flow supplies the default start.
A flow instance without a definition resolves `<flow-id>/flow.yaml`. Ordered step
IDs resolve exactly one of `<step-id>.step.yaml`, `<step-id>.step.md`,
`<step-id>/step.yaml` or `<step-id>/step.md`; missing or ambiguous candidates fail.
Explicit registries, inline definitions and file references remain supported.
References resolve from their containing file within the configuration root.
Step order and routing always remain authored, never inferred from file order.
Core never imports model training. Native decision types live once in
`foliqant.decisions`; model tooling imports those definitions without duplicating
wire shapes. The package root keeps imports lazy so contract use does not load
provider SDKs, telemetry or training code.

`scripts/` deliberately remains at the checkout root: it provides developer
entry points spanning the two uv projects and repository audits. It is not a
third runtime package. Historical review evidence remains under `plans/reviews/`;
it is retained as evidence rather than treated as live implementation guidance.

Reviewed synthetic example gold lives in tracked `examples/<name>/evaluation/`
JSON files and is read directly by example evaluators. Model weights, real/private
datasets, generated evaluation reports, `.env`, virtual environments,
caches and generated responses remain ignored and outside Git. The default data
workspace is the ignored `.foliqant/` directory in the checkout. Refactoring code does not rewrite existing
immutable artifact identities, source rights, splits or training data.

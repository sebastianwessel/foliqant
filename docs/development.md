# Develop Foliqant

Use this guide to work on the runtime, model tooling, or documentation. End-user
applications do not need the development dependencies described here.

## Set up the runtime project

Foliqant requires CPython 3.12 and [uv](https://docs.astral.sh/uv/). From the
repository root:

```sh
uv sync --locked --all-extras --group dev --group docs
```

This installs the runtime adapters, repository checks, examples, and local docs
tooling from the committed lockfile. It does not download model weights or
datasets.

Run the model-free quick start before changing a workflow:

```sh
uv run --no-sync foliqant init /tmp/foliqant-demo
uv run --no-sync foliqant validate \
  --config /tmp/foliqant-demo/foliqant.yaml
uv run --no-sync foliqant run \
  --config /tmp/foliqant-demo/foliqant.yaml \
  --workflow demo \
  --input /tmp/foliqant-demo/envelope.json
```

## Check runtime changes

```sh
uv run --no-sync pytest
uv run --no-sync mypy src
uv run --no-sync ruff check src tests examples scripts
uv run --no-sync python scripts/generate_schemas.py --check
uv build
```

Default tests exclude live model calls. Tests marked `live_model` require an
explicit selection and an explicitly configured endpoint.

## Work on model tooling

The `model/` directory is a separate uv project. Install its development tools
without the native training stack for configuration, validation, and unit-test
work:

```sh
uv sync --project model --locked --group dev
uv run --project model --no-sync pytest -c model/pyproject.toml model/tests
uv run --project model --no-sync mypy \
  --config-file model/pyproject.toml model/src
uv run --project model --no-sync ruff check \
  --config model/pyproject.toml model/src model/tests scripts
uv run --project model --no-sync python \
  scripts/generate_model_schemas.py --check model/schemas
```

Native MLX work requires Apple Silicon macOS. Add it only when training or
running native lifecycle integration:

```sh
uv sync --project model --locked --extra mlx --group dev
```

The default model test command excludes native integration tests. Those tests
require a completed local setup and an explicitly selected model; see [model
setup](getting-started/setup.md) before running them.

## Preview the documentation

The site uses MkDocs. Port `8001` avoids the default port used by the documented
local model endpoint:

```sh
uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8001
```

Open <http://127.0.0.1:8001/foliqant/>. Before opening a pull request, build in
strict mode and check guide links and documented CLI commands:

```sh
uv run --group docs mkdocs build --strict
uv run --project model --no-sync python scripts/check_docs.py
git diff --check
```

GitHub Pages is not enabled automatically. For a private repository it also
requires an eligible GitHub plan. When eligible, a repository administrator must
choose **Settings > Pages > Source > GitHub Actions** before the deployment job
can publish the site.

CI reports three checks: **Python quality gate**, **Runtime package quality
gate**, and **Documentation build**. The Pages deployment on `main` waits for all
three. A failed check prevents a merge only when repository rules require these
status checks; private repositories may require a plan upgrade before that rule
is available.

## Keep generated and private data out of Git

Model weights, prepared datasets, customer records, generated candidates,
checkpoints, prediction logs, and evaluation holdouts belong in a private data
workspace outside the checkout. Commit source manifests, configuration, schemas,
and documentation only. The setup command defaults to
`~/.local/share/foliqant` and refuses an unignored workspace inside a Git tree.

When a public contract changes, update its schema, example, and focused guide in
the same change. The runtime and model projects generate different schema sets;
run both drift checks when a change crosses that boundary.

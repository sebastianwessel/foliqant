# Contributor guide

Read [specs/README.md](specs/README.md), the relevant implementation contract,
and the current code. [docs/index.md](docs/index.md) is the public user guide.

## Scope and authority

The current user request authorizes work. Preserve unrelated edits and private
artifacts. Implement only the in-memory Python package: configured workflows,
sequential flows, bounded steps, model/MCP/handler integrations and evaluations.
Hosts own incoming transports, authentication, persistence and durable processing.
Runnable business examples belong in `examples/`.

Mechanical fixes and private implementation choices within the request need no
extra approval. Do not invent public behavior, fallbacks or infrastructure to fill
an unapproved gap. Resolve contradictions between specs and implementation rather
than documenting imaginary APIs. There is one current format; do not add version
fields, migration code or compatibility aliases.

## Keeping surfaces aligned

Change types, generated schemas, tests, examples, public guides and the runtime
skill together. Skills must be usable without internal specs. They teach setup,
business-use-case mapping, conventions, options and customization. Public docs
teach current usage, not implementation history. Author YAML examples and scaffold
files with block-style mappings and lists; reserve `{}` and `[]` for empty values.
JSON Schema resources remain JSON. Do not restrict valid user YAML merely for style.

Conventional, explicit-file and inline configuration share one compiler.
File discovery finds definitions; authored order and routes determine execution.
Use the shared environment resolver only for explicitly marked deployment fields.
Never interpolate secrets into prompts or input data.

Core mechanics use standard-library values and ports. Pydantic and provider SDKs
stay at boundaries. Preserve async cancellation and owned client lifetimes. Never
retry ambiguous remote timeouts automatically or report unavailable usage as zero.

## Verification and privacy

Run checks in [.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md). Default tests
are offline. Do not call model endpoints to check docs or configuration. Live
inference, external publication and paid services follow current user authorization;
old records do not grant permission. Do not mutate another active run's environment.

Commit small authored synthetic gold under each example's `evaluation/` folder.
Keep real customer data, private gold, responses, reports, secrets and logs out of
Git. Never log payloads, prompts, identifiers, credentials or raw exceptions.
Evaluation reports deliberately contain private business data and require explicit
local writing. Synthetic tests establish wiring and invariants, not model accuracy.

Report actual checks and any limitations. Do not claim previously observed test
counts or model outcomes as current evidence.

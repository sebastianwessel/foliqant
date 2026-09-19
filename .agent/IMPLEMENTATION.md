# Implementation conventions

Version 0.1, 2026-09-19. Applies to the repository foundation. Architecture and execution semantics remain in [docs/architecture.md](../docs/architecture.md); this guide does not approve additional scope.

## Modules and dependencies

Use `model/` for the Python model lifecycle, `inference/` for standard-server deployment profiles, and `service/` for the independent workflow application. Keep a single service package initially; do not turn every interface into a published package. Core depends on ports, adapters implement ports, and application startup assembles them. Never import adapters from core.

Each file should have one clear responsibility. Prefer modules under approximately 300 lines as a review heuristic, not a reason to fragment cohesive code. Avoid generic `utils` collections. JSON contracts and YAML keys use consistent camelCase; folders and workflow identifiers use kebab-case. Public identifiers and filenames must have stable documented meanings.

## Recommended language conventions

If TypeScript is selected: use ESM, strict checking, `noUncheckedIndexedAccess`, and `exactOptionalPropertyTypes`; use `unknown` at untrusted boundaries and validate before narrowing. Avoid `any`, unchecked casts, and cross-layer imports. Document exported types/functions with IDE-friendly TSDoc and small examples for non-obvious behavior.

For Python model tooling: use typed functions, explicit config models, structured errors, and independent dependency locks. Choose compatible pinned versions after testing the selected model/training stack. Avoid implicit downloads or training as import-time effects.

## Contracts and examples

Keep language-neutral schemas in `contracts/`. Generate or infer language types from the selected canonical schemas; do not hand-maintain divergent TypeScript and Python copies. External YAML remains runtime-validated even when internal code is typed. Preserve a clear subset if a provider cannot accept the full JSON Schema vocabulary.

Maintain a public-surface inventory in `service/README.md` as implementation starts. For each endpoint, adapter, or step handler, document validation, side effects, timeout/retry behavior, identity propagation, and durability. Do not introduce arbitrary expression/module-loading escape hatches instead of defining a reviewed extension contract.

Keep workflow examples focused, self-contained, and versioned, with local prompt references and synthetic input. Distinguish proposed examples from runnable examples. Run contract generation and drift checks when such tooling exists; until then, never claim generated artifacts were checked automatically.

## Errors, security, and persistence

Use typed failure categories at layer boundaries and preserve causal errors internally. Business review is an outcome, not a transport exception. Redact secrets and customer content in logs. Treat model outputs and retrieved documents as untrusted. Changes to retry, persistence, approval, or resume behavior require an explicit architecture/contract update.

Persist workflow/model/schema revisions with runs. Do not silently change the meaning of stored manifests. Do not describe best-effort execution as durable or exactly-once.

## Testing and verification

Test observable behavior using fake model, store, clock, and delivery adapters where those ports exist. Prioritize invalid configuration, tenant isolation, invalid/unsupported evidence, timeouts, cancellation, duplicate messages, and recovery behavior. Do not create tests that merely mirror low-impact scaffolding or prose.

Current checks: `git diff --check`; `python3 -m json.tool <schema-or-fixture.json>`. No runtime or integration suite exists yet. Add the real typecheck, lint, unit, and integration commands with executable code and its lockfiles.

## Convention drift

There is no existing implementation to reconcile. Language choice, workflow grammar, storage, and adapter packaging are proposals; update this guide after those decisions rather than pretending they are settled.

# Package and evaluation review

Date: 2026-09-21. Baseline: `2a9539a`.
Scope: installable in-memory library, coherent repository boundaries, step/full
pipeline evaluation, real runnable business examples, specs/docs/skills alignment.

## Changes

The root `foliqant` distribution contains `src/foliqant` including its shared
native decision contracts. Model tooling moved to its own uv project in `model/`
and imports those same contracts. Runtime imports are lazy; importing decision
contracts does not load provider clients or the workflow runtime. No compatibility
packages, hosted services, persistence or job infrastructure were introduced.

Examples own their complete workflow bundles. Obsolete root workflow/config
sketches and README-only placeholder directories were removed. Schemas, CI,
wrappers, guide paths and skills follow the current layout.

Evaluation checks explicit immutable golden cases through caller-selected async
variants. Full runs and isolated steps reuse the actual executor. Reports preserve
missing/skipped/error checks, actual step measurements and unknown token counts.
They identify suite/configuration revisions without claiming verified provider
weights or recording business payloads. They do not generate/optimize prompts or
judge answers with an LLM.

## Review findings

Independent review found an invalid `None` step selector could run the complete
pipeline; public isolated-step entry now rejects missing/non-string selectors
before I/O. Regression coverage also checks no upstream steps execute, reviewed
results stop locally, failure attempts remain measured, and deadlines still apply.
Suite fingerprinting happens before execution and supports valid deeply nested
inputs without adding artificial depth failures. Example configuration identity
includes model settings as well as the workflow revision.

## Verification

- 551 package tests pass, including real local MCP/HTTP protocol fixtures,
  isolated-step execution, evaluator cancellation, wheel installation into a fresh
  environment and CLI execution outside the checkout.
- 934 model-tooling tests pass; seven explicitly opt-in native lifecycle tests are
  deselected. No training, dataset generation or immutable artifact rewrite ran.
- Strict mypy passes for package/examples and model sources; Ruff lint/format,
  nine runtime schemas, 27 model schemas, 34 guide/skill references and 44 CLI
  examples pass. Existing GitHub required-check names remain stable.
- Both root and model uv environments synchronize from their locks offline.
  The obsolete service and placeholder directories are physically gone.
- Independent reviews checked evaluation semantics, step isolation and the
  specification/guide. Cancellation wording now states actual limits: evaluator
  tasks are joined, but remote/blocking work is not guaranteed to stop.

## Local Qwen evaluation

Two synthetic English email cases ran sequentially against the already configured
local `incoai/Qwen3.8-27B-Splash`, with low reasoning, temperature 0.1 and an 8192
output-token cap. Each full pipeline makes one classification and one extraction
request. There were no model-discovery requests or cloud calls.

The initial report recorded 5/6 assertions passing (both classifications correct,
no pipeline errors) in 30.92 seconds. The failed assertion expected an absent
account reference to remain null in an invoice-only message. Reports omit actual
business values, so that report alone does not establish which incorrect value
was emitted. The example's generic field definition was clarified to distinguish
customer/account identifiers from invoice/order/case/claim references.

The same suite then passed 6/6 assertions with no pipeline errors in 16.00 seconds.
This is development-set evidence only; one repeat does not establish a general
accuracy or latency gain. Timing may reflect warm caches or sampling variation.
The suite fingerprint stayed identical, while the configuration revision changed.
Reports remain in the private local evaluation workspace, not Git:


- `package-smoke-20260921-700dc1e9.json` — SHA-256 `89ee2e82d962b7033a8603d8194c61893911d28fc82d28e24f0e28cc29eddb80`.

- `package-smoke-refined-20260921-0fc5196e.json` — SHA-256 `92c86b555b67eeda2b83b763b948a0456b5b506706a05767411df9c97d10803a`.

Golden agreement is not calibrated confidence. Substantive use requires reviewed
representative cases, boundary cases, language/domain slices and an untouched
holdout after prompt selection. No financial production qualification is asserted.

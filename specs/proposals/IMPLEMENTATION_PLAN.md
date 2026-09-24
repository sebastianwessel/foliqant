# Implementation plan

Status legend: ☐ open · ◐ in progress · ☑ done. Update this file as work lands.

## Ground rules

* Fork, no compatibility: replaced constructs are removed (`optional`, `route_key`); docs,
  examples, tutorials, skill references and tests describe only the current contract.
* No fakes or mocks in `src/`. Tests use scripted pydantic-ai `FunctionModel` bindings and the
  real local MCP example servers, as upstream does.
* Every feature lands with: contract models → compiler validation → plan → runtime → result
  model → diagnostics/`explain` → tests (contracts, compiler, runtime, CLI) → docs → skill
  references → examples.
* Root-cause fixes for bugs found on the way, each with a regression test.
* Quality gates in `vendor/foliqant`: `uv run ruff check src tests examples scripts`,
  `uv run ruff format --check`, `uv run mypy` (strict, `src`), `uv run pytest`
  (`-m 'not live_model'`), `uv run python scripts/check_docs.py`, `mkdocs build --strict`
  when mkdocs is installed.

## Phase 1 — Library core (one owner, sequential inside; touches contracts, compiler, core, cli)

| # | Work item | Spec | Status |
|---|---|---|---|
| 1.1 | Condition contracts (`contracts/conditions.py`): leaf/combinator models, operator validation, regex compilation, depth ≤ 8 | conditions.md | ☑ |
| 1.2 | `core/conditions.py`: pure evaluator over the binding context; absent = null; type mismatches → false; unit tests per operator | conditions.md | ☑ |
| 1.3 | Bindings: remove `optional`; `default` implies optional; `first_of`; object `output.fields` (flow + workflow); compiler dominance for `first_of` and conditional steps | bindings.md | ☑ |
| 1.4 | Transitions: `route` list (+ `on_unresolved` route form), routed `start`; `TransitionResult.route` | conditions.md | ☑ |
| 1.5 | Step `when`: `steps` entries as id or `{id, when}`; skipped records; dominance for later references | conditions.md | ☑ |
| 1.6 | `repeat`: contracts, compiler (retry flow rules, budget estimate, graph checks), runner loop, `FlowResult.attempts/attempt_count/repeat`, retry-flow role | repeat.md | ☑ |
| 1.7 | Workflow `defaults.on_unresolved`, flow-level `defaults.model` | static-checks.md | ☑ |
| 1.8 | Diagnostics model on `PreparedApplication`; route coverage (`unmatched_case` error, `uncovered_value` warning, `default_covers`), `condition_type_mismatch`, `condition_always_*`, `unused_llm_input`, `empty_text_source`, `collection_budget`, `repeat_budget`, `review_ends_run`; `prepare_application(strict=)`; `validate --strict` | static-checks.md | ☑ |
| 1.9 | `explain`: `--format json|mermaid|dot`, `foliqant.explain(prepared)` graph model incl. bindings, conditions, repeat, effective review routes, diagnostics | static-checks.md | ☑ |
| 1.10 | Handler outcome forwarding (`unresolved_issues`, `selection`), `route_key` removal, `StepContext` additions (`trace`, `attempt`, `collection_item`, `flow_role`) | handlers.md | ☑ |
| 1.11 | Declared handler contracts (`settings.handlers`), CLI works with handlers, registration/declaration check (`handler_contract_mismatch`, `missing_handler_registration`), optional schemas on `HandlerRegistration` | handlers.md | ☑ |
| 1.12 | Observability: span attributes/events for attempts, roles, routes, skips, repeat stop; `execution.trace`; log events; `telemetry.conditions` opt-in | observability.md | ☑ |
| 1.13 | Fold the proposal specs into `specs/runtime.md` / `collections.md`; keep rationale here | all | ☑ |

## Phase 2 — Documentation, examples, skill (same owner as Phase 1, after each item)

| # | Work item | Status |
|---|---|---|
| 2.1 | New docs pages: `configuration/conditions.md`, `configuration/repeat.md`; update `workflows.md`, `flows.md`, `context.md`, `steps/handler.md`, `integration/results.md`, `integration/observability.md`, `reference/runtime-configuration.md`, `reference/inputs-and-results.md`; mkdocs nav | ☑ |
| 2.2 | Tutorials: extend "Route between flows" with `route`/`when`; new "Retry a lookup" step using `repeat`; all snippets compile (`scripts/check_docs.py`) | ☑ |
| 2.3 | Skill: `skills/foliqant/references/*.md` rewritten for the current contract (no `optional`, `route`/`when`/`repeat`/`first_of`, declared handlers, diagnostics, `explain --format`) and `SKILL.md` verification steps | ☑ |
| 2.4 | Examples: every example updated (no `optional`, declared handlers); new `examples/conditional_intake` (routed start, `route`, step `when`, `repeat` with MCP retry flow, `first_of`, object output) with offline scripted model, evaluation cases, tests | ☑ |
| 2.5 | `foliqant init` scaffold uses the current contract | ☑ |
| 2.6 | README / docs index feature list | ☑ |

## Phase 3 — Application alignment (platform repository, after Phase 1)

| # | Work item | Status |
|---|---|---|
| 3.1 | `new_report`: routed `start` replaces `report_type_source`; step `when` folds `repair`/`recheck` into `extract_fields`; `repeat` folds `correct_identifier` + `lookup_corrected_fund` into `lookup_fund`; `first_of` in `enrich`; `defaults.on_unresolved`; `default_covers` where wanted | ☐ |
| 3.2 | `answer_handling`: same for `map_reply`/`repair_mapping` and the lookup/correction collections (repeat inside the callable flow) | ☐ |
| 3.3 | Handlers: declare contracts in `settings.yaml` (generated from the pydantic models); remove routing-only handlers and status-only outputs that the conditions make redundant | ☐ |
| 3.4 | Remove application lint/explain where the library now provides them (`triage_workflows.lint`, `explain --mermaid` → `foliqant explain --format mermaid`), keep the drift tests | ☐ |
| 3.5 | Host observability: pass the transport trace into `app.run`, log `execution.trace` ids | ☐ |
| 3.6 | Docs of the platform (`docs/implementation.md` §7, `docs/workflows.md`, configuration) | ☐ |

## Phase 5 — Structural guarantees, hard failure and generated documentation (library)

| # | Work item | Spec | Status |
|---|---|---|---|
| 5.1 | `docs/configuration/validation.md` ("What the compiler guarantees"): every guarantee with code, level, failing example and fix; one focused test per row in `tests/test_config_guarantees.py`, which also compiles every example of the guide | static-checks.md | ☑ |
| 5.2 | Compiler audit fixes: `review_completes_run`, dead-route-entry reachability, `incompatible_route_type` on mixed types, `unavailable_value` (later attempts, attempt errors, defaults lacking a key, with completion-aware defaults), bindable step record fields, first-step `selection` in flow outputs, recursive `collection_budget`, new `run_budget` | runtime.md | ☑ |
| 5.3 | Located problems: validation errors mapped to YAML keys, full key-path fields, messages naming identifiers, hints on every diagnostic | runtime.md | ☑ |
| 5.4 | `CompilationError.problems`/`.diagnostics`, rendered `str(error)`, strict preparation listing every warning, `PreparedApplication.strict` enforced by `open_application`, all missing handler registrations | runtime.md | ☑ |
| 5.5 | `review_ends_run` warning unless the reviewing flow's result is the returned output (info) | static-checks.md | ☑ |
| 5.6 | CLI: JSON status on stdout, rendered problems on stderr for every command, documented exit codes; `explain --all`, `--output`, `--check`; `foliqant.graph.render_document` | runtime.md | ☑ |
| 5.7 | Graph labels with authored condition operands and complete repeat annotations; literals and defaults stay hidden | static-checks.md | ☑ |
| 5.8 | Docs (`validation.md`, `reference/runtime-configuration.md`, `integration/observability.md`, workflows, conditions, integration, deployment), skill references, examples with explicit review routes and generated `WORKFLOWS.md` checked by `tests/test_example_workflow_docs.py` | — | ☑ |
| 5.9 | Platform follow-up: regenerate `docs/workflows.md` and update graph-label assertions in `apps/workflows/tests/test_workflow_graph.py` (repeat annotation and operands changed); consider `foliqant explain --all --check` instead of the custom generator | — | ☐ |

## Phase 6 — Readable traces, usage by model and cost estimation (library)

| # | Work item | Spec | Status |
|---|---|---|---|
| 6.1 | Span names from configuration IDs (`workflow <id>`, `flow <id>` with ` [item <i>]` / ` #<n>`, `step <id> (<type>)`), one naming function at creation and export; `foliqant.step.kind`; existing `foliqant.*` attributes and scopes kept | usage-and-pricing.md | ☑ |
| 6.2 | Model spans: reported `gen_ai.usage.*` including cached, cache-creation and reasoning tokens, `gen_ai.response.model` (configured model or dated snapshot), `foliqant.usage.cost`; export allowlist updated | usage-and-pricing.md | ☑ |
| 6.3 | `by_model` on every usage object, recorded per reserved attempt (`start_model_request(model, pricing)`), aggregated through steps, flows, attempts, retry flows and collections | usage-and-pricing.md | ☑ |
| 6.4 | Profile `pricing` (strict, `Decimal`, long-context tier, `reasoning_billed_as`, `reference_model`), override replace/clear, results `cost`/`cost_complete`/`currency`/`reference_model` rounded to six decimals; schemas regenerated | usage-and-pricing.md | ☑ |
| 6.5 | `explain` (`model_selection.pricing`) and `doctor` (`models`) show configured pricing | usage-and-pricing.md | ☑ |
| 6.6 | Cached and reasoning usage from OpenAI-compatible and Azure responses verified (chat, Responses, `FunctionModel`) | usage-and-pricing.md | ☑ |
| 6.7 | Docs (`integration/observability.md`, `integration/results.md`, `reference/inputs-and-results.md`, `configuration/models.md`), skill reference, `runtime.md`; tests in `tests/test_usage_pricing.py` and the telemetry suites | — | ☑ |
| 6.8 | Platform follow-up: dashboards and queries that matched span names `foliqant.workflow|flow|step` select by instrumentation scope or `foliqant.*` attributes; add `pricing` to deployed profiles | — | ☐ |

## Phase 4 — Review and hardening

| # | Work item | Status |
|---|---|---|
| 4.1 | Independent code review of the library changes (correctness, spec conformance, edge cases, error paths, docs accuracy); fix findings | ☑ |
| 4.2 | Independent review of the application alignment; fix findings | ☐ |
| 4.3 | Full gates: library suite, platform `tasks.py check`, e2e, docker compose run | ☐ |
| 4.4 | Upstream: branch from the fork point, apply the fork, open a pull request in the upstream repository with the specs as description | ☐ |

### 4.1 review notes

Fixed with regression tests:

* Repeat failures between attempts (unbindable `retry.input`, unbindable next
  input, deadline) keep the last attempt with `stopped_by: failure`; an
  unbindable retry input is a failed retry run.
* Attempt entries bound at the boundary expose `attempt`, `status`, `result`,
  `error` only; pointers to `steps`/`usage`/`elapsed_seconds` are
  `dangling_pointer`.
* Static value checks compare numbers by value (`1` = `1.0`); handler contract
  comparison likewise (booleans stay distinct). `review_ends_run` names what the
  host really receives.
* Bounded conditions: `matches` compares values ≤ 1024 characters
  (`value_too_long` mismatch event otherwise); patterns with unbounded
  backtracking fail with `unsafe_pattern` (parse-tree analysis in
  `compiler/patterns.py`). Only unbounded outer quantifiers over ambiguous
  groups are rejected; bounded ones enter the work estimate (bound × inner).
* MCP trace headers are derived from each request's own `_meta` carrier; the
  session-level holder is gone, so concurrent calls never exchange traceparents.
* `invalid_condition` is reported by position, not by key name.
* `describe_condition` keeps `present=`/`empty=` polarity.

Added capabilities (additive): callable-flow `repeat` per collection item
(ledger `attempts`/`repeat`/`retry`, budget includes the worst case), shared
explicit step definitions anywhere in the configuration root,
`ExecutionResult.start`, and `RuntimePlugins(tracer_provider=...)`.

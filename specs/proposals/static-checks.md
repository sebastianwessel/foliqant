# Static checks, review defaults and `explain`

## Motivation

`validate` proves the graph has no structural dead end, but not that a route can ever be
taken: a `cases` key with a typo silently falls to `default`, and a flow without
`on_unresolved` ends a run in review with the output default — both are invisible until they
happen. Applications wrote their own lint and graph renderer; the compiler has all the
information to do this.

## Diagnostics model

`PreparedApplication` gains `diagnostics: tuple[Diagnostic, ...]` where
`Diagnostic(code, level: "warning" | "info", location: SourceLocation, message)`. Errors keep
raising `CompilationError` as today. `validate` and `explain` print diagnostics; `validate
--strict` turns warnings into a non-zero exit. `prepare_application(..., strict=True)` raises on
warnings for hosts that want that in tests.

## Route coverage

For a `cases` transition the compiler derives the **allowed values** of the bound field from
its static schema: `enum`/`const` (including inside `anyOf`/`oneOf` with `null`), decision
`selection/category/id` (catalog ids plus fallback id), decision predicate answers
(`true`/`false`/`unknown`), MCP and handler output schemas, LLM output schemas, and object
outputs (`bindings.md`).

| Diagnostic | Level | When |
|---|---|---|
| `unmatched_case` | error | a case key is not among the allowed values (the route can never be taken) |
| `uncovered_value` | warning | an allowed value has no case and falls to `default` — silenced by listing the values in `default_covers: [..]` on the transition, which then must equal the uncovered set (`default_covers_mismatch` error otherwise) |
| `case_on_unknown_type` | info | the static type of the bound field is unknown (no schema); cases cannot be checked |

The same applies to `route` conditions through `condition_always_false` / `condition_always_true`
(`conditions.md`).

## Review defaults

```yaml
defaults:
  model: default
  on_unresolved: {flow: enrich}      # or an outcome, an issue map, or a `route`
```

A flow without its own `on_unresolved` uses the workflow default; without either, the implicit
terminal `needs_review` applies as today. A default that names a flow must be a routed flow
reachable from every flow that inherits it without creating a cycle; the compiler checks this
per inheriting flow (`invalid_default_review_route` error).

| Diagnostic | Level | When |
|---|---|---|
| `review_ends_run` | info | a flow's effective review route is the terminal `needs_review` outcome; the message lists the workflow output default that the host receives |
| `review_route_to_self`, cycles | error | as for transitions |

Flow-level model default: a flow definition may declare `defaults: {model: <profile>}`, taking
precedence over the workflow default for its steps.

## Prompt and input checks

| Diagnostic | Level | When |
|---|---|---|
| `unused_llm_input` | warning | an LLM step declares an input that its `prompt` does not reference (it is not sent) |
| `empty_text_source` | info | a decision `text` source is bound to a field whose schema allows an empty string; the run would fail with `invalid_input` — the message suggests a `default` or `format: json` |
| `collection_budget` | warning | `max_items × steps(child)` may exceed `execution.max_steps` |

## `explain`

`explain` gains `--format json|mermaid|dot` (default `json`) and includes for every flow the
route binding pointers and conditions (their pointers and operators, never bound values), the
repeat configuration, the effective review route and the list of `/flows/<id>` results
available to its bindings. Mermaid/DOT render: flows as nodes with their steps, transitions as
labelled solid edges (`case` key, `route` index + condensed condition, or `default`),
`on_unresolved` as dashed edges, collection and repeat-retry calls as dotted edges, `repeat`
as a self-loop annotation, terminal outcomes as round nodes. Diagnostics are listed after the
graph. The Python API `foliqant.explain(prepared) -> WorkflowGraph` returns the same model for
hosts that render their own documentation.

## Documentation

* `docs/reference/runtime-configuration.md`: diagnostics, `validate --strict`, `explain` formats.
* `docs/configuration/workflows.md`: `defaults.on_unresolved`, `default_covers`.
* `docs/integration/observability.md`: reading `explain` output during review.
* `skills/foliqant/references/deployment-http.md` (CLI section) and `workflow-design.md`
  ("Deliverables and checks": run `explain --format mermaid` and fix every warning).

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* `Diagnostic` also carries an optional `field`, and new diagnostics and the
  condition, route and repeat errors carry line/column source locations.
* `validate --strict` fails with the standard error object for the first
  warning plus a `diagnostics` list with every finding.
* `review_ends_run` is reported for implicit terminal review only (no own
  `on_unresolved` and no workflow default); an explicit `outcome: needs_review`
  is a visible decision.
* A cycle closed by an inherited review default reports
  `invalid_default_review_route`; an explicit review route to its own flow
  reports `review_route_to_self`.
* `foliqant.explain(prepared, workflow=None) -> WorkflowGraph`; `workflow` may
  be omitted when exactly one workflow is configured. Rendering helpers are
  `foliqant.graph.render_mermaid` and `render_dot`; `--format mermaid|dot` prints
  text and needs `--workflow` when several workflows exist.
* The graph model shows binding pointers, `has_default`, condensed conditions and
  case keys, but no literal values, defaults or condition operands, matching the
  telemetry rule.
* `compile_workflow(..., max_steps=)` enables the budget checks;
  `prepare_application` passes `execution.max_steps`.

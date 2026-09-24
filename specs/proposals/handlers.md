# Handlers: declared contracts, review issues, context

## Motivation

Every real application registers trusted handlers, and every generic CLI command fails for it
with `unknown_handler`. A handler that stops a flow for review cannot say why, so issue-specific
review routes work for decisions only. `StepOutcome.route_key` is never read.

## Declared handler contracts

`settings.yaml` gains a `handlers` section mirroring the MCP catalog:

```yaml
handlers:
  identify_missing_fields:
    input_schema: ../contracts/identify_missing_fields.input.json     # inline object or local path
    output_schema: ../contracts/identify_missing_fields.output.json
    effect: read
```

* Paths resolve relative to the settings file and must stay inside the configuration root.
* The compiler uses declared contracts for binding and route checks exactly as it uses
  registered ones today. `validate`, `explain`, `doctor` and `evaluate --check` therefore work
  without Python registrations.
* `prepare_application(path, handlers=...)` accepts registrations for declared handlers and
  verifies each registration against its declaration: the schemas must be deep-equal after
  canonicalisation and the effect must match, otherwise `handler_contract_mismatch` (error,
  naming the handler and the first differing path). A registration for an undeclared handler
  is `unknown_handler`; a declared handler without registration is `missing_handler_registration`
  at `open_application` (compilation succeeds so the CLI can run).
* Configuration cannot import code: the declaration is a contract, the host supplies the callable.

`HandlerRegistration` keeps `handler` and `effect`; `input_schema` / `output_schema` become
optional: when omitted the declared schemas apply, when given they must match. `effect: write`
remains rejected by the runner.

## Review issues from handlers

`StepOutcome.unresolved_issues` and `StepOutcome.selection` returned by a handler are forwarded
to the flow record (today the handler adapter drops them). A handler review with issues follows
the issue-specific `on_unresolved` map like a decision; without issues it follows the default.
`selection` from a handler is validated like a decision selection (`origin` must be `fallback`
when `needs_review`).

## `route_key` removal

`StepOutcome.route_key` is removed. Routing comes from configuration only.

## `StepContext`

Adds `trace: Mapping[str, str]` (the W3C trace carrier of the current span, empty without
telemetry), `attempt: int` (repeat attempt, 1 for non-repeated flows), `collection_item: str | None`
(the item id when running inside a collection) and `flow_role: "routed" | "callable" | "retry"`.
Handlers may use `trace` to propagate context to their own outbound calls.

## Documentation

* `docs/steps/handler.md`: declared contracts, review issues, `StepContext` fields.
* `docs/reference/runtime-configuration.md`: `handlers` section; CLI with handlers.
* `docs/integration/index.md` and `deployment.md`: registration versus declaration.
* `skills/foliqant/references/runtime-configuration.md` and `deployment-http.md`.
* Examples with handlers declare their contracts (`routed_intake`, `multi_request_processing`,
  `support_email_tutorial`, the new `conditional_intake`).

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* Declared schemas are inline objects or JSON/YAML files; they must be
  self-contained (internal `$ref` only), like tool catalog schemas.
* `unknown_handler`, `handler_contract_mismatch` and
  `missing_handler_registration` name the handler in the field
  (`handlers.<name>`, plus `.effect` or `.<schema>/<json-pointer>`).
* `missing_handler_registration` applies to every declared handler when the
  application opens; the CLI `run` reports it as a configuration error.
* `unresolved_issues` require `needs_review` for every step type. A handler
  `selection` is checked for origin/status consistency; a native choice result
  must also agree with it.
* The decision adapter's internal `route_key` became `answer_key`
  (`ValidatedDecision`); it only derives the model selection.

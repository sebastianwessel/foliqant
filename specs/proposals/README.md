# Feature specifications (fork)

These specifications extend `../runtime.md`, `../collections.md`, `../prompt-trust.md` and
`../evaluation.md`. They were refined from the review of a six-workflow application
(`docs/foliqant-proposals.md` of the platform repository) into complete, general features.
Status: implemented. The normative text is folded into `../runtime.md` and
`../collections.md` and the user documentation; this directory keeps the design
rationale and, at the end of each file, the implementation notes that record
refinements made while building the features.

| Spec | Feature | Removes from applications |
|---|---|---|
| [conditions.md](conditions.md) | A closed condition language (`when`) and conditional routing (`route`, routed `start`, step `when`) | routing-only handler flows, `next` keys emitted by handlers |
| [repeat.md](repeat.md) | Bounded repetition of a flow with an optional retry flow (`repeat`) | hand-unrolled retry flows (`lookup` / `relookup`, `check` / `recheck`) |
| [bindings.md](bindings.md) | `first_of` bindings, object outputs (`fields`), `default` implies optional | join flows with optional inputs, projection handlers |
| [static-checks.md](static-checks.md) | Route coverage checks, workflow-level review default, warnings, `explain` graphs | application-side lint scripts and graph generators |
| [handlers.md](handlers.md) | Declared handler contracts for the CLI, review issues from handlers, `route_key` removal, richer `StepContext` | CLI workarounds, dead fields |
| [observability.md](observability.md) | Spans and events for routes, conditions, repeats and skips; host trace propagation; structured logs | ad-hoc logging in hosts |
| [usage-and-pricing.md](usage-and-pricing.md) | Descriptive span names, usage by provider model, configured cost estimates | trace tooling that decodes attributes, application-side cost spreadsheets |
| [evaluation-extensions.md](evaluation-extensions.md) | Null-aware `fields` metrics, `absent_as_null`, `text` / `contains`, `each` projections, checkpoint resume, progress, paired bootstrap intervals, member groups, cost summaries | application-side scoring layers, resume journals, bootstrap scripts |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Delivery plan: order, ownership, verification, documentation | — |

Principles kept by every feature (see `../README.md`): reviewed configuration owns the topology;
model output is data; no code import, expression evaluator or interpolation in YAML; explicit
review routes; an acyclic, statically checkable graph; one foreground call returns one result.

This fork has no compatibility layer: a construct that is replaced (`optional: true`,
`route_key`) is removed, and the documentation describes only the current contract.

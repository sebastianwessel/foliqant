# Conditions and conditional routing

## Motivation

Today the only way to make a route depend on data is an exact string match on one bound value
(`cases`), and the only way to skip work is to route around a flow. Any other decision ("the
form names a report type", "the check found no issues", "fewer than three candidates") needs a
trusted handler whose sole output is a routing key, plus a flow to host that handler. Conditions
give the configuration a small, closed vocabulary to express such decisions directly on the
data it already binds — without an expression evaluator and without giving the model authority
over the topology.

## The condition language

A **condition** is a YAML object that is either a leaf or a combinator.

```yaml
# leaf: one source, one operator
binding: {pointer: /flows/lookup_fund/result/status}
equals: found

# combinators
all: [<condition>, <condition>, ...]      # logical and, 1..32 operands
any: [<condition>, <condition>, ...]      # logical or, 1..32 operands
not: <condition>
```

### Source

Exactly one of `binding` (a pointer binding, no default) or `literal`. The pointer obeys the
scope rules of the position the condition appears in (§ Where conditions appear). A pointer
that does not resolve, or resolves to JSON `null`, is **absent**; every operator defines its
result for an absent value, so a condition always evaluates and never raises for missing data.

### Operators

Exactly one operator per leaf.

| Operator | Operand | True when |
|---|---|---|
| `present` | `true` / `false` | the value is present (not absent) — negated for `false` |
| `empty` | `true` / `false` | the value is absent, `""`, `[]` or `{}` — negated for `false` |
| `equals` | any JSON value | deep equality, no coercion (`"1"` ≠ `1`); absent equals `null` |
| `not_equals` | any JSON value | negation of `equals` |
| `in` | non-empty array of JSON values (≤ 64) | the value deep-equals one element |
| `not_in` | non-empty array | negation of `in` |
| `gt`, `gte`, `lt`, `lte` | number | the value is a number and the comparison holds; otherwise false |
| `matches` | regular expression string (≤ 256 chars) | the value is a string and the pattern full-matches it; otherwise false |
| `length` | `{gt|gte|lt|lte|equals: integer}` (one key) | the value is a string, array or object and its length satisfies the comparison; otherwise false |

Type mismatches (`gt` on a string, `matches` on a number) evaluate to **false**; they never
raise. Compile time catches what it can (§ Static validation). Regular expressions are
compiled at compile time with Python's `re`; a pattern that does not compile is a configuration
error. Patterns are full-match and anchored implicitly.

### Determinism and trust

A condition reads bound data only. It cannot call code, read the environment, or reference
prompt text. Model output can influence a condition only through a validated step result, in
the same way it influences a `cases` route today; the set of targets remains authored.

## Where conditions appear

### 1. Conditional transitions: `route`

A transition may be a direct target, a `cases` match (unchanged), or an ordered `route`:

```yaml
transition:
  route:
    - when:
        binding: {pointer: /flows/extract_fields/result/status}
        equals: invalid
      flow: repair_extraction
    - when:
        all:
          - {binding: {pointer: /payload/form/report_type}, present: true}
          - {binding: {pointer: /payload/form/report_type}, not_equals: custom_report}
      flow: extract_fields
    - flow: classify_report_type          # the last entry has no `when`: the otherwise target
```

Rules: 1..32 entries; every entry but the last has `when`; the last has none (mandatory
otherwise); targets are flows or outcomes exactly as in `cases`. Entries are evaluated in order;
the first true condition selects the target. Scope: workflow boundary scope (`/payload`,
`/metadata`, `/flows/<id>/result` of dominating or optional flows — see `bindings.md`).

`on_unresolved` accepts the same three forms (target, issue map, `route`).

### 2. Routed `start`

`start` may name a flow (unchanged) or be a `route` whose conditions read `/payload` and
`/metadata` only:

```yaml
start:
  route:
    - when: {binding: {pointer: /payload/form/report_type}, present: true}
      flow: extract_fields
    - flow: classify_report_type
```

Outcomes are not allowed as `start` targets (a workflow must run at least one flow).

### 3. Step `when`

A step may declare `when`; a false condition records the step as `skipped` and the flow
continues with the next step:

```yaml
steps:
  - resolve_fields
  - extract
  - check
  - id: repair
    when: {binding: {pointer: /steps/check/result/status}, equals: invalid}
  - id: recheck
    when: {binding: {pointer: /steps/repair/result}, present: true}
```

The `steps` list accepts either a bare step id (as today) or an object `{id, when}`. Scope: flow
scope (`/payload`, `/metadata`, `/steps/<earlier step>`). A binding to the result of a
conditional step (or of a step after it in the same flow) is only allowed with a `default`,
exactly as for steps after a reviewable step today: the compiler knows which steps may be
skipped. The flow `output` may also be a `first_of` over conditional steps (`bindings.md`).

Any step type may be conditional, including model steps: the condition is authored, so the
topology is still reviewed configuration.

### 4. `repeat.until` and `repeat.continue_when`

See `repeat.md`.

## Result model

* A skipped step: `StepResult.status == "skipped"` (exists), no `result`.
* `TransitionResult` gains `route: {kind: "direct" | "cases" | "route" | "review", index: int | null, case: str | null}`
  so a host can see which entry selected the target.
* The condition's evaluated boolean is recorded as a span event (see `observability.md`), not in
  the result.

## Static validation

| Diagnostic | Level | When |
|---|---|---|
| `invalid_condition` | error | not exactly one source, not exactly one operator, empty combinator, nesting deeper than 8, operand of the wrong shape, regex does not compile |
| `condition_type_mismatch` | error | the bound field's static schema type is known and can never satisfy the operator (e.g. `gt` on a `string`-typed field, `matches` on an `integer`, `in` with values of a different type than the field's `enum`) |
| `condition_always_false` / `condition_always_true` | warning | `equals` / `in` against a known `enum` or `const` with no overlap / full overlap |
| `route_without_otherwise` | error | last `route` entry has `when` |
| `route_unreachable_entry` | warning | an entry after the otherwise entry (impossible by shape) or an entry whose condition is statically always false |
| `unavailable_flow_reference` | error | the pointer of a `route` condition references a flow that does not dominate the transition and the leaf has no absent-tolerant semantics — note: conditions tolerate absence, so this applies only to pointers into flows that can **never** have run at that point (not in any path) |

Static types come from the same sources the compiler already uses for bindings: workflow
`input_schema`, handler output schemas, LLM output schemas, MCP output schemas and decision
result contracts.

## Runtime

Evaluation is pure: `evaluate(condition, context) -> bool`. It is implemented once in
`core/conditions.py` and used by transitions, `start`, step `when` and `repeat`. It never raises
for data; only an internal invariant violation (an unknown operator surviving compilation) raises
`ServiceError(INVALID_CONFIGURATION)`.

## Documentation

* `docs/configuration/conditions.md` (new): the language, operators, scopes, examples.
* `docs/configuration/workflows.md`: `route`, routed `start`.
* `docs/configuration/flows.md`: step `when`.
* `docs/integration/results.md`: `TransitionResult.route`, skipped steps.
* `skills/foliqant/references/workflow-design.md` and `process-composition.md`: replace the
  "handler that emits a routing key" idiom by `route` / `when`; new worked mapping.
* Tutorial "Route between flows" (`docs/tutorials/multiflow-routing.md`): extend with a
  condition-based route.
* Example `examples/conditional_intake` (new): routed start, `route`, step `when`, scripted
  model, evaluation cases.

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* `in` treats an absent value as `null`, consistent with `equals`, so
  `in: [null, x]` holds for missing values. Numbers compare by value (`1` equals
  `1.0`); booleans never equal numbers.
* A non-final `route` entry without `when` fails with `misplaced_otherwise`
  (the shape rule); `route_unreachable_entry` reports entries whose condition is
  statically false and entries after an entry that is statically always true.
* A condition pointer that can never resolve under a closed schema is a
  `dangling_pointer` error; an impossible path would silently make the
  condition constant.
* `condition_always_true` / `condition_always_false` distinguish a verdict that
  also holds for an absent value from one that holds only for present values
  (for example `in` covering every enum value); only the former makes a route
  entry unreachable. Messages say which case applies.
* Malformed conditions in `when`, `until` or `continue_when` report
  `invalid_condition` with the safe field path.
* `TransitionResult.route` omits `index` and `case` when they do not apply
  instead of serializing `null` (the library's absence convention). For `review`,
  `case` is the issue that selected an issue-specific target and `index` the
  entry of an `on_unresolved.route`.
* The routed `start` is reported by a `route.selected` event on the workflow
  span and, with the same values, by `ExecutionResult.start: {flow, route: {kind:
  direct|route, index?}}` (omitted for `run_flow` / `run_step`).
* Bounded evaluation (review decision): `matches` compares values of at most
  1024 characters; a longer value is false and traced as
  `condition.type_mismatch` with `reason: value_too_long` (other mismatches carry
  `reason: incompatible_type`). The compiler walks the `re._parser` tree and
  rejects unbounded backtracking with the reason `unsafe_pattern`: an unbounded
  quantifier (`*`, `+`, `{m,}`) on a group containing a backtracking unbounded
  quantifier, a variable-length ambiguous part or an alternation whose branches
  may overlap; or estimated choices above `(1024 + 1)^3`, the work of three
  independent unbounded quantifiers. A bounded outer quantifier (`?`, `{m,n}`)
  is allowed and costs bound × inner work, except that ambiguous bounded
  iterations multiply (`(a{1,2}){1,4}` passes, `(a{1,2}){1,40}` fails).
  Alternations of equal fixed-width branches with distinct first literals are
  deterministic (`(?:foo|bar)+`). Possessive quantifiers are not backtracking;
  atomic groups and lookarounds are checked inside. Matching is
  `pattern.fullmatch(value)` on the bounded value; timing tests keep
  `\d+(?:\.\d+)?`, `[a-z]+(?:-[a-z]+){0,3}` and `(a{1,2}){1,4}` within 50 ms
  on 1024-character adversarial input.
* A validation error is `invalid_condition` only when it lies in a condition
  position (step or route `when`, `repeat.until`, `retry.continue_when`); a
  business input or output key named `when` or `until` reports
  `invalid_contract`.
* `describe_condition` keeps the polarity of `present` / `empty`
  (`present=false`); explain JSON reports it as the leaf's `operand`.

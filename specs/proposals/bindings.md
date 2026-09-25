# Bindings: `first_of`, object outputs, `default` implies optional

## Motivation

After alternative branches, the flow that continues must declare every branch's result as
optional and let a handler pick the one that ran. A flow can project only one pointer, so a
result object that combines several step results also needs a handler. Both are projection,
not policy, and belong in configuration.

## `default` implies optional

The `optional` flag is removed. A pointer binding is optional exactly when it declares
`default`; a binding without `default` is required. `default: null` is a valid default.

```yaml
lookup: {pointer: /flows/lookup_fund/result, default: null}     # optional
identifier: {pointer: /payload/identifier}                      # required
```

Diagnostics: `unknown_field` for `optional` (the field no longer exists); `unavailable_flow_reference`
unchanged for required pointers into non-dominating flows.

## `first_of`

```yaml
lookup:
  first_of:
    - {pointer: /flows/lookup_corrected_fund/result}
    - {pointer: /flows/lookup_fund/result}
  default: null
```

Resolves to the value of the first member that is **present** (resolves and is not `null`);
otherwise to `default`; without `default` the binding is required and a run with no present
member fails with `missing_binding` (as a required pointer does). Members are pointer bindings
without their own `default` (1..16). `first_of` is allowed wherever a binding is: flow and step
`input`, `arguments`, `output` fields, decision `sources` (with `format`), condition sources.

Static rule: without `default`, at least one member must reference a dominating scope
(`/payload`, `/metadata`, a dominating flow or an unconditional earlier step); otherwise
`unavailable_flow_reference`. Type checks apply to every member.

## Object outputs

Flow and workflow `output` accept either a single binding (unchanged) or `fields`:

```yaml
output:
  fields:
    status: {pointer: /steps/check/result/status}
    fields: {pointer: /steps/check/result/fields}
    repaired: {pointer: /steps/repair/result, default: null}
```

`fields` is a non-empty map of id → binding (any binding form). The projected value is an
object with exactly these keys. The same dominance rules as for `input` apply: a field that
reads a reviewable or conditional step needs a `default`. Boundary bindings of later flows can
point into the object (`/flows/extract_fields/result/status`), and the compiler knows the
object's static schema (built from the member types) for route coverage checks
(`static-checks.md`).

## Documentation

* `docs/configuration/context.md`: `default`, `first_of`, object outputs; remove `optional`.
* `docs/configuration/flows.md` and `workflows.md`: `output.fields`.
* `skills/foliqant/references/workflow-design.md` (Bindings and scope) and
  `runtime-configuration.md`.
* Every example and tutorial that uses `optional: true` is updated.

## Implementation notes (normative text now in `../runtime.md`)

Implemented as specified, with these refinements:

* Every unknown field, not only `optional`, reports `unknown_field` with the
  safe field path.
* A `first_of` member that resolves to `null` is skipped like a missing one;
  a plain pointer still treats `null` as present.
* For a conditional step, `/steps/<id>` and `/steps/<id>/status` stay available
  without a default; `result`, `selection` and the other record fields need one.
* A flow output reading the first step still needs a default when that step is
  conditional.

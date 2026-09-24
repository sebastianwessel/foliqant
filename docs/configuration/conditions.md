# Decide with conditions

A condition is a small, closed test over data the configuration already binds:
"the lookup found the fund", "the form names a report type", "the check found
no issues". Conditions select routes, choose the starting flow, skip steps and
stop a bounded repeat. They read validated values only; they cannot call code,
read the environment, reference prompt text or evaluate expressions. The set of
possible targets always stays authored configuration.

## Write a condition

A **leaf** has exactly one source and exactly one operator:

```yaml
binding:
  pointer: /flows/lookup_fund/result/status
equals: found
```

The source is either `binding` (a pointer, or `first_of` candidates, without a
default) or `literal`. Combine leaves with `all`, `any` and `not`:

```yaml
all:
  - binding:
      pointer: /payload/form/report_type
    present: true
  - not:
      binding:
        pointer: /payload/form/report_type
      equals: custom_report
```

`all` and `any` take 1 to 32 conditions and evaluate them in authored order,
stopping at the first decisive result. Conditions nest at most eight levels,
counting the leaf.

## Absent values

A pointer that does not resolve, or resolves to JSON `null`, is **absent**.
Every operator defines a result for an absent value, so a condition always
evaluates and never fails a run because data is missing. This is what lets a
route read the result of a flow that may not have run.

## Operators

| Operator | Operand | True when |
| --- | --- | --- |
| `present` | `true` / `false` | the value is present (`false` negates) |
| `empty` | `true` / `false` | the value is absent, `""`, `[]` or `{}` (`false` negates) |
| `equals` | any JSON value | deep equality without coercion; absent equals `null` |
| `not_equals` | any JSON value | negation of `equals` |
| `in` | 1 to 64 JSON values | the value equals one element; absent equals `null` |
| `not_in` | 1 to 64 JSON values | negation of `in` |
| `gt`, `gte`, `lt`, `lte` | number | the value is a number and the comparison holds |
| `matches` | regular expression, at most 256 characters | the value is a string of at most 1024 characters and the pattern matches all of it |
| `length` | one of `gt`, `gte`, `lt`, `lte`, `equals` with an integer | the value is a string, array or object and its length satisfies the comparison |

Equality never coerces: `"1"` does not equal `1`, and `true` does not equal
`1`. Numbers compare by value, so `1` equals `1.0`. `matches` is anchored
implicitly: `FOI-[0-9]+` does not match `see FOI-12`. Patterns use Python
regular expressions and must compile when the configuration is validated.

### Bounded evaluation

A condition evaluates in bounded time, also for `matches` on untrusted text:

- `matches` compares strings of at most 1024 characters. A longer value is
  **false** and reported as a `condition.type_mismatch` event with
  `reason: value_too_long`.
- Validation rejects patterns whose backtracking can grow without bound, with
  the error reason `unsafe_pattern`:
  - an unbounded quantifier (`*`, `+`, `{m,}`) on a group that contains an
    unbounded quantifier, a variable-length ambiguous part or an alternation
    whose branches may overlap, such as `(a+)+`, `(a{1,2})*` or `(a|ab)*`;
  - patterns whose choices multiply beyond the work of three independent
    unbounded quantifiers, such as `a*a*a*a*b` or `(a{1,2}){1,40}`.
- Bounded quantifiers (`?`, `{m,n}`) on a group are fine and count as
  bound × inner work: `\d+(?:\.\d+)?`, `[a-z]+(?:-[a-z]+){0,3}` and
  `(a{1,2}){1,4}` pass.
- Alternations of equal fixed-width branches with distinct first characters
  match deterministically, so `(?:foo|bar)+` passes; single-character
  alternatives (`(a|b)*`) compile to a character class.
- Possessive quantifiers (`++`, `*+`, `?+`) never give back what they matched,
  so they are allowed inside unbounded groups: `(?:\d++,)*`.

```yaml
binding:
  pointer: /steps/extract/result/reference
matches: "FOI-[0-9]{4}-[0-9]{4}"
```

```yaml
binding:
  pointer: /steps/search/result/candidates
length:
  lt: 3
```

A comparison that meets an incompatible value at run time (for example `gt` on
a string) is **false**, never an error. The runtime reports it as a
`condition.type_mismatch` event with `reason: incompatible_type` (or
`value_too_long` for an overlong `matches` value) so you can see it in traces
and logs.

## Read several candidates

Use `first_of` as a condition source when the value may come from one of
several places. The first member that resolves to a non-null value is used;
if none does, the source is absent:

```yaml
binding:
  first_of:
    - pointer: /flows/lookup_corrected_fund/result/status
    - pointer: /flows/lookup_fund/result/status
equals: found
```

## Where conditions appear

| Position | What it decides | Pointer scope |
| --- | --- | --- |
| [`transition.route`](workflows.md#route-on-conditions) | the next flow or outcome after a completed flow | `/payload`, `/metadata`, `/flows/{id}/result` of flows that may have run |
| [`on_unresolved.route`](workflows.md#route-review) | the review target after a flow stops for review | same as `transition.route` |
| [`start.route`](workflows.md#choose-the-first-flow) | the first flow | `/payload` and `/metadata` only |
| [step `when`](flows.md#skip-a-step-with-when) | whether a step runs | `/payload`, `/metadata`, `/steps/{id}` of earlier steps |
| [`repeat.until`](repeat.md) | whether a repeated flow stops | workflow scope including the flow's own `/flows/{id}/result` and `/attempts` |
| [`repeat.retry.continue_when`](repeat.md) | whether repetition continues after the retry flow | the same, plus the retry flow's result |

Because conditions tolerate absence, a route may read a flow that did not run
on every path. It may not read a flow that can **never** have run at that point
(a later flow, or a callable flow): compilation fails with
`unavailable_flow_reference` or `invalid_flow_reference`.

## Checked before any request

Validation compiles every condition and checks it against the static schemas
of the bound fields (workflow input, flow outputs, handler, MCP, LLM and
decision result schemas):

| Code | Level | Meaning |
| --- | --- | --- |
| `invalid_condition` | error | not exactly one source and one operator, an empty combinator, nesting deeper than eight, a malformed operand or an invalid pattern |
| `unsafe_pattern` | error | a `matches` pattern whose backtracking is not bounded (see [bounded evaluation](#bounded-evaluation)) |
| `condition_type_mismatch` | error | the field's known type can never satisfy the operator, such as `gt` on a string field or `equals: "3"` on an integer |
| `dangling_pointer` | error | the pointer can never resolve under a closed schema |
| `condition_always_false` / `condition_always_true` | warning | `equals` or `in` against a known `enum`/`const` with no overlap, or covering every value |
| `route_unreachable_entry` | warning | a route entry can never be selected |

Run `foliqant validate --config config/settings.yaml` to see diagnostics, and
`foliqant explain --format mermaid` to see conditions on the graph. Telemetry
and `explain` show conditions as pointers and operators only, never operand
values.

Continue with [workflows](workflows.md) to route on conditions, or with
[repeat](repeat.md) to retry a flow until a condition holds.

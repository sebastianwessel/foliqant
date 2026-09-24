# 3. Route to the right support branch

Now make the category select a different flow. The model reports a category;
the workflow owns the allowed destinations and an explicit review default.

In `my_support/config/support_email/workflow.yaml`, set `start: classify`
and replace the `classify` transition with:

```yaml
transition:
  binding:
    pointer: /flows/classify/result
  cases:
    billing:
      flow: billing
    cancellation:
      flow: cancellation
  default:
    outcome: needs_review
```

Add `billing` and `cancellation` to the same `flows` mapping:

```yaml
billing:
  input:
    message:
      pointer: /payload/message
  transition:
    outcome: completed
cancellation:
  input:
    message:
      pointer: /payload/message
  transition:
    outcome: completed
```

Every flow needs a decision about what happens when it stops for review. Say it
once for the whole workflow instead of repeating `on_unresolved` on every flow:

```yaml
defaults:
  model: local_qwen
  on_unresolved:
    outcome: needs_review
```

A flow with its own `on_unresolved` keeps it; the others inherit the default.

Restore `classify/flow.yaml` to its one-step form from chapter 1. Move the
extraction step and schema from `classify/` into
`my_support/config/support_email/billing/`, then copy them into
`my_support/config/support_email/cancellation/`. Adjust each instruction to
name the selected action. Create `flow.yaml` in each new directory:

```yaml
steps:
  - extract
output:
  pointer: /steps/extract/result
```

These two branch flows are alternative paths. Each gets the original email
through its own workflow input binding. A workflow boundary cannot reach into
another flow's private `/steps` records. To pass a result between flows, project
it as a flow result and bind it in the workflow.

## Let the compiler check the cases

`classify` projects `/steps/classify/selection/category/id`, and the compiler
knows the catalog: `billing` and `cancellation`. It therefore checks the route:

- a case key that is not a category (for example a typo `biling`) fails with
  `unmatched_case`;
- a category without a case is reported as an `uncovered_value` warning,
  because it would silently fall to `default`.

When falling to `default` is intended, list those values in `default_covers`.
Run `foliqant validate` and read the `diagnostics` in its output.

## Return whichever branch ran

Only one branch runs, so the workflow output reads the first branch result that
exists. `first_of` does this in configuration; no join flow or handler is
needed:

```yaml
output:
  first_of:
    - pointer: /flows/billing/result
    - pointer: /flows/cancellation/result
  default:
    disposition: needs_review
```

If `classify` ends in review, no branch ran and the host receives the default.

## Route on a condition instead of a value

`cases` matches one exact value. When the decision needs more (presence, several
fields, a pattern), use an ordered `route`. The first true `when` wins and the
last entry is the otherwise target. For example, to send messages that carry an
account reference straight to billing and classify the rest:

```yaml
start:
  route:
    - when:
        binding:
          pointer: /payload/message
        matches: ".*account A-[0-9]+.*"
      flow: billing
    - flow: classify
```

The same form works for `transition` and `on_unresolved`, and a single step can
be made conditional with `when`; see [conditions](../configuration/conditions.md).
The tutorial keeps the classification start, because a reference alone does not
say whether the customer wants billing or cancellation.

## See the complete workflow file

This is the complete `my_support/config/support_email/workflow.yaml` from the
finished tutorial:

```yaml
defaults:
  model: local_qwen
  on_unresolved:
    outcome: needs_review
start: classify
input_schema: input.schema.json
output:
  first_of:
    - pointer: /flows/billing/result
    - pointer: /flows/cancellation/result
  default:
    disposition: needs_review
flows:
  classify:
    input:
      message:
        pointer: /payload/message
    transition:
      binding:
        pointer: /flows/classify/result
      cases:
        billing:
          flow: billing
        cancellation:
          flow: cancellation
      default:
        outcome: needs_review
  billing:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
  cancellation:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
```

`output` selects the concise business value that becomes
`ExecutionResult.payload`. It does **not** discard any execution detail. The
same returned `ExecutionResult` always has `flows`: the completed run includes
the classification evidence, every executed branch step, and the skipped
branch. For a billing request, these paths are all available:

```python
result = await app.run("support_email", envelope)

payload = result.payload
classification = result.flows["classify"].steps["classify"].result
account = result.flows["billing"].steps["lookup"].result

assert payload == result.flows["billing"].result
assert result.flows["cancellation"].status == "skipped"
assert result.transitions[0].route.case == "billing"
```

The [input and result contract](../reference/inputs-and-results.md#executionresult-and-nested-records)
shows the complete nesting and the difference between `payload` and the full
execution record.

Compile the new graph without calling a model and look at it:

```sh
uv run --no-sync foliqant validate --config my_support/config/settings.yaml
uv run --no-sync foliqant explain --config my_support/config/settings.yaml --workflow support_email --format mermaid
```

For the invoice email, `flows.billing.status` will be
`completed` and `flows.cancellation.status` will be `skipped`. An email with
two current queues should end `needs_review` before either branch runs.
The [`evaluation/dataset.json`](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/evaluation/dataset.json)
records these expected paths.

Continue to [the account lookup](read-only-mcp.md). See
[workflow transitions](../configuration/workflows.md#define-transitions) for all
route forms.

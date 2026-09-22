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
on_unresolved:
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
  on_unresolved:
    outcome: needs_review
cancellation:
  input:
    message:
      pointer: /payload/message
  transition:
    outcome: completed
  on_unresolved:
    outcome: needs_review
```

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
it as a flow result and bind it in the workflow. The final
[`workflow.yaml`](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_email/workflow.yaml)
shows that pattern again when it feeds the final projection.

Compile the new graph without calling a model:

```sh
uv run --no-sync foliqant validate --config my_support/config/settings.yaml
uv run --no-sync foliqant explain --config my_support/config/settings.yaml --workflow support_email
```

For the invoice email, `flows.billing.status` will be
`completed` and `flows.cancellation.status` will be `skipped`. An email with
two current queues should end `needs_review` before either branch runs.
The [`evaluation/dataset.json`](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/evaluation/dataset.json)
records these expected paths.

Continue to [the account lookup](read-only-mcp.md). See
[workflow transitions](../configuration/workflows.md) for other route forms.

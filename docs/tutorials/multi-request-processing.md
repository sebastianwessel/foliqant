# 6. Process several requests in one email

The single-choice `support_email` workflow deliberately reviews an email with
two active queues. Build a separate `support_multi` workflow when you need to
prepare both tasks. It keeps model assessment, trusted planning, read-only
child work, and final disposition distinct:

```text
assess -> plan -> process -> finalize
                    |
                    +-> billing_task       callable
                    +-> cancellation_task  callable
```

The [completed configuration](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_email_tutorial/config/support_multi)
is runnable offline. Add these directories under
`my_support/config/support_multi/`, each with a `flow.yaml`: `assess/`,
`plan/`, `process/`, `finalize/`, `billing_task/`, and
`cancellation_task/`. Give `support_multi/input.schema.json` the same
message-only schema as `support_email/input.schema.json`. In
`support_multi/workflow.yaml`, set `start: assess`, route the four ordinary
flows in order, and declare the two child flows with `callable: true`. The
[workflow file](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_multi/workflow.yaml)
shows the required cross-flow input bindings. `plan` receives only
`/flows/assess/result`; `process` receives `/flows/plan/result/items`;
`finalize` receives both the plan and `/flows/process/result`.

## Assess the email

Create `assess/identify.step.yaml` as a `decision` with
`question.type: request_units`, a named `message` source, and a catalog of
`billing` and `cancellation`. Its criteria require one unit per active action,
source-order IDs, explicit account references as subjects, and preservation
of withdrawn, conditional, or related requests. The
[complete step](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/config/support_multi/assess/identify.step.yaml)
is short enough to use directly. `assess/flow.yaml` lists `identify` and
projects its result. For the two-account email, the result has two active
units. No lookup happens during assessment.

## Plan allowed work in trusted code

Create `plan/plan.step.yaml`:

```yaml
type: handler
handler: plan_support_requests
input:
  assessment:
    pointer: /payload/assessment
```

Register the handler before `prepare_application`. The
[support policy](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/multi_policy.py)
validates the typed `RequestUnitsResult`, holds uncertainty and related or
conditional work, ignores withdrawn units and exact repeats of category,
account reference, and description, and requires an
explicit `A-<digits>` account reference. It emits at most eight ordered
`{id, flow, input}` items. This is application policy, so review these rules
for your own account IDs before adapting it. For the sample message, the plan
contains:

```yaml
items:
  - id: task_1
    flow: billing_task
    input:
      account_reference: A-100
  - id: task_2
    flow: cancellation_task
    input:
      account_reference: A-200
```

`request_ids` correlates task IDs to assessment units. `held` and `ignored`
stay in the plan for final disposition. The planner does not perform work.

## Run a bounded read-only collection

Create `process/requests.step.yaml`:

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - billing_task
  - cancellation_task
max_items: 8
```

Each callable flow has a closed `input.schema.json` requiring
`account_reference`, a direct `lookup.step.yaml` calling the same reviewed
`account_records.lookup_account` tool from chapter 4, and a
`prepare.step.yaml` bound to the validated lookup result. That trusted
`prepare_support_task` handler returns a queue, reference, plan, and a
`next_step` for a human to review. The
[billing child](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_email_tutorial/config/support_multi/billing_task)
and [cancellation child](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_email_tutorial/config/support_multi/cancellation_task)
show the exact files. Neither child changes an account or sends an email.
The collection executes its items sequentially under the parent budget and
keeps their ordered ledger at `/flows/process/result/items`.

## Decide from the ledger

Create `finalize/disposition.step.yaml` as a handler bound to the plan and
collection. The example's `decide_support_disposition` checks that every
planned `(id, flow)` appears in the ledger in order. It maps completed tasks
back to request-unit IDs and carries held work into `review`. It projects
`ready`, `partial_review`, `review`, or `no_action`. A technical collection
failure stops before this handler; it retains a partial ledger rather than
inventing a business result.

Try both checkpoints from the repository root:

```sh
uv run --no-sync python -m examples.support_email_tutorial.run --multi
uv run --no-sync python -m examples.support_email_tutorial.evaluate
```

The first result is `ready` with two ordered child items and two tool calls.
The synthetic partial case, `Review invoice INV-7 for account A-100 and cancel
renewal for my other account.`, prepares billing, holds cancellation for a
missing reference, returns `partial_review`, and makes one tool call. The
[evaluation gold](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/evaluation/dataset.json)
checks both cases. See [flow collections](../steps/flow-collection.md) for
ledger semantics, then [evaluate and integrate](evaluate.md).

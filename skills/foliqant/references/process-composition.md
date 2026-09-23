# Compose a business process

Use this reference when a use case needs routing, multiple requested actions,
reusable child flows, or a final disposition. Configuration controls execution;
the model supplies validated observations for that configuration and trusted
application policy to consume.

## Contents

- [Choose the composition](#choose-the-composition)
- [Route after single-choice triage](#route-after-single-choice-triage)
- [Process independent requests](#process-independent-requests)
- [Implement planning and disposition](#implement-planning-and-disposition)
- [Check the complete process](#check-the-complete-process)

## Choose the composition

| Business requirement | Runtime pattern |
| --- | --- |
| Exactly one queue owns a request | `choice`, project its answer, then an exact-match flow transition. Ambiguity takes an explicit review route. |
| Several labels describe one request | `multiselect`; a trusted rule can select one coordinated process from the label set. Labels do not create jobs automatically. |
| One request needs several ordered activities | Several ordered steps or sequential routed flows; use explicit bindings for previous results. |
| Multiple independent actions, possibly sharing a category | `request_units` → trusted planner → `flow_collection` → trusted disposition. |
| Two intents jointly require one special process | A trusted policy maps that combination to a named coordinated flow; separate it from independent work. |
| One action depends on another or changes a later action | Author that dependency as a coordinated sequence or hold it for review. A collection is not a dependency scheduler. |

One workflow is the externally invoked capability. Routed flows form its finite
graph; callable flows belong to that same workflow and are invoked by a collection.
There is no transition that invokes a different workflow. Reuse an explicit flow
definition file when several workflows need the same sequence. The host can call
different workflows as separate runs, but then owns their coordination and result
aggregation. Do not implement collection composition by calling `app.run_flow`
inside a handler: it starts an independent root run with separate budgets.

## Route after single-choice triage

For a `classify` choice step in `triage/flow.yaml`, project its selected category:

```yaml
steps:
  - classify
output:
  pointer: /steps/classify/result/answer/optionId
  optional: true
  default: null
```

In `workflow.yaml`, this routed flow instance selects one branch:

```yaml
triage:
  input:
    message:
      pointer: /payload/message
  transition:
    binding:
      pointer: /flows/triage/result
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

This is a `flows` map entry, not a complete workflow. Declare both target flows.
Give each branch explicit input bindings; one branch can reach a finalizer while
the other remains skipped. Finalizer bindings to alternative branch outputs need
`optional: true` with explicit defaults. Its handler must handle the absent
branch rather than concatenate two presumed successes.

An optional binding handles a missing path, not a present `null`. The terminal
`needs_review` above is authored business policy. An output default alone neither
routes the process nor recovers a failed step. Never route on free-text reasons
or evidence strength. Exact-match transitions take string keys; use a handler to
map a label array or category combination to one explicit string route.

## Process independent requests

Keep assessment and planning in separate routed flows. An unresolved decision
stops the remaining steps in its flow, so a planner placed immediately after it
would be skipped. Explicitly route `on_unresolved` to the planning flow when the
policy must inspect incomplete assessments.

This complete workflow graph uses a model profile named `local` and conventional
flow definitions in the directories listed below. The final payload is a business
projection; `ExecutionResult.flows` still contains the complete root records.

```yaml
defaults:
  model: local
start: assess
output:
  pointer: /flows/finalize/result
  optional: true
  default:
    disposition: review
flows:
  assess:
    input:
      message:
        pointer: /payload/message
    transition:
      flow: plan
    on_unresolved:
      flow: plan
  plan:
    input:
      assessment:
        pointer: /flows/assess/result
    transition:
      flow: process
  process:
    input:
      items:
        pointer: /flows/plan/result/items
    transition:
      flow: finalize
    on_unresolved:
      flow: finalize
  finalize:
    input:
      plan:
        pointer: /flows/plan/result
      collection:
        pointer: /flows/process/result
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
  billing_task:
    callable: true
  cancellation_task:
    callable: true
```

Supply these definitions and register the named handlers in the host:

| File | Ordered steps and output |
| --- | --- |
| `assess/flow.yaml` | `identify`: a `request_units` decision with described billing/cancellation categories and explicit criteria; project `/steps/identify/result`. |
| `plan/flow.yaml` | `plan`: trusted handler taking `/payload/assessment`; project `/steps/plan/result`. |
| `process/flow.yaml` | `requests`: collection below; project `/steps/requests/result`. |
| `finalize/flow.yaml` | `disposition`: trusted handler taking `/payload/plan` and `/payload/collection`; project `/steps/disposition/result`. |
| `billing_task/flow.yaml`, `cancellation_task/flow.yaml` | The application's read-only preparation steps, with an input schema for the planner's supplied object and an explicit result projection. |

`process/requests.step.yaml`:

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - billing_task
  - cancellation_task
max_items: 8
```

The cap of 8 is an example policy; the package default is 32. These callable
flows prepare work. Actual account changes or external fulfillment belong to the
host; the current handler and MCP execution boundary permits read effects only.

## Implement planning and disposition

The planner consumes `RequestUnitsResult` from `foliqant.contracts.decisions`.
Validate its input and output through `HandlerRegistration` schemas. Preserve
the assessment and return an application-owned plan containing:

- `items`: ordered `FlowCollectionItem` values (`id`, `flow`, object `input`)
  from `foliqant.contracts.execution`;
- a mapping from safe runtime task IDs such as `task_1` to original request IDs;
- held and ignored requests with explicit policy reasons;
- the original assessment for disposition and traceability.

Define which assessment statuses may produce actionable work. Check unit status
(`active`, `withdrawn`, `conditional`, `quoted`), category, required subject,
relations, duplicates, and item limits. Deduplication is business policy: two
requests in one category can be different work. Do not invent rules for unclear
combinations; ask for the policy while implementing independent parts.

Return a successful planning `StepOutcome` containing held work when the authored
process must continue to collection and disposition. Setting `needs_review=True`
on that planner would instead activate the planning flow's unresolved route.
An empty item list is valid and completes; held work must remain in the plan.

The final handler validates `FlowCollectionResult` and compares its ordered
item IDs and flow IDs with the planned items. Reject missing, duplicated, or
unexpected ledger entries. Combine **all** held, ignored, completed, and reviewed
work using explicit business rules. For example, the application may distinguish
`ready`, `partial_review`, `review`, and `no_action`; these are application values,
not framework statuses. Return `StepOutcome(..., needs_review=True)` whenever
that policy requires review, not just a payload containing the word `review`.

| What happened | Framework behavior and caller responsibility |
| --- | --- |
| Child completes | Its record remains in the collection ledger; later items run. |
| Child needs review | Later independent items run; collection then needs review. The explicit `process.on_unresolved` above reaches disposition. |
| Child fails technically | Collection stops; remaining items are skipped. The host receives `execution.status == "failed"` and reads `/flows/process/steps/requests/partial_result/items`. Disposition does not run. |
| All work is held or ignored | Empty collection completes. Disposition still accounts for the plan and determines review versus no action. |

No implicit parallelism, join, rollback, durable retry, or cross-request memory
is supplied. Do not convert a partial ledger or a timeout into fulfilled business
work. Preserve root `execution.status` even if a payload has a benign default.

## Check the complete process

Compile and explain the graph with the host's handler registrations. Test at
least the actual branches, ambiguous triage, two independent requests, two
requests in one category, related/conditional/withdrawn/quoted units, held work,
an empty collection, a child review, and a child technical failure. Verify the
selected branch and skipped branches, final disposition, and complete ledger.

Use scripted model and tool adapters for offline execution. Add independent gold
for pipeline disposition and routes, request-unit assessment, planner policy,
callable-flow input/output, and collection behavior. Do not count repeated scopes
as independent examples or claim these wiring tests establish model accuracy.

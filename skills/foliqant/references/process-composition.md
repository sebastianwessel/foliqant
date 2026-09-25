# Compose a business process

Use this reference when a use case needs routing, multiple requested actions,
reusable child flows, or a final disposition. Configuration controls execution;
the model supplies validated observations for that configuration and trusted
application policy to consume.

## Contents

- [Choose the composition](#choose-the-composition)
- [Route after single-choice triage](#route-after-single-choice-triage)
- [Decide with conditions instead of handlers](#decide-with-conditions-instead-of-handlers)
- [Bounded retry](#bounded-retry)
- [Process independent requests](#process-independent-requests)
- [Implement planning and disposition](#implement-planning-and-disposition)
- [Check the complete process](#check-the-complete-process)

## Choose the composition

| Business requirement | Runtime pattern |
| --- | --- |
| Exactly one queue owns a request | `choice`, project its selection, then an exact-match `cases` transition with coverage checks. Ambiguity takes an explicit review route. |
| A step is needed only in some cases | Step `when` in the same flow; later bindings to it declare a default. |
| The next flow depends on several facts | An ordered `route` with conditions. |
| A lookup may need a corrected input | `repeat` with `until`, a callable retry flow and `retry_input`. |
| Several labels describe one request | `multiselect`; a trusted rule can select one coordinated process from the label set. Labels do not create jobs automatically. |
| One request needs several ordered activities | Several ordered steps or sequential routed flows; use explicit bindings for previous results. |
| Multiple independent actions, possibly sharing a category | `request_units` → trusted planner → `flow_collection` → trusted disposition. |
| Two intents jointly require one special process | A trusted policy maps that combination to a named coordinated flow; separate it from independent work. |
| One action depends on another or changes a later action | Author that dependency as a coordinated sequence or hold it for review. A collection is not a dependency scheduler. |

One workflow is the externally invoked capability. Routed flows form its finite
graph; callable flows belong to that same workflow and are invoked by a
collection or as a repeat retry flow.
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

This is a `flows` map entry, not a complete workflow. Declare both target flows,
and put the shared review route in the workflow `defaults.on_unresolved` instead
of repeating it on every flow. Give each branch explicit input bindings; only
one branch runs and the other remains skipped. Select whichever branch ran with
`first_of`, without a join flow or handler:

```yaml
output:
  first_of:
    - pointer: /flows/billing/result
    - pointer: /flows/cancellation/result
  default:
    disposition: needs_review
```

A default handles a missing path, not a present `null`. The terminal
`needs_review` above is authored business policy. An output default alone neither
routes the process nor recovers a failed step. Never route on free-text reasons
or evidence strength. `cases` take string keys; route on a label array or a
category combination with `route` conditions (`in`, `length`, `all`/`any`) or,
when the mapping is real business policy, with a trusted handler that returns a
typed field.

## Decide with conditions instead of handlers

A handler whose only output is a routing key, and a flow that exists only to
host it, are configuration. Express the decision on the data the workflow
already binds:

```yaml
transition:
  route:
    - when:
        binding:
          pointer: /flows/extract_fields/result/status
        equals: invalid
      flow: repair_extraction
    - when:
        all:
          - binding:
              pointer: /payload/form/report_type
            present: true
          - binding:
              pointer: /payload/form/report_type
            not_equals: custom_report
      flow: extract_fields
    - flow: classify_report_type
```

Fold "check, repair if invalid, recheck" into one flow with step conditions:

```yaml
steps:
  - check
  - id: repair
    when:
      binding:
        pointer: /steps/check/result/status
      equals: invalid
  - id: recheck
    when:
      binding:
        pointer: /steps/repair/result
      present: true
output:
  fields:
    status:
      first_of:
        - pointer: /steps/recheck/result/status
        - pointer: /steps/check/result/status
      default: invalid
    repaired:
      pointer: /steps/repair/result
      default: null
```

Choose the starting flow from the envelope with `start.route` (conditions read
`/payload` and `/metadata` only). The set of targets stays authored; the model
influences a route only through a validated result.

## Bounded retry

Do not unroll a retry into copies (`lookup_fund`, `correct_identifier`,
`lookup_corrected_fund`). Repeat one flow instance:

```yaml
lookup_fund:
  input:
    identifier:
      pointer: /payload/identifier
  repeat:
    max_attempts: 2
    until:
      binding:
        pointer: /flows/lookup_fund/result/status
      equals: found
    retry:
      flow: correct_identifier
      input:
        lookup:
          pointer: /flows/lookup_fund/result
        message:
          pointer: /payload/message
      continue_when:
        binding:
          pointer: /flows/correct_identifier/result/status
        equals: accepted
    retry_input:
      identifier:
        pointer: /flows/correct_identifier/result/identifier
  transition:
    route:
      - when:
          binding:
            pointer: /flows/lookup_fund/result/status
          equals: found
        flow: enrich
      - flow: request_details
```

`correct_identifier` is a callable flow. The flow's result is its last attempt;
exhaustion is not a review, so route on the result. `/flows/lookup_fund/attempts`
lists every attempt, and later flows read `/flows/correct_identifier/result`
only with a default. Every attempt and retry run consumes `execution.max_steps`;
validation fails with `repeat_budget` when the worst case exceeds it. Repeating
a model step without a retry flow is flagged `repeat_without_retry`. `repeat`
is not a retry policy for technical failures: those fail the run so the host
can redeliver.

To retry per collection item, declare the same `repeat` on the callable flow
(`flows.<id>: {callable: true, repeat: ...}`) with another callable flow as
`retry.flow`. Its pointers read the item scope (`/payload` is the item input,
`/flows/<self>/result`, `/flows/<retry>/result`); each ledger entry then shows
`attempts`, `repeat.stopped_by` and `retry`. Do not hand-unroll item retries in
a planner handler.

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

Compile with `foliqant validate --strict` (declared handlers need no
registration) and review `foliqant explain --format mermaid`. Test at
least the actual branches, ambiguous triage, every `route` entry, skipped
conditional steps, each repeat stop reason (`until`, `exhausted`,
`continue_when`, review), two independent requests, two
requests in one category, related/conditional/withdrawn/quoted units, held work,
an empty collection, a child review, and a child technical failure. Verify the
selected branch and skipped branches, final disposition, and complete ledger.

Use scripted model and tool adapters for offline execution. Add independent gold
for pipeline disposition and routes, request-unit assessment, planner policy,
callable-flow input/output, and collection behavior. Do not count repeated scopes
as independent examples or claim these wiring tests establish model accuracy.

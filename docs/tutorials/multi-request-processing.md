# 6. Process several requests conservatively

A message can contain several independent requests, duplicates, withdrawals,
and dependencies. Model this as assessment, policy planning, bounded collection,
and final disposition. Do not make a model-selected list equivalent to
permission to run work.

## Map the business boundaries

The example uses four routed flows and two callable flows:

```text
assess -> plan -> process -> finalize
                    |
                    +-> lookup_status      callable
                    +-> prepare_guidance   callable
```

- `assess` returns typed request units with status, category, subject, and
  relations.
- `plan` is a trusted handler that applies application policy.
- `process` invokes only allowlisted callable flows, sequentially.
- `finalize` decides `ready`, `partial_review`, `review`, or `no_action` from the
  plan and complete collection ledger.

Create these definitions below `config/intake/`: `workflow.yaml`, routed flow
directories `assess/`, `plan/`, `process/`, and `finalize/`, plus callable flow
directories `lookup_status/` and `prepare_guidance/`. The workflow binds each
completed result explicitly into the next flow.

Callable flows cannot be workflow route targets:

```yaml
flows:
  lookup_status:
    callable: true
  prepare_guidance:
    callable: true
```

## Keep planning in trusted code

The planner receives the complete assessment and language. Its handler validates
typed input, merges exact duplicates, ignores withdrawals and quoted requests,
and holds unsupported, conditional, related, or incomplete work for review. It
emits ordered items shaped as `id`, `flow`, and `input`.

```yaml
type: handler
handler: plan_requests
input:
  assessment:
    pointer: /payload/assessment
  language:
    pointer: /payload/language
```

Collection task IDs use `task_1`, `task_2`, and so on; a `request_ids` map
correlates each task to the original request-unit ID without rewriting the
assessment. That policy is application-specific. Change it deliberately in host code and
update gold; do not hide it in a prompt.

The planner keeps policy in ordinary Python. Its guards record why a request
is held or ignored before creating any allowed work:

```python
def plan_requests(value: PlanningInput) -> RequestPlan:
    assessment = value.assessment
    plan = RequestPlan(items=[], request_ids={}, held=[], ignored=[], assessment=assessment)
    if assessment.answerability.status != "answerable" or assessment.answer is None:
        plan.held.append(HeldRequest(id="assessment", reason="incomplete_assessment"))
        return plan
    if assessment.answer.relations:
        plan.held.append(HeldRequest(id="assessment", reason="related_requests"))
        return plan
    seen: set[tuple[str, str | None]] = set()
    for unit in assessment.answer.units:
        if unit.status in {"withdrawn", "quoted"}:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason=unit.status))
            continue
        if unit.status == "conditional":
            plan.held.append(HeldRequest(id=unit.id, reason="conditional_request"))
            continue
        if unit.categoryId not in {"request_status", "guidance"}:
            plan.held.append(HeldRequest(id=unit.id, reason="unsupported_request"))
            continue
        if unit.categoryId == "request_status" and (
            unit.subject is None or re.fullmatch(r"FOI-[0-9]{4}-[0-9]{4}", unit.subject) is None
        ):
            plan.held.append(HeldRequest(id=unit.id, reason="missing_reference"))
            continue
        key = (
            unit.categoryId,
            unit.subject if unit.categoryId == "request_status" else None,
        )
        if key in seen:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason="duplicate"))
            continue
        seen.add(key)
        if len(plan.items) >= 8:
            plan.held.append(HeldRequest(id=unit.id, reason="too_many_requests"))
            continue
        inputs: dict[str, JsonValue] = {"language": value.language}
        flow = "prepare_guidance"
        if unit.categoryId == "request_status":
            inputs["reference"] = unit.subject
            flow = "lookup_status"
        item_id = f"task_{len(plan.items) + 1}"
        plan.items.append(FlowCollectionItem(id=item_id, flow=flow, input=inputs))
        plan.request_ids[item_id] = unit.id
    return plan
```

The async wrappers validate strict Pydantic input, freeze the returned value,
and register schemas for all three handlers. Reuse the complete registration
when preparing the example:

```python
from pathlib import Path

from examples.multi_request_processing.policy import HANDLERS
from foliqant import prepare_application

prepared = prepare_application(Path("config/settings.yaml"), handlers=HANDLERS)
```

## Invoke the bounded collection

The process flow contains one collection step:

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - lookup_status
  - prepare_guidance
max_items: 8
```

Each item chooses only one allowlisted callable flow. The runtime rejects
invalid or duplicate item IDs, invalid child input, and more than the compiled
limit. It executes items sequentially with the shared root deadline and budgets;
this is not parallel fan-out or a second workflow.

A collection step has `kind: flow_collection`. Completed or review results keep
the ordered child ledger under `result.items`; a failed collection preserves the
records produced through failure under `partial_result.items`, including failed
and skipped items. Each item records its `id`, `flow`, steps, status, result when
present, and usage.

## Decide from the full ledger

The disposition handler receives both the original plan and collection result.
This makes held and ignored units visible alongside completed child work and
prevents partial success from silently becoming full completion. A technical
collection failure stops the workflow before this handler; the caller receives
the failure and partial ledger, without a fabricated business disposition.

It first verifies that `(id, flow)` pairs in the ledger exactly equal the plan
in order. It maps completed task IDs back through `request_ids`, adds held or
noncompleted requests to review, then selects disposition with this policy:

```python
disposition = (
    "partial_review"
    if review and prepared
    else "review"
    if review
    else "ready"
    if prepared
    else "no_action"
)
return StepOutcome(result, needs_review=bool(review))
```

For the demo input
`{"message":"Check FOI-2026-0142 and explain how to apply.","language":"en"}`,
the offline checkpoint is a completed workflow with disposition `ready`, two
ordered collection items, three model requests, and one tool call.

Run the end-to-end scripted example and its English/German gold:

```sh
python -m examples.multi_request_processing.run
python -m examples.multi_request_processing.evaluate
```

The evaluator can target the pipeline, routed flows, callable flows, collection
step, and nested child operations. Nested checks carry `invocation_path` so
repeated calls to the same flow and step remain distinguishable. Parent run usage
is counted once; grouped child summaries are diagnostic views and must not be
added to it.

Study
[`examples/multi_request_processing`](https://github.com/sebastianwessel/foliqant/tree/main/examples/multi_request_processing)
before adapting its conservative public-record policy to another domain.

# What the compiler guarantees

Foliqant compiles every workflow before any model, tool or handler runs. The
compiler does not judge whether a route is the *right* business decision, but
it proves that the logical flow is sound: the graph has no loops and no dead
ends, every flow can run, every state a route names exists, every repetition
and collection is bounded, and every binding that must hold a value can resolve
on every path that reaches it. A configuration that breaks one of these rules
does not load.

This page lists each guarantee with its diagnostic code, a minimal failing
example and the fix. Every row has a focused test in
[`tests/test_config_guarantees.py`](https://github.com/sebastianwessel/foliqant/blob/main/tests/test_config_guarantees.py),
and every example on this page is compiled by that test module.

## How problems are reported

A problem has a stable `code`, a `level`, the authored file with line and
column, the key path of the offending `field`, a message naming the configured
identifiers involved, and a corrective hint. It renders as one line; this is
the first example below (the cycle):

```text
demo/workflow.yaml:10:7: workflow_cycle at flows.second.transition.flow: The flows form a cycle: `first` -> `second` -> `first`, so a run might never terminate. (hint: Remove route and callable-flow cycles so every invocation terminates.)
```

| Level | Effect |
| --- | --- |
| `error` | Compilation fails; the first error is reported. |
| `warning` | The configuration loads, but `validate --strict` and `prepare_application(..., strict=True)` fail and list **every** warning. |
| `info` | A finding worth reading; never fails. |

Use strict preparation at startup so an invalid configuration hard-fails
before the application accepts work:

```python
import logging
import sys
from pathlib import Path

from foliqant import prepare_application
from foliqant.compiler import CompilationError

try:
    prepared = prepare_application(Path("config/settings.yaml"), handlers=HANDLERS, strict=True)
except CompilationError as error:
    for problem in error.problems:  # structured: code, level, location, field, message, hint
        logging.error(
            "configuration problem",
            extra={
                "code": problem.code,
                "path": problem.location.path,
                "line": problem.location.line,
                "field": problem.field,
            },
        )
    sys.exit(str(error))  # one rendered line per problem
```

`error.problems` holds the problems that make the configuration invalid (the
error, or every warning under `strict`); `error.diagnostics` holds every
finding. An application prepared with `strict=True` stays strict:
`open_application` refuses it if it carries any warning. On the command line,
`foliqant validate --strict` prints the same lines on standard error, the JSON
status on standard output, and exits with `2`
([exit codes](../reference/runtime-configuration.md#exit-codes)).

## Guarantees at a glance

| Guarantee | Code | Level | Test |
| --- | --- | --- | --- |
| No cycle between flows | `workflow_cycle` | error | `test_cycle_between_flows_is_rejected` |
| No self transition | `workflow_cycle` | error | `test_self_transition_is_a_cycle` |
| No cycle through collections or retry flows | `workflow_cycle` | error | `test_cycle_through_a_retry_flow_is_rejected` |
| Every flow is reachable | `unreachable_flow` | error | `test_unreachable_flow_is_rejected` |
| No flow only behind route entries that can never be selected | `unreachable_flow` | error | `test_flow_behind_a_route_entry_that_can_never_be_selected_is_unreachable` |
| Every route target is a declared flow | `missing_flow` | error | `test_undefined_route_target_is_rejected` |
| The start is a declared routed flow | `missing_start` | error | `test_undefined_start_is_rejected` |
| Routes enter routed flows only | `invalid_routed_flow` | error | `test_routes_cannot_enter_callable_flows` |
| A `route` always selects a target | `route_without_otherwise` | error | `test_route_needs_a_final_otherwise_entry` |
| Only the last `route` entry is the otherwise | `misplaced_otherwise` | error | `test_only_the_last_route_entry_may_omit_when` |
| `cases` always have a `default` | `invalid_contract` (missing `default`) | error | `test_cases_need_a_default_target` |
| Review never completes a run | `review_completes_run` | error | `test_review_never_completes_a_run` |
| Review never re-enters its own flow | `review_route_to_self` | error | `test_review_route_cannot_target_its_own_flow` |
| An inherited review default creates no cycle | `invalid_default_review_route` | error | `test_inherited_review_default_cannot_close_a_cycle_through_a_retried_flow` |
| A run ending in review returns a projected result | `review_ends_run` | warning | `test_review_ending_the_run_without_the_projected_result_is_a_warning` |
| Review ending the output flow returns its result | `review_ends_run` | info | `test_review_returning_the_output_flow_result_is_info` |
| Repeat attempts are bounded (2–64) | `invalid_contract` (`max_attempts`) | error | `test_repeat_attempts_are_bounded` |
| A retry flow is callable, used once and not repeated | `invalid_repeat` | error | `test_retry_flow_must_be_a_callable_flow` |
| A repeat fits the step budget | `repeat_budget` | error | `test_repeat_worst_case_must_fit_the_step_budget` |
| A repeated model step changes its input | `repeat_without_retry` | warning | `test_repeating_a_model_step_without_a_retry_is_a_warning` |
| Collections are bounded (`max_items` 1–1024) | `invalid_contract` (`max_items`) | error | `test_collection_size_is_bounded` |
| Collections nest at most sixteen levels | `collection_depth_exceeded` | error | `test_collection_nesting_depth_is_bounded` |
| A collection fits the step budget, nested work included | `collection_budget` | warning | `test_collection_worst_case_counts_nested_collections` |
| Every path fits the step budget, repeats and collections included | `run_budget` | warning | `test_repeat_around_a_collection_counts_every_attempt` |
| Flow results are bound only where the flow ran | `unavailable_flow_reference` | error | `test_flow_input_needs_a_flow_that_runs_on_every_path` |
| A `first_of` has a member available on every path | `unavailable_flow_reference` | error | `test_first_of_needs_a_member_available_on_every_path` |
| A `cases` binding is a required pointer | `unavailable_flow_reference` | error | `test_cases_binding_is_a_required_pointer` |
| Conditions read only flows that may have run | `unavailable_flow_reference` | error | `test_route_conditions_tolerate_absence_but_not_flows_that_never_ran` |
| A retry flow's result needs a default | `unavailable_flow_reference` | error | `test_retry_flow_result_needs_a_default` |
| Later repeat attempts need a default | `unavailable_value` | error | `test_later_repeat_attempts_need_a_default` |
| A result default holds every key bound from it | `unavailable_value` | error | `test_a_flow_result_default_must_hold_the_selected_key` |
| Results of skipped steps need a default | `unavailable_step_reference` | error | `test_skipped_step_results_need_a_default` |
| A flow output resolves when a step stops for review | `incompatible_output_binding` | error | `test_flow_output_is_projected_on_review_so_later_steps_need_a_default` |
| A selection read by a flow output has a default | `incompatible_output_binding` | error | `test_a_selection_is_missing_when_the_first_step_stops_for_review` |
| No pointer outside a known schema | `dangling_pointer` | error | `test_pointer_outside_a_closed_schema_is_dangling` |
| Pointers name readable flow records | `invalid_flow_reference` | error | `test_attempts_of_a_flow_that_does_not_repeat_are_invalid` |
| Every case key can match | `unmatched_case` | error | `test_case_that_can_never_match_is_rejected` |
| Every allowed value has a case or is listed | `uncovered_value` | warning | `test_allowed_value_without_a_case_is_a_warning` |
| `default_covers` lists exactly the uncovered values | `default_covers_mismatch` | error | `test_default_covers_must_equal_the_uncovered_values` |
| `cases` route strings or null only | `incompatible_route_type` | error | `test_cases_bind_only_strings_or_null` |
| Condition operators fit the field type | `condition_type_mismatch` | error | `test_condition_operand_must_fit_the_field_type` |
| Conditions can vary | `condition_always_false`, `condition_always_true` | warning | `test_condition_that_can_never_hold_is_a_warning` |
| Every route entry can be selected | `route_unreachable_entry` | warning | `test_route_entry_that_can_never_be_selected_is_a_warning` |
| `matches` runs in bounded time | `unsafe_pattern` | error | `test_matches_patterns_must_have_bounded_backtracking` |
| Handler steps call declared contracts | `unknown_handler` | error | `test_handler_steps_need_a_declared_contract` |
| Registrations match their declarations | `handler_contract_mismatch` | error | `test_registration_must_match_its_declaration` |
| Every declared handler is registered before running | `missing_handler_registration` | error | `test_declared_handlers_need_a_registration_to_run` |
| Every LLM input reaches its prompt | `unused_llm_input` | warning | `test_llm_input_not_used_by_the_prompt_is_a_warning` |
| Prompt placeholders name declared inputs | `invalid_prompt` | error | `test_prompt_placeholders_must_name_declared_inputs` |

Two further infos help while authoring: `case_on_unknown_type` (a `cases`
field has no enum or const, so its cases cannot be checked) and
`empty_text_source` (a decision text source may be an empty string).

## Files the examples assume

Every example is a complete `demo/workflow.yaml` next to this `settings.yaml`.
A flow without a `definition` uses the conventional `<flow>/flow.yaml` with one
`note` handler step; the flow `lookup` returns the `lookup` handler result,
`{status: found | not_found, fund}`.

```yaml
# settings.yaml
models:
  local:
    provider: openai_compatible
    model: example-model
    base_url: http://127.0.0.1:8000/v1
    allow_insecure_http: true
    output_mode: native
handlers:
  lookup:
    input_schema:
      type: object
      properties:
        identifier:
          type: string
      required:
        - identifier
    output_schema:
      type: object
      properties:
        status:
          enum:
            - found
            - not_found
        fund:
          type: string
      required:
        - status
      additionalProperties: false
    effect: read
  note:
    input_schema:
      type: object
    output_schema:
      type: object
    effect: read
```

```yaml
# lookup/flow.yaml; lookup/find.step.yaml calls the lookup handler with /payload/identifier
output:
  pointer: /steps/find/result
steps:
  - find
```

## The graph is acyclic

Flows run at most once per run, so a route back to an earlier flow, to itself,
or a callable flow that calls itself again through a collection or a retry
flow, could never terminate.

```yaml
# demo/workflow.yaml: fails with workflow_cycle
start: first
flows:
  first:
    input: {}
    transition:
      flow: second
  second:
    input: {}
    transition:
      flow: first
```

Fix: end one branch with an outcome, or express a bounded retry with
[`repeat`](repeat.md), which keeps the graph acyclic.

## Every flow can run and every state exists

A declared flow must be reachable from a start candidate through a route, a
collection call or a repeat retry; a flow reachable only through `route`
entries whose conditions can never hold (see `route_unreachable_entry`) is
unreachable too. Every route target must be a declared routed flow, and the
start must name one.

```yaml
# demo/workflow.yaml: fails with unreachable_flow
start: first
flows:
  first:
    input: {}
    transition:
      outcome: completed
  orphan:
    input: {}
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: fails with missing_flow
flows:
  first:
    input: {}
    transition:
      flow: missing
```

```yaml
# demo/workflow.yaml: fails with missing_start
start: missing
flows:
  first:
    input: {}
    transition:
      outcome: completed
  second:
    input: {}
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: fails with invalid_routed_flow
flows:
  first:
    input: {}
    transition:
      flow: helper
  helper:
    callable: true
```

Fix: route to the flow, remove it, declare the target, or call a callable flow
from a [`flow_collection`](../steps/flow-collection.md) step.

## Every transition terminates

After a flow completes, its transition always selects exactly one target: a
`route` ends with an entry without `when` and `cases` require a `default`.

```yaml
# demo/workflow.yaml: fails with route_without_otherwise
flows:
  first:
    input: {}
    transition:
      route:
        - when:
            binding:
              pointer: /payload/urgent
            equals: true
          outcome: completed
```

```yaml
# demo/workflow.yaml: fails with misplaced_otherwise
flows:
  first:
    input: {}
    transition:
      route:
        - outcome: completed
        - when:
            binding:
              pointer: /payload/urgent
            equals: true
          outcome: needs_review
```

```yaml
# demo/workflow.yaml: fails with invalid_contract
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      binding:
        pointer: /flows/lookup/result/status
      cases:
        found:
          outcome: completed
```

Fix: add the otherwise entry as the last entry, or a `default` target.

## Review never completes a run and ends visibly

`on_unresolved` never targets `outcome: completed` directly (a review flow may
later complete), never re-enters its own flow, and an inherited
`defaults.on_unresolved` must not route back to a flow that inherits it.

```yaml
# demo/workflow.yaml: fails with review_completes_run
flows:
  first:
    input: {}
    transition:
      outcome: completed
    on_unresolved:
      default:
        outcome: completed
```

```yaml
# demo/workflow.yaml: fails with review_route_to_self
flows:
  first:
    input: {}
    transition:
      outcome: completed
    on_unresolved:
      flow: first
```

```yaml
# demo/workflow.yaml: fails with invalid_default_review_route
defaults:
  on_unresolved:
    flow: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    repeat:
      max_attempts: 2
      until:
        binding:
          pointer: /flows/lookup/result/status
        equals: found
      retry:
        flow: correct
    transition:
      outcome: completed
  correct:
    callable: true
```

A flow without any review route ends the run with `needs_review` when it
stops for review. That is a warning whenever the host would then receive the
workflow output's default, the accepted input, or other flows' results instead
of the projected result of the reviewing flow; it is an info only when the
reviewing flow is the one the workflow output returns.

```yaml
# demo/workflow.yaml: warns with review_ends_run
output:
  pointer: /flows/answer/result
  default: null
start: classify
flows:
  classify:
    input: {}
    transition:
      flow: answer
  answer:
    input: {}
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

Fix: declare `on_unresolved` on the flow, or once as `defaults.on_unresolved`:
a review flow, or `outcome: needs_review` when ending the run is intended.

## Repetition is bounded

A [`repeat`](repeat.md) runs 2–64 attempts. Its retry flow is a callable flow,
serves one repeat and does not repeat itself. The worst case,
`max_attempts × steps + (max_attempts − 1) × retry steps`, must fit
`execution.max_steps` (32 by default).

```yaml
# demo/workflow.yaml: fails with invalid_repeat
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    repeat:
      max_attempts: 2
      until:
        binding:
          pointer: /flows/lookup/result/status
        equals: found
      retry:
        flow: other
    transition:
      flow: other
  other:
    input: {}
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: fails with repeat_budget
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    repeat:
      max_attempts: 17
      until:
        binding:
          pointer: /flows/lookup/result/status
        equals: found
      retry:
        flow: correct
    transition:
      outcome: completed
  correct:
    callable: true
```

Repeating a model step with identical input warns with
`repeat_without_retry`: add a retry flow that changes the input.

```yaml
# demo/workflow.yaml: fails with invalid_contract
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    repeat:
      max_attempts: 100
      until:
        binding:
          pointer: /flows/lookup/result/status
        equals: found
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: warns with repeat_without_retry
defaults:
  model: local
flows:
  answer:
    input:
      message:
        pointer: /payload/message
    definition:
      steps:
        - id: reply
          definition:
            type: llm
            instructions: Answer the message.
            input:
              message:
                pointer: /payload/message
            output: text
    repeat:
      max_attempts: 2
      until:
        binding:
          pointer: /flows/answer/result
        present: true
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

## Collections and whole runs are bounded

A `flow_collection` runs at most `max_items` (1–1024, default 32) items, and
callable flows nest at most sixteen collection levels (`collection_depth_exceeded`).
Two warnings compare worst cases with `execution.max_steps`: `collection_budget`
for one collection step, counting nested collections and item repeats
(`max_items × worst(child)`), and `run_budget` for the most expensive path from
a start to an outcome, counting every step, repeat attempt, retry run and
collection item. Skipped steps are counted, so these are bounds, not
predictions.

```yaml
# demo/workflow.yaml: warns with collection_budget
flows:
  main:
    input: {}
    definition:
      steps:
        - id: each
          definition:
            type: flow_collection
            items:
              pointer: /payload/items
            flows:
              - item
            max_items: 4
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
  item:
    callable: true
    definition:
      steps:
        - id: nested
          definition:
            type: flow_collection
            items:
              pointer: /payload/items
            flows:
              - leaf
            max_items: 8
  leaf:
    callable: true
```

```yaml
# demo/workflow.yaml: warns with run_budget
flows:
  main:
    input: {}
    definition:
      output:
        literal:
          done: false
      steps:
        - id: each
          definition:
            type: flow_collection
            items:
              pointer: /payload/items
            flows:
              - item
            max_items: 10
    repeat:
      max_attempts: 3
      until:
        binding:
          pointer: /flows/main/result/done
        equals: true
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
  item:
    callable: true
```

```yaml
# demo/workflow.yaml: fails with invalid_contract
flows:
  main:
    input: {}
    definition:
      steps:
        - id: each
          definition:
            type: flow_collection
            items:
              pointer: /payload/items
            flows:
              - item
            max_items: 5000
    transition:
      outcome: completed
  item:
    callable: true
```

Fix: lower `max_items` or `max_attempts`, shorten the path, or raise
`execution.max_steps` deliberately.

## Bindings resolve on every path

A binding without `default` is required: the run fails with `missing_binding`
when it has no value. The compiler rejects every required binding that can be
missing on some path:

* a flow result is bound only where the flow dominates the reader (runs on
  every path to it, using a virtual root over all start candidates);
* a `first_of` without default needs one member that is available on every path;
* a `cases` binding is a required pointer (a `route` condition instead
  tolerates absence, but it may read only flows that can have run before it);
* a retry flow runs only after an attempt that did not satisfy `until`, and a
  second attempt may not run, so their records need a default;
* a flow result's `default` must contain every key a later binding selects from
  it, because it is what that binding reads when the flow stopped early;
* inside a flow, results of steps with `when` need a default, and the flow
  output, which is also projected when a step stops for review, reads later
  steps and the first step's `selection` only with a default.

```yaml
# demo/workflow.yaml: fails with unavailable_flow_reference
start: classify
flows:
  classify:
    input: {}
    transition:
      route:
        - when:
            binding:
              pointer: /payload/urgent
            equals: true
          flow: escalate
        - flow: answer
  escalate:
    input: {}
    transition:
      flow: answer
  answer:
    input:
      escalation:
        pointer: /flows/escalate/result
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: fails with unavailable_value
start: check
flows:
  check:
    input: {}
    definition:
      output:
        pointer: /steps/second/result
        default: {}
      steps:
        - id: first
          definition:
            type: handler
            handler: note
            input: {}
        - id: second
          definition:
            type: handler
            handler: note
            input: {}
    transition:
      flow: answer
    on_unresolved:
      flow: answer
  answer:
    input:
      status:
        pointer: /flows/check/result/status
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

```yaml
# demo/workflow.yaml: fails with unavailable_step_reference
flows:
  first:
    input: {}
    definition:
      steps:
        - id: check
          definition:
            type: handler
            handler: note
            input: {}
        - id: repair
          when:
            binding:
              pointer: /steps/check/result/broken
            equals: true
          definition:
            type: handler
            handler: note
            input: {}
        - id: report
          definition:
            type: handler
            handler: note
            input:
              repaired:
                pointer: /steps/repair/result
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: fails with incompatible_output_binding
flows:
  first:
    input: {}
    definition:
      output:
        pointer: /steps/second/result
      steps:
        - id: first
          definition:
            type: handler
            handler: note
            input: {}
        - id: second
          definition:
            type: handler
            handler: note
            input: {}
    transition:
      outcome: completed
```

Fix: add a `default` (it may be `null`), bind a flow that runs on every path,
or add a `first_of` member that does. Give object defaults the keys that later
bindings select:

```yaml
output:
  pointer: /steps/second/result
  default:
    status: unchecked
```

## Pointers stay inside known schemas

Pointers into closed schemas (workflow and flow input schemas, handler, MCP
and LLM output schemas, decision results, object outputs, collection ledgers
and repeat attempts) must name a path that exists. Workflow-boundary pointers
read `/payload`, `/metadata` and `/flows/<id>/result`, plus
`/flows/<id>/attempts` of repeated and retry flows; callable flows have no
workflow-level record.

```yaml
# demo/workflow.yaml: fails with dangling_pointer
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      flow: answer
  answer:
    input:
      owner:
        pointer: /flows/lookup/result/owner
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

```yaml
# demo/workflow.yaml: fails with invalid_flow_reference
start: first
flows:
  first:
    input: {}
    transition:
      flow: second
  second:
    input:
      tries:
        pointer: /flows/first/attempts
    transition:
      outcome: completed
```

## `cases` cover their field

The compiler derives the allowed values of the routed field (enums, consts,
decision categories and predicate answers). Every case key must be one of them;
an allowed value without a case warns unless `default_covers` lists exactly the
uncovered values. A `cases` field must be a string or null on every path,
because any other value fails the run.

```yaml
# demo/workflow.yaml: fails with unmatched_case
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      binding:
        pointer: /flows/lookup/result/status
      cases:
        fonud:
          outcome: completed
      default:
        outcome: needs_review
```

```yaml
# demo/workflow.yaml: warns with uncovered_value
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      binding:
        pointer: /flows/lookup/result/status
      cases:
        found:
          outcome: completed
      default:
        outcome: needs_review
    on_unresolved:
      outcome: needs_review
```

```yaml
# demo/workflow.yaml: fails with default_covers_mismatch
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      binding:
        pointer: /flows/lookup/result/status
      cases:
        found:
          outcome: completed
      default:
        outcome: needs_review
      default_covers:
        - found
```

```yaml
# demo/workflow.yaml: fails with incompatible_route_type
input_schema:
  type: object
  properties:
    priority:
      enum:
        - high
        - 1
flows:
  first:
    input: {}
    transition:
      binding:
        pointer: /payload/priority
      cases:
        high:
          outcome: completed
      default:
        outcome: needs_review
```

Fix: correct the key, add a case, or state the deliberate fall-through:

```yaml
default_covers:
  - not_found
```

## Conditions are checked and bounded

Operators must fit the field's known type, a condition that is constant for
every allowed value warns, and so does a `route` entry that can never be
selected. `matches` patterns are compiled offline and rejected when their
backtracking is unbounded; values longer than 1024 characters never match.

```yaml
# demo/workflow.yaml: fails with condition_type_mismatch
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup/result/status
            gt: 3
          outcome: completed
        - outcome: needs_review
```

```yaml
# demo/workflow.yaml: warns with condition_always_false
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup/result/status
            equals: fonud
          outcome: completed
        - outcome: needs_review
    on_unresolved:
      outcome: needs_review
```

```yaml
# demo/workflow.yaml: warns with route_unreachable_entry
start: lookup
flows:
  lookup:
    input:
      identifier:
        pointer: /payload/identifier
    transition:
      route:
        - when:
            binding:
              pointer: /flows/lookup/result/status
            equals: fonud
          outcome: completed
        - outcome: needs_review
    on_unresolved:
      outcome: needs_review
```

```yaml
# demo/workflow.yaml: fails with unsafe_pattern
flows:
  first:
    input: {}
    transition:
      route:
        - when:
            binding:
              pointer: /payload/reference
            matches: (a+)+b
          outcome: completed
        - outcome: needs_review
```

Fix: compare with a value the field can hold, reorder or remove the entry, or
use a pattern without nested unbounded quantifiers (`a++b`, `[a-z]+(?:-[a-z]+){0,3}`).

## Handlers are declared, registered and matching

A handler step calls a handler declared under `handlers` in settings.yaml.
The host's registration must match the declared schemas and effect
(`handler_contract_mismatch` at `prepare_application`), and every declared
handler needs a registration before `open_application` runs anything
(`missing_handler_registration`). The CLI compiles with declarations alone.

```yaml
# demo/workflow.yaml: fails with unknown_handler
flows:
  first:
    input: {}
    definition:
      steps:
        - id: act
          definition:
            type: handler
            handler: notify
            input: {}
    transition:
      outcome: completed
```

## Prompts use their declared inputs

A `prompt` template may insert only declared inputs; an input the prompt never
references is never sent and warns.

```yaml
# demo/workflow.yaml: fails with invalid_prompt
defaults:
  model: local
flows:
  answer:
    input:
      message:
        pointer: /payload/message
    definition:
      steps:
        - id: reply
          definition:
            type: llm
            instructions: Answer the message.
            input:
              message:
                pointer: /payload/message
            prompt: "Message: {{ body }}"
            output: text
    transition:
      outcome: completed
```

```yaml
# demo/workflow.yaml: warns with unused_llm_input
defaults:
  model: local
flows:
  answer:
    input:
      message:
        pointer: /payload/message
      language:
        pointer: /payload/language
    definition:
      steps:
        - id: reply
          definition:
            type: llm
            instructions: Answer the message.
            input:
              message:
                pointer: /payload/message
              language:
                pointer: /payload/language
            prompt: "Message: {{ message }}"
            output: text
    transition:
      outcome: completed
    on_unresolved:
      outcome: needs_review
```

## What is not guaranteed

These checks are structural. They do not prove that categories, cases, review
policy or prompts are right for the business; exercise those with reviewed
[evaluation cases](../evaluation/index.md). Some absence depends on data and
is checked at run time instead: a payload field that the input schema does
not `require`, a `first_of` whose members are all `null`, values in open or
complex schemas, and model or tool output that fails validation. A run still
never loops, never exceeds `execution.max_steps` or its deadline, and reports
such a value as `missing_binding` or `invalid_input` rather than guessing.

To document the checked graph, render every workflow with
`foliqant explain --format mermaid --all --output docs/workflows.md` and keep
the file current in CI with `--check` ([CLI reference](../reference/runtime-configuration.md#explain-and-generated-documentation)).

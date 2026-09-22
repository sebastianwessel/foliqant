# Process multiple requests

First decide what “multiple” means in your application. A `multiselect`
question returns several labels for one subject. A `request_units` question
identifies separate requested actions, each with an ID, status, category, and
optional subject. Two labels are not automatically two jobs.

For example, “Freeze card-123 and send the statement for account-9” can have
both labels in `multiselect`; `request_units` can additionally represent the
two distinct actions. A single-choice classification may correctly abstain
with `multiple_valid_options`. The [decision evidence example](https://github.com/sebastianwessel/foliqant/blob/main/examples/decision_evidence/README.md)
demonstrates those question types and their different outputs.

## Plan before processing

A request-unit assessment is information, not authorization to execute each
unit. Units can be `active`, `withdrawn`, `conditional`, or `quoted`, and the
answer may contain explicit relations. The host's trusted policy should check
the assessment status, supported categories, subjects, duplicates, relations,
and any business prerequisites before creating a work plan.

The [multi-request example](https://github.com/sebastianwessel/foliqant/blob/main/examples/multi_request_processing/README.md)
uses `assess → plan → process → finalize`. Its trusted `plan_requests` handler
creates ordered `{id, flow, input}` items only for allowed independent work;
held and ignored units remain visible for final disposition. The process step
uses a `flow_collection` with an explicit callable-flow allowlist and item cap.

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - lookup_status
  - prepare_guidance
max_items: 8
```

Every item is validated before child I/O. Callable flows run sequentially
under the parent's admission slot, deadline, and budgets. A child review is
recorded and later independent items continue. A technical child failure stops
new work and preserves the ordered ledger in `partial_result.items`; later
items are skipped. After a successful or review collection, read
`result.items` and let trusted code determine the final business disposition.

Run the complete example offline with its scripted model and local tool:

```sh
uv sync --locked --extra mcp --extra openai
uv run --no-sync python -m examples.multi_request_processing.run
```

The default run does not call a model endpoint. See [Configure a flow
collection](../steps/flow-collection.md) for the full item and ledger contract
and [score request-unit results](../evaluation/task-types.md) before using the
policy with real inputs.

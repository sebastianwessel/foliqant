# 6. Process several requests conservatively

This capstone turns one message into typed request units, applies trusted
application policy, invokes a bounded sequence of callable flows, and decides a
final disposition from the complete ledger.

```text
assess -> plan -> process -> finalize
                    |
                    +-> lookup_status      callable model and MCP flow
                    +-> prepare_guidance   callable trusted-handler flow
```

`assess` preserves the full model assessment. `plan_requests` accepts only
active units from a fully answerable independent assessment, merges exact duplicates, ignores
withdrawals and quoted requests, and holds conditional, related, unsupported,
or incomplete work for review. Collection IDs use `task_1`, `task_2`, and so on;
`request_ids` maps each task back to the original model-authored request-unit ID
without changing the assessment.

The collection step allows only the two callable flows and caps the number of
items:

```yaml
type: flow_collection
items:
  pointer: /payload/items
flows:
  - lookup_status
  - prepare_guidance
max_items: 8
```

Items run sequentially under the root deadline and budgets. The disposition
handler receives the plan and collection ledger, so held and ignored requests
stay visible alongside prepared work. The example produces `ready`,
`partial_review`, `review`, or `no_action`; it performs no writes.

Run the public scenarios with a scripted model, actual local MCP server, and
trusted handlers:

```sh
uv run --no-sync python -m examples.multi_request_processing.run
uv run --no-sync python -m examples.multi_request_processing.evaluate
```

The committed English and German gold covers the pipeline, routed and callable
flows, collection step, and nested operations. It tests synthetic wiring and
policy behavior, not live model quality.

To use the explicitly configured OpenAI-compatible endpoint, set `MODEL_ID` and
`MODEL_BASE_URL`, then opt in:

```sh
uv run --no-sync python -m examples.multi_request_processing.run --live
uv run --no-sync python -m examples.multi_request_processing.evaluate --live
```

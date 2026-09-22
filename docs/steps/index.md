# Choose a step for the job

A flow runs its listed steps in order. Start with the output your support-email workflow needs, then choose a step. You can combine steps: classify an email, extract its reference, and look up a record.

| What you need | Guide | Result value |
| --- | --- | --- |
| Is a claim true, false, or unknown? | [Yes/no decision](yes-no.md) | `answer.value` |
| One support queue | [Classification](classification.md) | `answer.optionId` |
| All applicable support tags | [Labeling](labeling.md) | `answer.optionIds` |
| An ordered urgency level | [Ranking](ranking.md) | `answer.levelId` |
| Distinct requests in one email | [Request extraction](request-extraction.md) | `answer.units` and `relations` |
| An application-shaped JSON object | [JSON response and extraction](llm.md) | Schema-validated JSON |
| A draft or summary | [Text response](text.md) | String |
| Model-selected read-only lookups | [Agent loop](agent-loops.md) | Final JSON or text |
| A fixed read-only lookup | [Direct MCP call](mcp.md) | Tool JSON or text |
| Trusted application code | [Handler](handler.md) | Schema-validated result |
| An explicit list of requests | [Flow collection](flow-collection.md) | Ordered child-flow ledger |

Typed decisions share [answerability, reason, evidence strength, and question configuration](decision.md). They return an assessment, not just a category. Choose a decision when the business answer fits one of its fixed shapes; use an LLM step for an application-specific JSON schema or prose.

## Put the definition beside its flow

```text
config/
  support_triage/
    triage/
      flow.yaml
      classify.step.md
      extract.step.yaml
```

```yaml
# config/support_triage/triage/flow.yaml
steps:
  - classify
  - extract
```

The list determines execution order. An ID resolves exactly one of `<id>.step.yaml`, `<id>.step.md`, `<id>/step.yaml`, or `<id>/step.md` beside `flow.yaml`. Unlisted files do not run. `decision` and `llm` can use Markdown: YAML front matter holds configuration and its nonempty body supplies `instructions`. Do not set `instructions` in both places. Explicit file and inline definitions are also supported; see [flows](../configuration/flows.md).

## Bind data and read the record

```yaml
message:
  pointer: /payload/message
language:
  literal: en
reference:
  pointer: /steps/extract/result/reference
```

`/payload` is this flow's bound input; `/steps/<id>` is an earlier local step record. A missing required pointer fails with `missing_binding`; JSON `null` counts as present. An optional pointer needs an explicit `default`. See [context and bindings](../configuration/context.md).

The public result keeps each step at `/flows/<flow-id>/steps/<step-id>`. A completed step has `result`; a decision with unresolved business evidence has `needs_review`; a technical error has `failed` and a safe `error`. Later unrun steps are `skipped`. The containing flow owns transitions and `on_unresolved`; see [flows](../configuration/flows.md).

Validate a complete configuration offline with `foliqant validate --config config/settings.yaml`. This checks structure and references, not provider behavior. A custom handler must be registered through `prepare_application(..., handlers=HANDLERS)` for validation. Test business behavior with [evaluations](../evaluation/index.md).

# Conditional intake

This example routes an account request entirely through configuration: which
flow starts, which steps run, where each flow continues, and when a lookup is
retried. Models and handlers return data; they never choose a route. All
accounts and messages are synthetic.

```text
config/
  settings.yaml                 # model, MCP catalog and declared handler contracts
  contracts/                    # handler input/output schemas
  account_intake/
    workflow.yaml               # routed start, cases, route, repeat, review default
    classify/                   # decision: billing or cancellation
    extract/                    # step `when`: extract, check, repair, recheck
    lookup/                     # MCP lookup, repeated with a retry flow
    correct/                    # callable retry flow: propose a corrected reference
    manual_review/              # declared handler summarizes the review reason
```

What each feature does here:

| Feature | Where | Effect |
| --- | --- | --- |
| Routed `start` | `workflow.yaml` | A form that names the request type skips classification |
| `cases` with coverage | `classify` transition | Both catalog categories are covered; the compiler would flag a typo |
| Step `when` | `extract/flow.yaml` | Extraction runs only without a form reference; repair and recheck only for an invalid one |
| `first_of` | `extract/check.step.yaml`, flow and workflow outputs | Use the form reference, else the extracted one; report the recheck, else the check |
| Object output | `extract/flow.yaml`, `workflow.yaml` | Project `{status, account_reference, repaired}` and the host payload without handlers |
| `route` | `extract` and `lookup` transitions | Continue only with a valid reference and a known account |
| `repeat` with a retry flow | `lookup` | An unknown account runs `correct`, then looks the corrected reference up once more |
| `defaults.on_unresolved` | `workflow.yaml` | Every review goes to `manual_review` |
| Declared handlers | `settings.yaml` | `validate` and `explain` work without Python registrations |

Run the scripted path and the synthetic gold:

```sh
uv run --no-sync python -m examples.conditional_intake.run
uv run --no-sync python -m examples.conditional_intake.evaluate
uv run --no-sync foliqant explain --config examples/conditional_intake/config/settings.yaml --format mermaid
```

The demo message names an unknown account and a previous one. The first lookup
returns `plan: Unknown`, the scripted correction proposes `A-100`, and the
second attempt finds it: `flows.lookup.attempt_count` is `2` and
`flows.lookup.repeat.stopped_by` is `until`. The default commands use a
`FunctionModel` and the real local stdio MCP server of the support tutorial;
they make no model-endpoint request. The gold checks five pipeline cases and
two isolated `extract` flow cases. It proves wiring, not model quality.

To use the configured OpenAI-compatible endpoint, set `MODEL_ID` and
`MODEL_BASE_URL`, then add `--live` to either command.

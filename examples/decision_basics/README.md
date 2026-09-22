# 1. Classify one request

This first learning example maps one support message to `billing` or
`cancellation`. It has one workflow, one flow, and one decision step. Unsupported
or ambiguous evidence goes to `needs_review`; the decision never routes directly.

Start with the file tree:

```text
config/
  settings.yaml
  decision_basics/
    input.schema.json
    workflow.yaml
    classify/
      flow.yaml
      classify.step.md
evaluation/
  dataset.json
  cases/requests.json
```

Read the files in that order. `settings.yaml` names the provider.
`workflow.yaml` binds only the message into the flow and owns the terminal
outcome. `flow.yaml` orders the step and projects its selected category.
`classify.step.md` supplies two described categories and criteria that separate
them.

Run the local scripted model; it opens no endpoint:

```sh
uv run --no-sync python -m examples.decision_basics.run
uv run --no-sync python -m examples.decision_basics.evaluate
```

The tracked gold contains independently authored English and German billing and
cancellation messages. The same four sources are measured at pipeline, flow, and
step scope; they remain four business cases, not twelve independent examples.

To use the explicitly configured OpenAI-compatible endpoint, set `MODEL_ID` and
`MODEL_BASE_URL`, then opt in:

```sh
uv run --no-sync python -m examples.decision_basics.run --live
uv run --no-sync python -m examples.decision_basics.evaluate --live
```

Continue with [structured extraction](../support_triage/README.md) after the
single decision and review route are clear.

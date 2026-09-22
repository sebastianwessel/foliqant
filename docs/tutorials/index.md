# Build workflows in six stages

This learning path adds one capability at a time. Each stage starts from a
business boundary, shows the files that encode it, runs offline by default, and
adds reviewed English and German gold. Work through the stages in order if you
are new to the runtime.

| Stage | Capability | Example |
| --- | --- | --- |
| 1 | One evidence-backed decision and a review outcome | [`decision_basics`](https://github.com/sebastianwessel/foliqant/tree/main/examples/decision_basics) |
| 2 | Structured extraction from selected context | [`support_triage`](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_triage) |
| 3 | Exact deterministic routing between flows | [`routed_intake`](https://github.com/sebastianwessel/foliqant/tree/main/examples/routed_intake) |
| 4 | One declared read-only MCP call | [`public_request_mcp`](https://github.com/sebastianwessel/foliqant/tree/main/examples/public_request_mcp) |
| 5 | A model-selected read-only tool loop | [`model_tool_loop`](https://github.com/sebastianwessel/foliqant/tree/main/examples/model_tool_loop) |
| 6 | Several requests planned, collected, and conservatively disposed | [`multi_request_processing`](https://github.com/sebastianwessel/foliqant/tree/main/examples/multi_request_processing) |

Every example uses `config/settings.yaml`, conventional workflow/flow/step
paths, and `evaluation/dataset.json`. Model-backed commands use a local
`FunctionModel` unless `--live` is explicit. Offline wiring checks establish
contracts and control flow; they do not establish model quality.

The examples directory also contains focused deep dives: decision question
forms and evidence, selected context passed to MCP, prompt-security cases, and a
thin HTTP host. Use those after the capability path rather than as prerequisites.

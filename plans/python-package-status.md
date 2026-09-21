# Python package implementation status

Canonical contract: [Python package](../specs/11-python-package.md).

The root project builds one `foliqant` wheel with runtime, native decision
contracts and evaluation. `model/` is a separate training/curation project;
`examples/` owns runnable workflow bundles and the small HTTP host. There is no
`service/` project or root workflow deployment configuration.

Implemented functionality:

- Strict typed configuration, confined workflow compilation and deterministic
  in-memory execution with optional caller/trace context.
- Native decisions, structured/text model operations, trusted Python handlers,
  read-only MCP tools/OAuth and optional safe OpenTelemetry.
- `run_step` for isolated execution with explicit resolved inputs, retaining the
  same limits, validators, clients and measured usage as full execution.
- Immutable golden suites, exact/set/custom checks, sequential-by-default bounded
  evaluation and prompt-variant comparison. Reports expose honest coverage,
  failures, review outcomes and measured step/pipeline latency and usage.
- Optional `evaluation.dataset` configuration, offline dataset checks/replay,
  explicit classification/multilabel metrics and private full-result artifacts.
  Runtime startup neither reads gold nor requires it to be deployed.
- Optional single-choice fallback categories retain original answers and record
  model/fallback selection separately; unresolved issue maps select deterministic
  routes. Evaluations score original answers, effective selections, and origins.
- Per-step profile/model/options selection reuses offline provider validation
  and shared admission. Explicit prior-result bindings drive extraction-to-MCP.
- Public package docs, local Qwen business examples and separate model-development
  guides and skills. Production dependencies exclude model-training and HTTP-host
  dependencies.

Persistence, queues, background jobs, app authentication and inbound transports
are not package features or pending implementation work. Evaluation performs no
hidden judge calls, automatic prompt changes or dataset/model downloads.

Initial package verification is recorded in
[the package/evaluation review](reviews/python-package-evaluation.md); configuration-based
evaluation evidence is in [the evaluation DX record](reviews/evaluation-dx-proposal-2026-09-21.md).
Small synthetic model runs verify integration only, not production accuracy.

Current fallback, context, and step-model verification is recorded in
[the implementation review](reviews/fallback-context-models-2026-09-21.md).

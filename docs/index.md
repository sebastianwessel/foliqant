# Foliqant documentation

Foliqant compiles and runs typed workflows in a Python process. A workflow
connects explicit flows; each flow executes an ordered sequence of decision,
LLM, MCP, or trusted handler steps. The host awaits one call and receives one
validated `ExecutionResult`.

## Start here

1. [Install and run](getting-started/runtime.md) to create, validate, inspect,
   and execute a minimal project.
2. Read [runtime concepts](concepts/runtime.md) to choose workflow, flow, and
   step boundaries.
3. Use [workflow authoring](guides/build-workflows.md) for file conventions,
   bindings, prompts, schemas, routes, and result paths.
4. Use [runtime configuration](reference/runtime-configuration.md) for model
   providers, MCP, handlers, limits, environment values, telemetry, and CLI
   behavior.

## Build and verify

- [Decision contracts](guides/decision-contracts.md) explains typed questions,
  answerability, evidence, fallback selection, and application policy.
- [Testing and evaluation](guides/testing-and-evaluation.md) covers local fakes,
  reviewed JSON gold, pipeline/flow/step scopes, offline checks, replay, and live
  evaluation.
- [Evaluation results](guides/evaluation-results.md) explains denominators,
  usage, confusion matrices, comparisons, and operational failures.

Runnable examples include
[support triage](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_triage/README.md),
[a read-only MCP lookup](https://github.com/sebastianwessel/foliqant/blob/main/examples/public_request_mcp/README.md),
[extraction passed to MCP](https://github.com/sebastianwessel/foliqant/blob/main/examples/extracted_request_mcp/README.md),
[prompt-security cases](https://github.com/sebastianwessel/foliqant/blob/main/examples/security_evaluation/README.md),
and [a thin HTTP host](https://github.com/sebastianwessel/foliqant/blob/main/examples/http_workflow/README.md).

Foliqant does not provide inbound HTTP, application authentication, persistent
jobs, or result storage. The embedding service owns those concerns. The
[runtime skill](skills/foliqant.md) gives coding agents a self-contained guide
to the same public contract.

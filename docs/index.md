# Foliqant documentation

Foliqant compiles and runs typed workflows in a Python process. A workflow
connects explicit flows; each flow executes an ordered sequence of decision,
LLM, MCP, trusted handler, or bounded flow-collection steps. The host awaits one call and receives one
validated `ExecutionResult`.

## Start here

1. [Install and run](getting-started/runtime.md) to create, validate, inspect,
   and execute a minimal project.
2. Follow the [six-stage tutorial](tutorials/index.md) to add decisions,
   extraction, routing, MCP, tool loops, and bounded multi-request processing.
3. Read [runtime concepts](concepts/runtime.md) to choose workflow, flow, and
   step boundaries.
4. Use [workflow authoring](guides/build-workflows.md) for file conventions,
   bindings, prompts, schemas, routes, and result paths.
5. Use [runtime configuration](reference/runtime-configuration.md) for model
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

The tutorial links each runnable learning example. Additional examples cover
decision evidence, selected extraction passed to MCP, prompt-security cases,
and a thin HTTP host.

Foliqant does not provide inbound HTTP, application authentication, persistent
jobs, or result storage. The embedding service owns those concerns. The
[runtime skill](skills/foliqant.md) gives coding agents a self-contained guide
to the same public contract.

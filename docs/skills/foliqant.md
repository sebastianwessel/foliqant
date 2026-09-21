# Foliqant runtime skill

The repository skill at
[`skills/foliqant`](https://github.com/sebastianwessel/foliqant/blob/main/skills/foliqant/SKILL.md) helps an agent build and
review applications that use the installable `foliqant` package.

Use it for versioned workflow bundles, in-memory execution, model and MCP
adapters, native decision contracts, schema validation, telemetry and runnable
examples. It keeps application-specific HTTP, authentication, persistence and
queue handling outside the package core.

The skill treats the current package implementation and CLI help as the command
authority. It preserves the boundary between deterministic workflow policy and
untrusted model observations, and requires explicit expected results for
evaluation cases. Give the agent your workflow and independently reviewed gold;
it can add the optional dataset reference, write strict JSON cases and explicit
label catalogs, and run offline validation. It does not invent expected business
outcomes, score thresholds, or implicit judge calls. See the evaluation guide for
the difference between offline checks, saved-result replay, and model execution.

Start with [runtime setup](../getting-started/runtime.md), then continue with
[workflow authoring](../guides/build-workflows.md) and
[testing and evaluation](../guides/testing-and-evaluation.md).

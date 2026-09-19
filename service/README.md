# Workflow service

Proposed stack: strict TypeScript on Node.js LTS. No executable service or dependencies are present yet; see the [architecture](../docs/architecture.md) before choosing the implementation stack.

`src/core/` owns graph execution, result validation, and deterministic routing. `src/ports/` owns extension interfaces. `src/adapters/` owns model endpoint, transport, and persistence implementations. Adapter imports must not flow back into core. Application startup will assemble an explicit registry of trusted adapters.

The first slice is one validated workflow, a fake model adapter, a real endpoint adapter, and synchronous local HTTP. Redis Streams requires the durable execution and delivery contract first. Keep dependency locks and runtime images separate from model tooling.

## Public-surface inventory

There are no implemented public endpoints, adapter interfaces, step handlers, CLI commands, or durable manifests yet. The workflow and service YAML files are proposed examples. Replace this inventory with concrete signatures and execution semantics as each feature is implemented.

# Foliqant

Financial understanding. Decisions supported by evidence.

Foliqant combines a reusable financial decision model with a configurable service for interpreting correspondence and documents. The model produces structured answers and evidence; deterministic workflow rules decide how those answers are used.

The target scope includes banking and fund operations, insurance and public-sector
processes. Multiple confirmed requests can lead to several configured tasks
inside one process. This expanded [decision and process concept](specs/10-business-decisions-and-processes.md)
is specified, not yet implemented in the workflow service.

**Available now:** local setup, pinned public-source curation, data preparation,
LoRA/QLoRA training, customer customization, evaluation, threshold selection,
independent audit, and model export. The Python workflow package runs configured pipelines in memory and returns
results; authentication, persistence, queues and inbound transports are outside
its scope.

```sh
./scripts/setup-model
./scripts/generate-data --pilot
```

Use `./scripts/generate-data` for the full bounded native decision-data recipe,
or add `--prepare-only` to prepare sources and tasks without inference. See
[native decision data](docs/guides/native-decision-data.md) for setup, resume,
visible progress, safe Ctrl+C pauses, coverage gates, and machine-readable
answerability results. The authored recipe includes English and German; German
examples retain German response prose with unchanged English enum values.
Generation publishes only accepted training lineage and does not start training.
Bounded pilot checks demonstrate pipeline behavior, not population accuracy,
full-recipe coverage, training readiness or financial production qualification.

The native recipes use standalone Splash with low reasoning and temperature
`0.1`; configure the exact endpoint in root `.env`. Other local OpenAI-compatible
servers can be selected explicitly. Runtime and model combinations must return
final structured content matching the requested JSON Schema.

## Start here

- [Model lifecycle specifications](specs/README.md)
- [User guides](docs/README.md)
- [Automated public-source curation](docs/guides/automated-curation.md)
- [Generate native decision data](docs/guides/native-decision-data.md)

- [Workflow service setup and CLI](service/README.md)
- [Python workflow service specification](specs/11-workflow-service.md)
- [Small HTTP wrapper example](examples/http-workflow/README.md)
- [Embedded workflow example](examples/embedded-workflow/README.md)
- [Model-enabled inbox example](examples/inbox/README.md)
- [Local MCP workflow example](examples/mcp-tools/README.md)
- [Workflow service agent skill](skills/foliqant-service/SKILL.md)
- [Apple Silicon training and model lineage](specs/research/apple-silicon.md)
- [Model research, datasets, calibration, and hosting](specs/research/model-research.md)
- [Contributor instructions](AGENTS.md)

## Repository layout

```text
model/
  base/             Shared Foliqant model, adapted from an upstream checkpoint
  customization/    Customer/domain adaptations of a released Foliqant model
  evaluation/       Baselines, held-out evaluation, calibration, regression
  export/           Merge, quantize, package, and verify serving artifacts
inference/          Standard model-server deployment profiles
service/
  src/foliqant/core/ Transport-independent workflow execution
  src/foliqant/ports/ Typed extension contracts
  src/foliqant/adapters/ Model/MCP clients, validation and telemetry
contracts/          Language-neutral data schemas
workflows/          Versioned processes, prompts, and workflow examples
config/             Deployment-specific bindings and secret references
docs/               End-user guides
specs/              Implementation contracts and research
```

The in-memory workflow package calls a separately hosted model endpoint. Training libraries and GPU dependencies do not belong in the service image. Model weights, real customer data, credentials, and generated artifacts do not belong in Git.

## Agreed constraints

- Local model development first: M1-family and M5-family Macs with 64 GB are available; retain the earlier 24 GB GPU inference target as a separate deployment profile.
- Use standard vLLM, LM Studio, Ollama, or comparable supported runtimes.
- Adapt existing open weights rather than pretraining from scratch.
- English first, German second, preserving a multilingual foundation.
- Cover correspondence, funds, disclosures, regulations, contracts, and reports.
- Retrieve versioned rules and product facts; do not trust model memory for current obligations.
- Validate evidence and abstention, and measure calibration independently.
- Keep model customization separate from workflow configuration. Most new prompts, catalogs, questions, and routes should not require fine-tuning.
- Keep this repository independent of PURISTA, Harness, and Voyage.

## Current implementation scope

The Python tools cover automated public-source curation, preparation, shared
adaptation, customer customization, evaluation and export for standard inference.
Curation preparation downloads and converts pinned assets; optional augmentation
uses a loopback endpoint by default or an explicitly allowed trusted private-network
endpoint; training remains a later explicit command.
The small setup model exercises the lifecycle locally. Selecting and qualifying
a production financial model is separate from verifying the tooling. The
workflow package provides an in-memory pipeline; see its guide for the supported
model/MCP steps and caller-owned transport boundary.

Foliqant is a working name derived from folio and quant; no trademark or domain availability is claimed. A distribution license has not yet been selected.

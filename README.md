# Foliqant

Financial understanding. Decisions supported by evidence.

Foliqant combines a reusable financial decision model with a configurable service for interpreting correspondence and documents. The model produces structured answers and evidence; deterministic workflow rules decide how those answers are used.

**Status:** repository foundation and architecture proposal. No training pipeline, workflow runner, HTTP adapter, or Redis adapter is implemented yet. YAML examples illustrate the proposed configuration; they are not executable today. The recommended language split is Python for model work and TypeScript on Node.js for the service, pending final selection.

## Start here

- [Architecture and language recommendation](docs/architecture.md)
- [Apple Silicon training and model lineage](docs/local-training-and-model-lineage.md)
- [Model research, datasets, calibration, and hosting](docs/research-and-concept.md)
- [Illustrative workflow](workflows/financial-triage/workflow.yaml)
- [Illustrative service configuration](config/service.example.yaml)
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
  src/core/         Transport-independent workflow execution
  src/ports/        Typed extension contracts
  src/adapters/     Model endpoints, inputs, outputs, persistence
contracts/          Language-neutral data schemas
workflows/          Versioned processes, prompts, and workflow examples
config/             Deployment-specific bindings and secret references
docs/               Architecture, research, and historical context
```

The workflow service and model server are separately deployed processes. Training libraries and GPU dependencies do not belong in the service image. Model weights, real customer data, credentials, and generated artifacts do not belong in Git.

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

## First implementation slice

Validate one workflow bundle, call a configurable local model endpoint through a model adapter, validate its structured result, apply a deterministic route, and return the same result through an HTTP adapter. Test the core using a fake model adapter before using real weights. Add durable execution and a Redis Streams adapter before claiming reliable asynchronous processing.

No model or dependency downloads, paid compute, or production deployment have been performed. The research originated in the PURISTA workspace and was moved into this standalone project on 2026-09-19. Historical integration research is retained in [docs/background](docs/background/purista-integration-research.md); it does not override this project's architecture.

Foliqant is a working name derived from folio and quant; no trademark or domain availability is claimed. A distribution license has not yet been selected.

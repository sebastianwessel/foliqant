# Foliqant

Financial understanding. Decisions supported by evidence.

Foliqant combines a reusable financial decision model with a configurable service for interpreting correspondence and documents. The model produces structured answers and evidence; deterministic workflow rules decide how those answers are used.

**Available now:** local setup, pinned public-source curation, data preparation,
LoRA/QLoRA training, customer customization, evaluation, threshold selection,
independent audit, and model export. The configurable workflow service remains
a separate proposal.

```sh
./scripts/setup-model
./scripts/curate-data --prepare-only
```

The curation command can continue with a local LM Studio model after source
preparation. Runtime and model combinations must return final structured content
matching the requested JSON Schema.

## Start here

- [Model lifecycle specifications](specs/README.md)
- [User guides](docs/README.md)
- [Automated public-source curation](docs/guides/automated-curation.md)

- [Architecture and language recommendation](specs/research/workflow-service-proposal.md)
- [Apple Silicon training and model lineage](specs/research/apple-silicon.md)
- [Model research, datasets, calibration, and hosting](specs/research/model-research.md)
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
docs/               End-user guides
specs/              Implementation contracts and research
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

## Current implementation scope

The Python tools cover automated public-source curation, preparation, shared
adaptation, customer customization, evaluation and export for standard inference.
Curation preparation downloads and converts pinned assets; optional augmentation
uses a loopback endpoint by default or an explicitly allowed trusted private-network
endpoint; training remains a later explicit command.
The small setup model exercises the lifecycle locally. Selecting and qualifying
a production financial model is separate from verifying the tooling. The
configurable workflow service remains a separate proposal.

Foliqant is a working name derived from folio and quant; no trademark or domain availability is claimed. A distribution license has not yet been selected.

# Scope and authority

## Requested outcome

Implement the complete local model-building and fine-tuning toolchain: rights-declared data preparation, reproducible shared model adaptation, independent customer customization, real evaluation, calibration-policy assessment, merge/export, and artifact verification. Include easy-to-advanced user documentation, reusable agent skills, independent review, cleanup, and evidence of a real end-to-end run.

The user explicitly requested implementation, delegated agent/model selection, and required specs first. This authoring step chooses concrete, reversible implementation behavior within that mandate. It does not authorize paid services, uploading customer data, public model releases, accepting third-party legal terms, or declaring regulatory suitability. Those remain human decisions. Review may not invent additional product scope.

## Boundaries

- Python model tooling is a standalone installable package and CLI named `foliqant-model`, rooted at `model/src/foliqant_model/` with root `pyproject.toml` and `uv.lock`.
- MLX LM is the initial real local training backend for Apple Silicon. The user has M1-family and M5-family Macs with 64 GB. Training memory/throughput are measured, not promised from parameter counts.
- A shared Foliqant model is an adaptation of pinned upstream instruction-capable weights, not pretraining from random initialization. Customer adapters start from an exact merged shared release.
- Production functions never substitute mocks, synthetic weights, copied expected answers, or fabricated evaluation metrics for real training/generation. Small generated diagnostic samples and small real models are valid integration inputs, but datasets and model weights are never stored in Git. Generate or download them into the local setup workspace. Only recipe/configuration/preparation code belongs in the repository.
- Standard local inference is an acceptance boundary: export a normal checkpoint and prove loading/generation outside MLX LM. GGUF is a separate supported-architecture export, never a fake file or automatic fallback.
- The HTTP/Redis workflow service, UI, foundation pretraining, distributed training, cloud provisioning, and production financial-model quality certification are not this implementation scope. Their earlier sketches are retained as research. Nothing may claim those capabilities are implemented.

## Completion definition

All public CLI paths have typed validated inputs, meaningful success/failure tests, documentation and skill coverage. A real tiny shared adaptation, merge, independent customer adaptation, evaluation, export and independent-runtime load must run. Negative evidence must include data leakage, parent mismatch, artifact corruption, invalid output, cancellation, and policy/profile mismatch. A lifecycle smoke pass proves the toolchain, not financial accuracy or readiness for automated financial/legal action.

No checkpoint is selected as the production Foliqant winner by these specs. The smoke checkpoint is a compatibility fixture. Users select explicit pinned upstream weights for substantive experiments.

## Business-process target extension

The owner subsequently requested recording broader banking/funds, insurance and
public-sector use cases, including one process branching into several tasks.
[Specification 10](10-business-decisions-and-processes.md) owns that future
decision/evidence/process design. Its current mandate is specifications and
dataset research only; the lifecycle boundary above and active data-generation
runs remain unchanged. It is not permission to deploy a workflow service,
download new data, call additional models or assert production reliability.

## One-command setup

The user additionally requires one command to prepare all model-development prerequisites: the isolated environment, pinned dataset downloads, pinned model weights, and local prepared inputs/configuration. Source datasets and weights stay outside version control; the default data workspace is outside the checkout. Committed files are manifests, hashes/revisions, downloader/preparation code, configurations, documentation and minimal code-created unit-test inputs. Setup is separate from training and must not silently start training, upload data, accept gated access terms or incur paid compute.

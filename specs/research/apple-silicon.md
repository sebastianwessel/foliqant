# Local training and model lineage

Date: 2026-09-19. Status: researched proposal, not a hardware benchmark.

## Available hardware and training approach

The user has an Apple M1-family Mac with 64 GB unified memory and an M5-family Mac with 64 GB. These are local development candidates in addition to the earlier 24 GB GPU deployment budget; 64 GB unified memory is shared with macOS and applications, not 64 GB of dedicated available VRAM.

Use Python with MLX/MLX LM for an Apple Silicon adapter-training pilot. MLX LM supports low-rank adaptation and quantized-base adaptation. The current MLX installation documentation specifies Apple Silicon, native Python 3.10 or later, and macOS 14 or later; verify the selected MLX LM release's own dependencies and model requirements before installation. [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html), [MLX LM](https://github.com/ml-explore/mlx-lm)

A supported model around 8–12B with LoRA/QLoRA is a sensible first experiment on either 64 GB machine. This is an engineering estimate, not a measured memory guarantee. Larger 20–30B models may be candidates, but architecture, quantization, trainable modules, activations, optimizer, sequence length, and implementation support decide feasibility. Do not infer training support from an inference-only model listing or from MoE active-parameter count.

Start with batch size one, bounded sequences around 2–4k tokens, a modest adapter rank, and gradient accumulation/checkpointing as supported. Record actual peak memory and tokens per second before scaling the dataset or context. Neither full-parameter fine-tuning nor long-context training is promised on these machines. The exact chip variant, GPU configuration, memory bandwidth, and cooling matter; M5 alone is not enough to quote a speedup over the M1 machine. Benchmark both on the same short run rather than inventing timings.

MLX is a training backend here, not a required custom production inference system. Native Mac inference can use a compatible MLX runner, LM Studio, or Ollama. Validate standard vLLM deployment separately on a supported host; do not assume a CUDA/vLLM container runs on the Apple GPU. Keep training-backend details out of the workflow service.

Prove the export path early. An MLX Safetensors artifact is not automatically a Transformers-compatible checkpoint or a GGUF file just because its extension is familiar. MLX LM's documented built-in GGUF export covers a limited set of architectures; verify the exact candidate's merge/conversion path and test outputs in the target engine before investing in a large fine-tune. [MLX LM adaptation and export](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md)

## Two-stage adaptation, not training from scratch

```text
Upstream pretrained / instruction-capable model, pinned revision
  + shared financial task dataset
  -> Foliqant shared model v1 (our reusable starting checkpoint)
       + customer A dataset -> customer A adapter/model v1
       + customer B dataset -> customer B adapter/model v1
       + no extra training  -> configurable workflows using shared v1
```

Both stages are fine-tuning. The shared Foliqant model is a new derivative checkpoint/release, not a new architecture or a foundation model pretrained from zero. It retains the upstream model's applicable license and attribution obligations.

Recommended initial artifact strategy:

1. Train a shared LoRA/QLoRA adaptation against a pinned upstream instruction-capable model, if it outperforms the untuned baseline.
2. Preserve the shared adapter and exact parent metadata. Produce a validated merged Foliqant checkpoint where the selected architecture/exporter supports it; keep a higher-precision canonical artifact where feasible. Dequantizing a quantized source does not recover precision already lost.
3. Freeze that shared release. Train a fresh customer adapter against the Foliqant checkpoint, not against another customer's adapter. This starts from the shared learned weights, not random weights or the original unadapted checkpoint.
4. Serve the exact Foliqant parent plus its customer adapter only if the runtime supports that combination, or export a merged customer model. Do not require arbitrary adapter stacking in ordinary inference tools.
5. Evaluate customer gains, shared-task/German regressions, evidence, and calibration. Use a permitted shared replay mix or other retention techniques where measurements show forgetting.

Customer training does not require replaying all upstream pretraining or all shared training data. It may still benefit from selected shared examples to preserve behavior. Continuing an existing adapter is possible in some tools, but retaining a frozen shared release and independent customer branches gives clearer isolation and reproducibility. A small adapter file is not an independent model; it requires its exact compatible parent. [PEFT checkpoint explanation](https://huggingface.co/docs/peft/developer_guides/checkpoint)

When shared v2 is released, do not silently attach a customer adapter trained against v1. Continue serving the supported v1 lineage until customer adaptation against v2 has been trained/revalidated. Rebuilding a customer branch is another adaptation run, not pretraining from scratch.

## Configuration before customization

New process routes, prompts, questions, catalogs, and current regulatory/product facts usually belong in workflow configuration or retrieval. Fine-tune only when a measured recurring behavior is not adequately solved there. Customer knowledge should not automatically become customer training data.

The first experiment should demonstrate the whole chain on a tiny authorized/synthetic dataset: train an adapter, merge/export, load in a standard local inference runtime, execute the same evaluation cases, and report memory, time, and quality changes. No model downloads or training have been performed yet.

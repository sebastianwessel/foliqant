# Backend and dependency contract

Research date: 2026-09-19. This file is normative for the initial implementation. Real installation, training and export remain acceptance gates; package resolution alone is not runtime qualification.

## Environment and packages

Use CPython 3.12 (requires-python `>=3.12,<3.13`) in a repository-local uv environment. Offline data/artifact/schema commands must work without GPU libraries. MLX operations require native arm64 macOS and a working Metal device; doctor reports unavailable capabilities rather than importing MLX at CLI startup. M1/M5 64 GB are user target machines, not measured capacity guarantees. No mutation of global Python packages.

| Dependency group | Exact direct pins |
|---|---|
| Base CLI | pydantic==2.13.5; PyYAML==6.0.3; huggingface-hub==1.32.0; safetensors==0.8.0; scipy==1.18.1; jsonschema==4.26.0; gguf==0.19.0 |
| Optional mlx extra (Darwin arm64 marker) | mlx-lm[train]==0.31.3; mlx==0.32.2; transformers==5.17.0 |
| Optional validation extra | transformers==5.17.0; torch==2.14.0 |
| Development | pytest==9.1.1; mypy==2.3.1; ruff==0.16.8; build==1.6.1; types-PyYAML==6.0.12.20260906 |
| Build backend | hatchling==1.32.3 |

All pins are from PyPI release metadata retrieved on the research date, not remembered versions. MLX LM v0.31.3 declares mlx>=0.31.2 and transformers>=5.0.0; mlx0.32.2 is the current stable candidate satisfying that minimum. Resolve all transitives into uv.lock and verify on the host; a concrete incompatibility requires updating this contract and recording evidence, never silent fallback. uv is the environment manager, not a runtime dependency. Do not install evaluation leaderboards, hosted telemetry clients, or provider SDKs unless required by this exact pinned dependency graph.

Primary metadata: https://pypi.org/pypi/{package}/json (replace package with the table's normalized name). Tagged dependency declaration: https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/setup.py. MLX system requirements: https://ml-explore.github.io/mlx/build/html/install.html.

## Offline worker boundary

Run MLX operations in a child interpreter launched as an argument array with shell=False, a new process group, stdin disabled, a private cwd/log, a deadline and a private typed JSON request/result file. This isolates crashes and makes timeouts enforceable. CLI/runtime paths are never shell fragments. Child requests are internal closed contracts catalogued with the other representations; results must be parsed and validated before publishing an artifact.

Set HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, HF_HUB_DISABLE_TELEMETRY=1 and disable experiment reporting. Remove known provider/HF token credentials from the worker environment. Offline environment flags prevent supported libraries' network access; they are not an OS network sandbox. Only fetch receives HF credentials/network intent. Validate snapshots have no Python/pickle/executable files, symlinks, auto_map or custom code loading configuration. Never monkey-patch a library to bypass security. Model JSON/chat templates are untrusted data; tokenizer loading always uses trust_remote_code=False and local_files_only=True. Use only registered MLX architecture implementations.

## Training

Do not call mlx_lm.lora.run: v0.31.3 hardcodes trust_remote_code=True and replaces the supplied callback. The worker instead calls mlx_lm.utils.load(local_path, tokenizer_config={trust_remote_code: False, local_files_only: True}), constructs local ChatDataset instances with mask_prompt=True, and calls mlx_lm.tuner.trainer.train with explicit TrainingArgs, a local TrainingCallback, and the completion-loss callable defined below. Use model.freeze(), mlx_lm.tuner.utils.linear_to_lora_layers, mlx.optimizers.AdamW and the pinned save helpers for the same LoRA/optimizer setup as train_model; do not duplicate a training loop. Seed NumPy and MLX with TrainConfig.seed. Do not call the dataset loader's remote fallback. Cache/process all training and validation rows for sequence-length and nonempty supervised-loss checks before training.

Fixed backend options: train=True, test=False, fine_tune_type=lora, optimizer=adamw, optimizer_config={adamw:{weight_decay:0.0}}, lr_schedule=None, report_to=None, project_name=None, mask_prompt=True, clear_cache_threshold=0, steps_per_report=1. Map all TrainConfig fields to their corresponding v0.31.3 names (steps->iters, gradientAccumulation->grad_accumulation_steps, maxSequenceLength->max_seq_length, numLayers->num_layers, validationEvery->steps_per_eval, validationBatches->val_batches, saveEvery->save_every, gradientCheckpointing->grad_checkpoint). Paths are verified local paths supplied by the parent. rank/scale/dropout populate lora_parameters; use the pinned architecture's default LoRA target selection and persist the resolved trainable tensor names. Unknown architectures or no trainable tensors fail.

Loss version completion-mask-v1: for input batch[:, :-1] and target batch[:, 1:], the one-based target positions t are included exactly when promptOffset <= t < sequenceLength. Average cross entropy over the included target tokens and reject a zero-token batch. The final assistant's terminating template/EOS tokens are part of its completion; artificial batch padding is excluded. MLX LM v0.31.3 default_loss uses <= sequenceLength, which includes the first padding token for padded rows, so supply this loss through the trainer's public loss parameter rather than using or monkey-patching that default. Use the same callable for validation via the trainer. A token-mask test with unequal row lengths must prove prompt/padding excluded and first/final completion targets included. Save the library-compatible adapter configuration explicitly for later load_adapters; it must include fine_tune_type and lora_parameters plus resolved layer count as the pinned loader expects.

Callbacks capture actual finite final training loss, validation observations and peak_memory (GB converted using 1e9 to integer bytes). Inspect final adapters.safetensors and adapter_config.json, reject empty/nonfinite tensors and tensor names not matching the resolved trainable parameters. Never infer success only from a zero exit code. Warm start loads matching adapter weights with a new optimizer, PRNG and iteration count; document this as additional training, not optimizer-state recovery.

Backend adapter_config.json contains the library-required snake_case shape; it is a catalogued boundary representation, not a second user config. Validate it against the request and restrict fields; keep private generated config paths out of stdout. It may refer to the private run workspace for provenance; no finalized manifest may rely on that path to locate its parent.

Sources: https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/lora.py; https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/tuner/datasets.py; https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/tuner/trainer.py; https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/tuner/callbacks.py.

## Generation and scoring

Use the same explicit safe load, then tokenizer.apply_chat_template(prompt_messages, tokenize=True, add_generation_prompt=True). Pass token IDs to mlx_lm.stream_generate with max_tokens from EvaluationConfig and mlx_lm.sample_utils.make_sampler(temp=0). No speculative model, tools, sampling filters, KV quantization or reused prompt cache. Seed before each example with the configured seed so input order does not change the defined generation profile.

Concatenate every response.text exactly once. Count and score actual non-EOS generated tokens, including the final response token when finish_reason=length, excluding EOS when finish_reason=stop. For each such token use float(response.logprobs[response.token]); average the finite values. Empty completion yields generatedTokens=0 and meanTokenLogprob=null with reason no-generated-tokens. A nonfinite token score is a backend failure. Do not use the backend's reported generation_tokens blindly because it includes a stopping EOS. This is mean likelihood of the generated tokens, not correctness probability. Record finish reason and reject incomplete length-limited outputs for policy eligibility even if a truncated text happens to parse.

Sources: https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/generate.py and https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/sample_utils.py.

## Quantization, merge and export

Quantization uses mlx_lm.convert.convert with quantize=True, q_bits=4|8, q_group_size=64, q_mode=affine, trust_remote_code=False, upload_repo=None, local input and a non-existing worker output path. No runtime fallback/model substitution.

Merge uses the pinned fuse operations: safe-load exact model+adapter; fuse each LoRA layer, dequantize when applicable; save ordinary tensor weights/config/tokenizer with library save helpers. Do not execute fuse.main through mutable argv or enable upload. Remove quantization configuration only when the actual weights have been dequantized. Dequantization cannot recover lost precision.

Checkpoint export copies a verified merged Safetensors/config/tokenizer bundle into a new artifact, preserving required tensor key names. No unsupported universal HF portability claim: the release smoke independently loads the exact export with Transformers AutoTokenizer/AutoModelForCausalLM, trust_remote_code=False, local_files_only=True, on CPU, then generates a bounded response. Each additional architecture needs its own target-engine evidence before compatibility is advertised.

GGUF export uses mlx_lm.gguf.convert_to_gguf over verified dense merged weights, config and tokenizer.json. v0.31.3 supports only model_type llama, mistral or mixtral and emits F16. Refuse unsupported models and residual quantized weights before conversion. Parse the GGUF header/tensor metadata with the standard gguf reader to check the output, and use an independently pinned llama.cpp-compatible runtime for actual loading/generation before advertising compatibility. The independent acceptance verifier is llama.cpp build 10180 at commit 11b068d06605288ce7917534b46d52b47823dc13. This is the installed local arm64 build, verified with llama-cli --version; it is an intentionally older test verifier, not a bundled product dependency or a recommendation that users downgrade. Capture the executable SHA-256 and version in acceptance evidence. Use explicit local model, --n-predict 16, --ctx-size 512, --temp 0, --seed 7, --no-conversation and --simple-io --single-turn, with stdin disabled and bounded timeout; never use automatic model-download convenience flags. Other versions require their own evidence, and no fabricated verified flag is allowed.

Sources: https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/convert.py; https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/fuse.py; https://raw.githubusercontent.com/ml-explore/mlx-lm/v0.31.3/mlx_lm/gguf.py; https://huggingface.co/docs/transformers/main_classes/model.

## Real acceptance fixture

Use HuggingFaceTB/SmolLM2-135M-Instruct, immutable commit 12fd25f77366fa6b3b4b768ec3050bf629380bac, Apache-2.0, model_type llama. Its approximately 135M parameters (~269 MB BF16 weights) make the complete lifecycle affordable to exercise locally; it is not the financial production model. Fetch only through the CLI's explicit pinned acquisition command. No large candidate model download is necessary for toolchain acceptance.

Use reviewed synthetic text chat fixtures with enough separate components for four nonempty partitions and a separate customer dataset with no ancestor overlap. Two LoRA steps, batch one, rank four, scale eight and four final layers are sufficient to exercise real updates; maxSequenceLength must fit the tokenized fixture and is not assumed from text length. Prove tensors changed; do not require invented financial quality scores. Exercise both dense and quantized parent paths, shared/customer lineage, evaluations, policy/audit, merge/checkpoint/GGUF exports and independent generation. Record actual command outputs and hashes, and any unsupported target explicitly.

Model source: https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct/tree/12fd25f77366fa6b3b4b768ec3050bf629380bac.

Dependency resolution evidence: uv 0.7.13 resolved all listed groups (101 packages) using CPython 3.12.11 on 2026-09-19. The candidate lock is retained in research/dependencies/uv.lock and its matching pyproject.toml. These establish resolution only; actual install/import and training checks remain implementation acceptance. The final root lock must match the selected package graph and be checked again after packaging metadata is added.

Independent GGUF verifier executable observed on this host: llama.cpp 10180 / commit 11b068d06605288ce7917534b46d52b47823dc13; executable SHA-256 c47c683d54c76cefde5d62bbdf352025f66fd7156ea88ffc0f6becc47084238c. Record this exact verifier separately from model hashes.

Snapshot acquisition supports root-level text-model Safetensors files, an optional standard shard index, config/generation config, tokenizer JSON/model/tiktoken/vocabulary/merges assets, a single explicit chat template, README and license/notice files. Model metadata JSON is bounded at 16 MiB. Fetch requires unquantized source weights; quantization is an explicit later operation. No fallback chat template is registered in version one. Reject remote auto_map loading, unsupported files, empty/duplicate tensors and incomplete shard indexes. Header inspection does not claim inference compatibility.

Quantization and merge must preserve the parent's architecture and exact chat-template digest. Compare model inference configuration after removing only quantization/quantization_config fields. Keep the original parent tokenizer files byte-for-byte in the finalized child, rather than publishing library-normalized tokenizer metadata; record the new weight/config identity independently.

Conversions also retain the original parent README, LICENSE/NOTICE files and generation configuration when present. Model/data license declarations are provenance rather than legal approval.

Automated curation adds direct `pyarrow==25.0.1` for the pinned typed-decisions Parquet assets. The local endpoint uses the standard-library HTTP client plus the existing pinned JSON Schema validator; it does not add a model-serving runtime.

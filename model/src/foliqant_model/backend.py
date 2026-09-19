"""Isolated MLX worker implementation.

MLX and MLX-LM are imported only inside operations so importing the package and
running data-only commands never probes Metal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import numpy as np
from pydantic import Field, ValidationError, model_validator

from .contracts import (
    DoctorWorkerRequest,
    DoctorWorkerResult,
    GenerateWorkerRequest,
    GenerateWorkerResult,
    GgufExportMetadata,
    GgufWorkerRequest,
    GgufWorkerResult,
    MergeWorkerRequest,
    MergeWorkerResult,
    QuantizeWorkerRequest,
    QuantizeWorkerResult,
    TrainWorkerRequest,
    TrainWorkerResult,
    ValidationObservation,
    WorkerRequest,
    WorkerResult,
)
from .contracts.base import ContractModel
from .contracts.inputs import ChatMessage

_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_UNSAFE_MODEL_SUFFIXES = {".py", ".pkl", ".pickle", ".bin", ".pt", ".pth"}
type WorkerErrorCode = Literal[
    "INVALID_REQUEST",
    "SAFE_LOAD_FAILED",
    "UNSUPPORTED_ARCHITECTURE",
    "TOKENIZATION_FAILED",
    "SEQUENCE_TOO_LONG",
    "TRAINING_FAILED",
    "GENERATION_FAILED",
    "NONFINITE_METRIC",
    "OUTPUT_INVALID",
    "INTERRUPTED",
    "INTERNAL",
]
type WorkerOperationResult = (
    DoctorWorkerResult
    | TrainWorkerResult
    | GenerateWorkerResult
    | QuantizeWorkerResult
    | MergeWorkerResult
    | GgufWorkerResult
)


class _ChatRow(ContractModel):
    messages: Annotated[list[ChatMessage], Field(min_length=2, max_length=256)]

    @model_validator(mode="after")
    def validate_conversation(self) -> _ChatRow:
        roles = [message.role for message in self.messages]
        if roles[0] == "system":
            roles = roles[1:]
        if len(roles) < 2 or roles[-2:] != ["user", "assistant"]:
            raise ValueError("training conversation must end with user then assistant")
        expected = "user"
        for role in roles:
            if role != expected:
                raise ValueError("training messages must alternate user and assistant")
            expected = "assistant" if expected == "user" else "user"
        return self


class _BackendFailure(Exception):
    def __init__(self, code: WorkerErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _TrainingMetrics:
    def __init__(self) -> None:
        self.final_loss: float | None = None
        self.peak_memory_gb = 0.0
        self.validation: list[ValidationObservation] = []

    def on_train_loss_report(self, train_info: dict[str, object]) -> None:
        loss = _finite_float(train_info.get("train_loss"), "training loss")
        peak = _finite_float(train_info.get("peak_memory"), "peak memory")
        if loss < 0 or peak < 0:
            raise _BackendFailure("NONFINITE_METRIC", "training reported an invalid metric")
        self.final_loss = loss
        self.peak_memory_gb = max(self.peak_memory_gb, peak)

    def on_val_loss_report(self, val_info: dict[str, object]) -> None:
        iteration = val_info.get("iteration")
        if type(iteration) is not int or iteration < 0:
            raise _BackendFailure("NONFINITE_METRIC", "validation reported an invalid iteration")
        loss = _finite_float(val_info.get("val_loss"), "validation loss")
        elapsed = _finite_float(val_info.get("val_time"), "validation elapsed time")
        if loss < 0 or elapsed < 0:
            raise _BackendFailure("NONFINITE_METRIC", "validation reported an invalid metric")
        self.validation.append(
            ValidationObservation(iteration=iteration, loss=loss, elapsedSeconds=elapsed)
        )


def completion_token_mask(
    prompt_offsets: Any,
    sequence_lengths: Any,
    target_width: int,
) -> Any:
    """Build the completion-mask-v1 tensor for one shifted target batch."""

    if target_width < 0:
        raise ValueError("target_width must be nonnegative")
    import mlx.core as mx

    steps = mx.arange(1, target_width + 1)
    return mx.logical_and(
        steps >= prompt_offsets[:, 0:1],
        steps < sequence_lengths[:, 0:1],
    )


def completion_loss(model: Any, batch: Any, lengths: Any) -> tuple[Any, Any]:
    """Compute completion-mask-v1 cross entropy for the pinned trainer."""

    import mlx.core as mx
    import mlx.nn as nn

    inputs = batch[:, :-1]
    targets = batch[:, 1:]
    logits = model(inputs)
    mask = completion_token_mask(lengths[:, 0:1], lengths[:, 1:2], targets.shape[1])
    token_count = mask.sum()
    cross_entropy = nn.losses.cross_entropy(logits, targets) * mask
    loss = cross_entropy.astype(mx.float32).sum() / token_count
    return loss, token_count


def execute_request(request: WorkerRequest) -> WorkerResult:
    """Execute one validated request and always return a validated worker result."""

    _harden_worker_environment()
    branch = request.root
    try:
        result: WorkerOperationResult
        if isinstance(branch, DoctorWorkerRequest):
            result = _doctor()
        elif isinstance(branch, TrainWorkerRequest):
            result = _train(branch)
        elif isinstance(branch, GenerateWorkerRequest):
            result = _generate(branch)
        elif isinstance(branch, QuantizeWorkerRequest):
            result = _quantize(branch)
        elif isinstance(branch, MergeWorkerRequest):
            result = _merge(branch)
        elif isinstance(branch, GgufWorkerRequest):
            result = _gguf(branch)
        else:  # pragma: no cover - the discriminated union is exhaustive
            raise _BackendFailure("INTERNAL", "unsupported validated worker operation")
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": branch.requestId,
                "operation": branch.operation,
                "ok": True,
                "result": result.model_dump(mode="json"),
            }
        )
    except KeyboardInterrupt:
        return _failure(branch.requestId, branch.operation, "INTERRUPTED", "worker interrupted")
    except _BackendFailure as error:
        return _failure(branch.requestId, branch.operation, error.code, error.message)
    except Exception:
        codes: dict[str, WorkerErrorCode] = {
            "train": "TRAINING_FAILED",
            "generate": "GENERATION_FAILED",
            "quantize": "OUTPUT_INVALID",
            "merge": "OUTPUT_INVALID",
            "gguf": "OUTPUT_INVALID",
            "doctor": "INTERNAL",
        }
        code = codes[branch.operation]
        return _failure(branch.requestId, branch.operation, code, "worker operation failed")


def run_request_file(request_path: Path, result_path: Path) -> int:
    """Read, execute, and exclusively write one private worker exchange."""

    try:
        request = _read_request(request_path)
    except (OSError, ValueError, ValidationError):
        return 2
    _harden_worker_environment()
    with redirect_stdout(sys.stderr):
        result = execute_request(request)
    try:
        _write_result(result_path, result)
    except OSError:
        return 3
    return 0


def _harden_worker_environment() -> None:
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[name] = "1"
    for name in (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HUGGINGFACEHUB_API_TOKEN",
        "WANDB_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        os.environ.pop(name, None)


def _doctor() -> DoctorWorkerResult:
    try:
        import importlib.metadata

        import mlx.core as mx

        mlx_version = importlib.metadata.version("mlx")
        mlx_lm_version = importlib.metadata.version("mlx-lm")
        metal_available = bool(mx.metal.is_available())
    except Exception:
        return DoctorWorkerResult(
            mlxImportAvailable=False,
            metalAvailable=False,
            mlxVersion=None,
            mlxLmVersion=None,
            unavailableReason="MLX capability probe failed",
        )
    return DoctorWorkerResult(
        mlxImportAvailable=True,
        metalAvailable=metal_available,
        mlxVersion=mlx_version,
        mlxLmVersion=mlx_lm_version,
        unavailableReason=None,
    )


def _train(request: TrainWorkerRequest) -> TrainWorkerResult:
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten
    from mlx_lm.tuner.datasets import CacheDataset, ChatDataset
    from mlx_lm.tuner.trainer import TrainingArgs, train
    from mlx_lm.tuner.utils import linear_to_lora_layers

    namespace = request.namespace
    output_path = Path(request.outputPath)
    if Path(namespace.adapter_path) != output_path:
        raise _BackendFailure("INVALID_REQUEST", "adapter output paths do not match")
    _require_new_output(output_path)
    train_rows = _read_chat_rows(Path(request.trainPath))
    validation_rows = _read_chat_rows(Path(request.validationPath))
    if len(train_rows) < namespace.batch_size or len(validation_rows) < namespace.batch_size:
        raise _BackendFailure("INVALID_REQUEST", "dataset is smaller than the configured batch")

    model, tokenizer, _ = _safe_load(namespace.model, return_config=True)
    train_data = ChatDataset(
        [row.model_dump(mode="python") for row in train_rows], tokenizer, mask_prompt=True
    )
    validation_data = ChatDataset(
        [row.model_dump(mode="python") for row in validation_rows],
        tokenizer,
        mask_prompt=True,
    )
    train_cache = CacheDataset(train_data)
    validation_cache = CacheDataset(validation_data)
    _precache_dataset(train_cache, namespace.max_seq_length)
    _precache_dataset(validation_cache, namespace.max_seq_length)

    np.random.seed(namespace.seed)
    mx.random.seed(namespace.seed)
    model.freeze()
    if namespace.num_layers > len(model.layers):
        raise _BackendFailure("INVALID_REQUEST", "num_layers exceeds model layer count")
    lora_parameters = namespace.lora_parameters.model_dump(mode="python")
    linear_to_lora_layers(model, namespace.num_layers, lora_parameters, use_dora=False)
    if namespace.resume_adapter_file is not None:
        resume_path = Path(namespace.resume_adapter_file)
        if not resume_path.is_file() or resume_path.is_symlink():
            raise _BackendFailure("INVALID_REQUEST", "resume adapter is not a regular file")
        model.load_weights(str(resume_path), strict=False)

    trainable = dict(tree_flatten(model.trainable_parameters()))
    trainable_names = sorted(trainable)
    if not trainable_names:
        raise _BackendFailure("UNSUPPORTED_ARCHITECTURE", "model has no trainable LoRA tensors")
    mx.eval(*trainable.values())

    output_path.mkdir(mode=0o700, parents=False)
    adapter_file = output_path / "adapters.safetensors"
    adapter_config = output_path / "adapter_config.json"
    _write_json_private(adapter_config, namespace.model_dump(mode="json"))

    metrics = _TrainingMetrics()
    optimizer = optim.AdamW(learning_rate=namespace.learning_rate, weight_decay=0.0)
    training_args = TrainingArgs(
        batch_size=namespace.batch_size,
        iters=namespace.iters,
        val_batches=namespace.val_batches,
        steps_per_report=namespace.steps_per_report,
        steps_per_eval=namespace.steps_per_eval,
        steps_per_save=namespace.save_every,
        max_seq_length=namespace.max_seq_length,
        adapter_file=str(adapter_file),
        grad_checkpoint=namespace.grad_checkpoint,
        grad_accumulation_steps=namespace.grad_accumulation_steps,
        clear_cache_threshold=namespace.clear_cache_threshold,
    )
    started = time.perf_counter()
    train(
        model=model,
        optimizer=optimizer,
        train_dataset=train_cache,
        val_dataset=validation_cache,
        args=training_args,
        loss=completion_loss,
        training_callback=cast(Any, metrics),
    )
    elapsed = time.perf_counter() - started
    if metrics.final_loss is None:
        raise _BackendFailure("NONFINITE_METRIC", "trainer did not report a final loss")
    _validate_adapter_weights(adapter_file, trainable_names)
    return TrainWorkerResult(
        finalLoss=metrics.final_loss,
        validation=metrics.validation,
        peakMemoryBytes=int(metrics.peak_memory_gb * 1_000_000_000),
        elapsedSeconds=elapsed,
        adapterWeightsPath=str(adapter_file),
        adapterConfigPath=str(adapter_config),
        trainableTensorNames=trainable_names,
    )


def _generate(request: GenerateWorkerRequest) -> GenerateWorkerResult:
    import mlx.core as mx
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = _safe_load(request.modelPath, adapter_path=request.adapterPath)
    messages = [message.model_dump(mode="python") for message in request.messages]
    try:
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
        )
    except Exception as error:
        raise _BackendFailure("TOKENIZATION_FAILED", "chat prompt tokenization failed") from error
    if not isinstance(prompt, list) or not prompt:
        raise _BackendFailure("TOKENIZATION_FAILED", "chat prompt produced no token IDs")
    mx.random.seed(request.seed)
    sampler = make_sampler(temp=0.0)
    text_parts: list[str] = []
    scores: list[float] = []
    finish_reason: Literal["stop", "length"] | None = None
    started = time.perf_counter()
    try:
        for response in stream_generate(
            model,
            tokenizer,
            prompt,
            max_tokens=request.maxTokens,
            sampler=sampler,
        ):
            text_parts.append(response.text)
            if response.finish_reason == "stop":
                finish_reason = "stop"
                continue
            score = cast(float, response.logprobs[response.token].item())
            if not math.isfinite(score):
                raise _BackendFailure("NONFINITE_METRIC", "generation produced a nonfinite score")
            scores.append(score)
            if response.finish_reason == "length":
                finish_reason = "length"
    except _BackendFailure:
        raise
    except Exception as error:
        raise _BackendFailure("GENERATION_FAILED", "model generation failed") from error
    if finish_reason is None:
        raise _BackendFailure("GENERATION_FAILED", "generation ended without a finish reason")
    elapsed = time.perf_counter() - started
    if scores:
        return GenerateWorkerResult(
            generated="".join(text_parts),
            generatedTokens=len(scores),
            meanTokenLogprob=sum(scores) / len(scores),
            finishReason=finish_reason,
            elapsedSeconds=elapsed,
        )
    return GenerateWorkerResult(
        generated="".join(text_parts),
        generatedTokens=0,
        meanTokenLogprob=None,
        meanTokenLogprobUnavailableReason="no-generated-tokens",
        finishReason=finish_reason,
        elapsedSeconds=elapsed,
    )


def _quantize(request: QuantizeWorkerRequest) -> QuantizeWorkerResult:
    from mlx_lm.convert import convert

    model_path = _validate_local_model_dir(Path(request.modelPath))
    output_path = Path(request.outputPath)
    _require_new_output(output_path)
    _, input_dtypes = _safetensor_summary(model_path, "model*.safetensors")
    try:
        convert(
            hf_path=str(model_path),
            mlx_path=str(output_path),
            quantize=True,
            q_group_size=request.groupSize,
            q_bits=request.bits,
            q_mode=request.mode,
            trust_remote_code=False,
        )
    except Exception as error:
        raise _BackendFailure("OUTPUT_INVALID", "model quantization failed") from error
    tensor_count, _ = _safetensor_summary(output_path, "model*.safetensors")
    return QuantizeWorkerResult(
        outputPath=str(output_path),
        tensorCount=tensor_count,
        fromPrecision="/".join(sorted(input_dtypes)),
        toPrecision=f"{request.bits}-bit-affine",
    )


def _merge(request: MergeWorkerRequest) -> MergeWorkerResult:
    from mlx.utils import tree_flatten, tree_unflatten
    from mlx_lm.utils import dequantize_model, save

    model, tokenizer, config = _safe_load(
        request.modelPath,
        adapter_path=request.adapterPath,
        return_config=True,
    )
    quantized = "quantization" in config or "quantization_config" in config
    if request.dequantize != quantized:
        raise _BackendFailure("INVALID_REQUEST", "dequantize must match model quantization")
    fused_layers = [
        (name, module.fuse(dequantize=request.dequantize))
        for name, module in model.named_modules()
        if hasattr(module, "fuse")
    ]
    if not fused_layers:
        raise _BackendFailure("OUTPUT_INVALID", "adapter contains no fusible layers")
    model.update_modules(tree_unflatten(fused_layers))
    if request.dequantize:
        model = dequantize_model(model)
        config.pop("quantization", None)
        config.pop("quantization_config", None)
    tensor_count = len(dict(tree_flatten(model.parameters())))
    if tensor_count == 0:
        raise _BackendFailure("OUTPUT_INVALID", "merged model has no tensors")
    output_path = Path(request.outputPath)
    _require_new_output(output_path)
    try:
        save(
            output_path,
            request.modelPath,
            model,
            tokenizer,
            config,
            donate_model=False,
        )
    except Exception as error:
        raise _BackendFailure("OUTPUT_INVALID", "merged model save failed") from error
    saved_count, _ = _safetensor_summary(output_path, "model*.safetensors")
    if saved_count != tensor_count:
        raise _BackendFailure("OUTPUT_INVALID", "merged tensor inventory changed during save")
    return MergeWorkerResult(
        outputPath=str(output_path),
        tensorCount=saved_count,
        dequantized=request.dequantize,
    )


def _gguf(request: GgufWorkerRequest) -> GgufWorkerResult:
    from gguf import GGUFReader
    from mlx.utils import tree_flatten
    from mlx_lm.gguf import convert_to_gguf

    model_path = _validate_local_model_dir(Path(request.modelPath))
    output_path = Path(request.outputPath)
    _require_new_output(output_path)
    model, _, config = _safe_load(str(model_path), return_config=True)
    model_type = config.get("model_type")
    if model_type not in {"llama", "mistral", "mixtral"}:
        raise _BackendFailure("UNSUPPORTED_ARCHITECTURE", "model type is unsupported for GGUF")
    if "quantization" in config or "quantization_config" in config:
        raise _BackendFailure("OUTPUT_INVALID", "GGUF conversion requires dense weights")
    weights = dict(tree_flatten(model.parameters()))
    if not weights:
        raise _BackendFailure("OUTPUT_INVALID", "model has no tensors")
    try:
        convert_to_gguf(model_path, weights, config, str(output_path))
        reader = GGUFReader(output_path)
    except Exception as error:
        raise _BackendFailure("OUTPUT_INVALID", "GGUF conversion or inspection failed") from error
    version = int(reader.fields["GGUF.version"].parts[-1][0])
    metadata_count = int(reader.fields["GGUF.kv_count"].parts[-1][0])
    metadata = GgufExportMetadata(
        format="gguf",
        ggufVersion=version,
        tensorCount=len(reader.tensors),
        metadataKeyCount=metadata_count,
        quantizationType=request.outputPrecision,
    )
    return GgufWorkerResult(outputPath=str(output_path), metadata=metadata)


def _safe_load(
    model_path: str,
    *,
    adapter_path: str | None = None,
    return_config: bool = False,
) -> Any:
    from mlx_lm.utils import load

    path = _validate_local_model_dir(Path(model_path))
    if adapter_path is not None:
        adapter = Path(adapter_path)
        if not adapter.is_dir() or adapter.is_symlink():
            raise _BackendFailure("SAFE_LOAD_FAILED", "adapter path is not a local directory")
    try:
        loaded = load(
            str(path),
            tokenizer_config={"trust_remote_code": False, "local_files_only": True},
            adapter_path=adapter_path,
            return_config=return_config,
        )
    except ValueError as error:
        raise _BackendFailure(
            "UNSUPPORTED_ARCHITECTURE", "model architecture is unsupported"
        ) from error
    except Exception as error:
        raise _BackendFailure("SAFE_LOAD_FAILED", "local model load failed") from error
    return cast(Any, loaded)


def _validate_local_model_dir(path: Path) -> Path:
    if not path.is_dir() or path.is_symlink():
        raise _BackendFailure("SAFE_LOAD_FAILED", "model path is not a local directory")
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise _BackendFailure("SAFE_LOAD_FAILED", "model directory contains a symlink")
        if entry.is_file() and entry.suffix.lower() in _UNSAFE_MODEL_SUFFIXES:
            raise _BackendFailure("SAFE_LOAD_FAILED", "model directory contains an unsafe file")
    config_path = path / "config.json"
    try:
        config = _load_json(config_path)
    except (OSError, ValueError) as error:
        raise _BackendFailure("SAFE_LOAD_FAILED", "model config is invalid") from error
    if not isinstance(config, dict):
        raise _BackendFailure("SAFE_LOAD_FAILED", "model config is not an object")
    if "auto_map" in config or "model_file" in config:
        raise _BackendFailure("SAFE_LOAD_FAILED", "custom model code is prohibited")
    return path


def _read_chat_rows(path: Path) -> list[_ChatRow]:
    if not path.is_file() or path.is_symlink():
        raise _BackendFailure("INVALID_REQUEST", "chat dataset is not a regular file")
    rows: list[_ChatRow] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.endswith("\n") or line.endswith("\r\n") or not line.strip():
                    raise ValueError("invalid JSONL line")
                rows.append(_ChatRow.model_validate(_loads_json(line)))
    except (OSError, ValueError, ValidationError) as error:
        raise _BackendFailure("INVALID_REQUEST", "chat dataset is invalid") from error
    if not rows:
        raise _BackendFailure("INVALID_REQUEST", "chat dataset is empty")
    return rows


def _precache_dataset(dataset: Any, max_sequence_length: int) -> None:
    for index in range(len(dataset)):
        tokens, prompt_offset = dataset[index]
        sequence_length = len(tokens)
        if sequence_length > max_sequence_length:
            raise _BackendFailure(
                "SEQUENCE_TOO_LONG", "tokenized record exceeds max sequence length"
            )
        supervised_tokens = sequence_length - max(int(prompt_offset), 1)
        if supervised_tokens <= 0:
            raise _BackendFailure("INVALID_REQUEST", "record has no supervised completion tokens")


def _validate_adapter_weights(path: Path, expected_names: list[str]) -> None:
    import mlx.core as mx

    if not path.is_file() or path.is_symlink():
        raise _BackendFailure("OUTPUT_INVALID", "adapter weights were not written")
    try:
        weights = cast(dict[str, Any], mx.load(str(path)))
    except Exception as error:
        raise _BackendFailure("OUTPUT_INVALID", "adapter weights cannot be loaded") from error
    names = sorted(weights)
    if names != expected_names or not names:
        raise _BackendFailure("OUTPUT_INVALID", "adapter tensor names do not match training")
    for tensor in weights.values():
        finite = bool(mx.all(mx.isfinite(tensor)).item())
        if tensor.size == 0 or not finite:
            raise _BackendFailure("OUTPUT_INVALID", "adapter contains an empty or nonfinite tensor")


def _safetensor_summary(path: Path, pattern: str) -> tuple[int, set[str]]:
    from safetensors import safe_open

    files = sorted(path.glob(pattern))
    if not files:
        raise _BackendFailure("OUTPUT_INVALID", "no Safetensors weights were produced")
    names: set[str] = set()
    dtypes: set[str] = set()
    for file_path in files:
        if file_path.is_symlink():
            raise _BackendFailure("OUTPUT_INVALID", "Safetensors output is a symlink")
        try:
            with safe_open(file_path, framework="np") as handle:
                for name in handle.keys():
                    if name in names:
                        raise _BackendFailure("OUTPUT_INVALID", "duplicate tensor name")
                    names.add(name)
                    dtypes.add(str(handle.get_slice(name).get_dtype()))
        except _BackendFailure:
            raise
        except Exception as error:
            raise _BackendFailure("OUTPUT_INVALID", "Safetensors output is invalid") from error
    if not names or not dtypes:
        raise _BackendFailure("OUTPUT_INVALID", "Safetensors output is empty")
    return len(names), dtypes


def _read_request(path: Path) -> WorkerRequest:
    if path.is_symlink() or not path.is_file():
        raise ValueError("request is not a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_REQUEST_BYTES:
        raise ValueError("request size is invalid")
    raw = path.read_text(encoding="utf-8")
    return WorkerRequest.model_validate(_loads_json(raw))


def _write_result(path: Path, result: WorkerResult) -> None:
    encoded = (result.model_dump_json() + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _write_json_private(path: Path, value: object) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _load_json(path: Path) -> object:
    return _loads_json(path.read_text(encoding="utf-8"))


def _loads_json(value: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    return cast(object, json.loads(value, object_pairs_hook=reject_duplicates))


def _require_new_output(path: Path) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise _BackendFailure("INVALID_REQUEST", "output path must be new with an existing parent")


def _finite_float(value: object, label: str) -> float:
    if type(value) not in {float, int}:
        raise _BackendFailure("NONFINITE_METRIC", f"{label} has an invalid type")
    converted = float(cast(float | int, value))
    if not math.isfinite(converted):
        raise _BackendFailure("NONFINITE_METRIC", f"{label} is nonfinite")
    return converted


def _failure(
    request_id: str,
    operation: str,
    code: WorkerErrorCode,
    message: str,
) -> WorkerResult:
    return WorkerResult.model_validate(
        {
            "schemaVersion": 1,
            "requestId": request_id,
            "operation": operation,
            "ok": False,
            "error": {"code": code, "message": message},
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated Foliqant MLX worker request")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    arguments = parser.parse_args()
    return run_request_file(arguments.request, arguments.result)


if __name__ == "__main__":
    raise SystemExit(main())

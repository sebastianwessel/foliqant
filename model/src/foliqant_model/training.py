"""Parent-side orchestration for shared LoRA training and customer customization."""

from __future__ import annotations

import importlib.metadata
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np
from pydantic import TypeAdapter, ValidationError
from safetensors import SafetensorError, safe_open

from .artifacts import (
    ArtifactTransaction,
    build_inventory,
    copy_verified_file,
    create_manifest,
    load_verified_artifact,
    parent_snapshot,
    producer_identity,
    require_disjoint_output,
)
from .configuration import load_config, read_document
from .contracts import (
    ArtifactManifest,
    ConfigIdentity,
    DatasetDetails,
    FileEntry,
    LeakageEntry,
    MlxTrainNamespace,
    TrainConfig,
    TrainWorkerRequest,
    TrainWorkerResult,
    VersionedComponent,
    WorkerFailure,
    WorkerRequest,
    canonical_digest,
)
from .contracts.base import Command, Id
from .errors import ModelError
from .execution import run_worker
from .lineage import (
    VerifiedArtifact,
    has_customer_ancestry,
    leakage_reference,
    materialize_inherited_leakage,
    merge_leakage_entries,
    read_leakage_index,
    union_source_rights,
    write_leakage_index,
)

_ID_ADAPTER = TypeAdapter(Id)
_PUBLISHED_ADAPTER_FILES = {"adapter_config.json", "adapters.safetensors"}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_customer(customer: str | None) -> str | None:
    if customer is None:
        return None
    try:
        return _ID_ADAPTER.validate_python(customer, strict=True)
    except ValidationError as error:
        raise ModelError("ARGUMENT_INVALID", "Customer identifier is invalid") from error


def _validate_training_parent(manifest: ArtifactManifest, customer: str | None) -> None:
    parent = manifest.root
    if customer is None:
        accepted = (
            (parent.kind == "checkpoint" and parent.stage == "upstream")
            or (parent.kind == "merged" and parent.stage == "shared")
            or (
                parent.kind == "quantized"
                and (
                    (
                        parent.stage == "upstream"
                        and len(parent.parents) == 1
                        and parent.parents[0].kind == "checkpoint"
                    )
                    or (
                        parent.stage == "shared"
                        and len(parent.parents) == 1
                        and parent.parents[0].kind == "merged"
                        and parent.parents[0].stage == "shared"
                    )
                )
            )
        )
        if not accepted or has_customer_ancestry(parent):
            raise ModelError("LINEAGE_MISMATCH", "Shared training model lineage is invalid")
        return
    accepted = (parent.kind == "merged" and parent.stage == "shared") or (
        parent.kind == "quantized"
        and parent.stage == "shared"
        and len(parent.parents) == 1
        and parent.parents[0].kind == "merged"
        and parent.parents[0].stage == "shared"
    )
    if not accepted or has_customer_ancestry(parent):
        raise ModelError("LINEAGE_MISMATCH", "Customer customization model lineage is invalid")


def _validate_dataset(
    manifest: ArtifactManifest, config: TrainConfig, customer: str | None
) -> DatasetDetails:
    if manifest.root.kind != "dataset":
        raise ModelError("LINEAGE_MISMATCH", "Training dataset artifact kind is invalid")
    details = manifest.root.details
    if details.partitions.train.records < config.batchSize:
        raise ModelError("CONFIG_INVALID", "Training partition is smaller than batchSize")
    if details.partitions.validation.records < config.batchSize:
        raise ModelError("CONFIG_INVALID", "Validation partition is smaller than batchSize")
    if customer is None and any(
        not right.sharedTrainingAllowed for right in manifest.root.sourceRights
    ):
        raise ModelError(
            "DATA_RIGHTS_DENIED", "Shared training requires explicit source permission"
        )
    return details


def _adapter_tensor_shapes(path: Path) -> dict[str, tuple[int, ...]]:
    if not path.is_file() or path.is_symlink():
        raise ModelError("OUTPUT_INVALID", "Adapter weights are not a regular file")
    shapes: dict[str, tuple[int, ...]] = {}
    try:
        with safe_open(path, framework="numpy") as handle:
            for name in handle.keys():
                tensor = np.asarray(handle.get_tensor(name))
                if tensor.size == 0 or not np.isfinite(tensor).all():
                    raise ModelError(
                        "OUTPUT_INVALID", "Adapter contains empty or nonfinite tensors"
                    )
                shapes[name] = tuple(int(value) for value in tensor.shape)
    except ModelError:
        raise
    except (OSError, SafetensorError, TypeError, ValueError) as error:
        raise ModelError("OUTPUT_INVALID", "Adapter weights are invalid") from error
    if not shapes:
        raise ModelError("OUTPUT_INVALID", "Adapter contains no tensors")
    return {name: shapes[name] for name in sorted(shapes)}


def _validate_lora_dimensions(shapes: dict[str, tuple[int, ...]], rank: int) -> None:
    pairs: dict[str, dict[str, tuple[int, ...]]] = {}
    for name, shape in shapes.items():
        if name.endswith(".lora_a"):
            stem, side = name[:-7], "a"
        elif name.endswith(".lora_b"):
            stem, side = name[:-7], "b"
        else:
            raise ModelError("OUTPUT_INVALID", "Adapter contains an unsupported tensor name")
        if len(shape) not in {2, 3}:
            raise ModelError("OUTPUT_INVALID", "LoRA tensor dimensions are unsupported")
        pairs.setdefault(stem, {})[side] = shape
    if not pairs:
        raise ModelError("OUTPUT_INVALID", "Adapter has no LoRA tensor pairs")
    for pair in pairs.values():
        if set(pair) != {"a", "b"}:
            raise ModelError("OUTPUT_INVALID", "Adapter LoRA tensor pair is incomplete")
        left, right = pair["a"], pair["b"]
        standard = len(left) == 2 and len(right) == 2 and left[1] == rank and right[0] == rank
        switch = (
            len(left) == 3
            and len(right) == 3
            and left[0] == right[0]
            and left[1] == rank
            and right[2] == rank
        )
        if not standard and not switch:
            raise ModelError("OUTPUT_INVALID", "Adapter LoRA dimensions are incompatible")


def _read_adapter_namespace(path: Path) -> MlxTrainNamespace:
    try:
        return MlxTrainNamespace.model_validate(read_document(path), strict=True)
    except ModelError as error:
        raise ModelError("OUTPUT_INVALID", "Adapter configuration is invalid") from error
    except ValidationError as error:
        raise ModelError("OUTPUT_INVALID", "Adapter configuration is invalid") from error


def _validate_warm_start(
    directory: Path,
    manifest: ArtifactManifest,
    model: ArtifactManifest,
    config: TrainConfig,
    customer: str | None,
) -> dict[str, tuple[int, ...]]:
    node = manifest.root
    expected_stage = "shared" if customer is None else "customer"
    if node.kind != "adapter" or node.stage != expected_stage or node.customer != customer:
        raise ModelError("LINEAGE_MISMATCH", "Warm-start scope is incompatible")
    if node.details.modelArtifactId != model.root.artifactId:
        raise ModelError("LINEAGE_MISMATCH", "Warm-start model parent is not exact")
    previous = node.details.trainConfig
    for field in ("rank", "scale", "dropout", "numLayers"):
        if getattr(previous, field) != getattr(config, field):
            raise ModelError("LINEAGE_MISMATCH", "Warm-start LoRA configuration is incompatible")
    namespace = _read_adapter_namespace(directory / "adapter_config.json")
    if (
        namespace.num_layers != config.numLayers
        or namespace.lora_parameters.rank != config.rank
        or namespace.lora_parameters.scale != config.scale
        or namespace.lora_parameters.dropout != config.dropout
    ):
        raise ModelError("LINEAGE_MISMATCH", "Warm-start adapter configuration is inconsistent")
    shapes = _adapter_tensor_shapes(directory / "adapters.safetensors")
    _validate_lora_dimensions(shapes, config.rank)
    if list(shapes) != node.details.trainableTensorNames:
        raise ModelError("LINEAGE_MISMATCH", "Warm-start resolved target keys are inconsistent")
    return shapes


def _copy_partition(source: Path, target: Path, expected: FileEntry) -> None:
    copy_verified_file(source, target, expected)


def _training_namespace(
    config: TrainConfig,
    model_path: Path,
    data_path: Path,
    adapter_path: Path,
    warm_start: Path | None,
) -> MlxTrainNamespace:
    return MlxTrainNamespace.model_validate(
        {
            "model": str(model_path.absolute()),
            "data": str(data_path.absolute()),
            "adapter_path": str(adapter_path.absolute()),
            "resume_adapter_file": (
                None
                if warm_start is None
                else str((warm_start / "adapters.safetensors").absolute())
            ),
            "train": True,
            "test": False,
            "fine_tune_type": "lora",
            "optimizer": "adamw",
            "optimizer_config": {"adamw": {"weight_decay": 0}},
            "seed": config.seed,
            "num_layers": config.numLayers,
            "batch_size": config.batchSize,
            "iters": config.steps,
            "val_batches": config.validationBatches,
            "learning_rate": config.learningRate,
            "steps_per_report": 1,
            "steps_per_eval": config.validationEvery,
            "save_every": config.saveEvery,
            "test_batches": 500,
            "max_seq_length": config.maxSequenceLength,
            "config": None,
            "grad_checkpoint": config.gradientCheckpointing,
            "grad_accumulation_steps": config.gradientAccumulation,
            "clear_cache_threshold": 0,
            "lr_schedule": None,
            "report_to": None,
            "project_name": None,
            "lora_parameters": {
                "rank": config.rank,
                "scale": config.scale,
                "dropout": config.dropout,
            },
            "mask_prompt": True,
        },
        strict=True,
    )


def _worker_failure(result: WorkerFailure) -> ModelError:
    error = result.error
    code = error.code
    if code == "INTERRUPTED":
        return ModelError("INTERRUPTED", "Training worker was interrupted")
    if code == "UNSUPPORTED_ARCHITECTURE":
        return ModelError("ARCHITECTURE_UNSUPPORTED", "Model architecture is unsupported")
    if code in {"INVALID_REQUEST", "TOKENIZATION_FAILED", "SEQUENCE_TOO_LONG"}:
        return ModelError("CONFIG_INVALID", "Training inputs are incompatible with configuration")
    if code in {"NONFINITE_METRIC", "OUTPUT_INVALID"}:
        return ModelError("OUTPUT_INVALID", "Training worker produced invalid output")
    return ModelError("BACKEND_FAILED", "Training worker failed")


def _inspect_worker_output(
    output_path: Path,
    namespace: MlxTrainNamespace,
    result: TrainWorkerResult,
    warm_shapes: dict[str, tuple[int, ...]] | None,
) -> tuple[dict[str, tuple[int, ...]], list[FileEntry]]:
    if Path(result.adapterWeightsPath) != output_path / "adapters.safetensors":
        raise ModelError("OUTPUT_INVALID", "Worker reported an unexpected adapter weights path")
    if Path(result.adapterConfigPath) != output_path / "adapter_config.json":
        raise ModelError("OUTPUT_INVALID", "Worker reported an unexpected adapter config path")
    checkpoints = {
        f"{iteration:07d}_adapters.safetensors"
        for iteration in range(namespace.save_every, namespace.iters + 1, namespace.save_every)
    }
    expected_paths = _PUBLISHED_ADAPTER_FILES | checkpoints
    inventory = build_inventory(output_path)
    paths = {entry.path for entry in inventory}
    if paths != expected_paths:
        raise ModelError("OUTPUT_INVALID", "Worker adapter output file set is invalid")
    observed_namespace = _read_adapter_namespace(output_path / "adapter_config.json")
    if observed_namespace.model_dump(mode="json") != namespace.model_dump(mode="json"):
        raise ModelError("OUTPUT_INVALID", "Worker adapter configuration changed")
    shapes = _adapter_tensor_shapes(output_path / "adapters.safetensors")
    if list(shapes) != result.trainableTensorNames:
        raise ModelError("OUTPUT_INVALID", "Worker tensor report does not match adapter weights")
    _validate_lora_dimensions(shapes, namespace.lora_parameters.rank)
    for checkpoint in sorted(checkpoints):
        checkpoint_shapes = _adapter_tensor_shapes(output_path / checkpoint)
        if checkpoint_shapes != shapes:
            raise ModelError("OUTPUT_INVALID", "Worker checkpoint tensor shapes changed")
    if warm_shapes is not None and shapes != warm_shapes:
        raise ModelError("OUTPUT_INVALID", "Warm-start resolved tensor dimensions changed")
    if not math.isfinite(result.finalLoss) or not math.isfinite(result.elapsedSeconds):
        raise ModelError("OUTPUT_INVALID", "Worker reported a nonfinite training metric")
    return shapes, inventory


def _backend_components() -> list[VersionedComponent]:
    try:
        mlx_version = importlib.metadata.version("mlx")
        mlx_lm_version = importlib.metadata.version("mlx-lm")
    except importlib.metadata.PackageNotFoundError as error:
        raise ModelError(
            "DEPENDENCY_MISSING", "Pinned MLX training dependencies are unavailable"
        ) from error
    return [
        VersionedComponent(name="mlx-lm", version=mlx_lm_version, role="backend"),
        VersionedComponent(name="foliqant-completion-loss", version="1", role="library"),
        VersionedComponent(name="mlx", version=mlx_version, role="library"),
    ]


def _same_manifest(left: ArtifactManifest, right: ArtifactManifest) -> bool:
    return left.root.model_dump(mode="json") == right.root.model_dump(mode="json")


def train_model(
    config_path: Path,
    model_path: Path,
    dataset_path: Path,
    output: Path,
    *,
    customer: str | None = None,
    warm_start: Path | None = None,
) -> ArtifactManifest:
    """Train a shared adapter or customize a shared merged model for one customer."""

    customer = _validate_customer(customer)
    config = load_config(config_path, TrainConfig)
    if config.steps % config.gradientAccumulation != 0:
        raise ModelError("CONFIG_INVALID", "steps must be divisible by gradientAccumulation")
    model = load_verified_artifact(model_path)
    dataset = load_verified_artifact(dataset_path)
    _validate_training_parent(model, customer)
    dataset_details = _validate_dataset(dataset, config, customer)
    warm: ArtifactManifest | None = None
    warm_shapes: dict[str, tuple[int, ...]] | None = None
    if warm_start is not None:
        warm = load_verified_artifact(warm_start)
        warm_shapes = _validate_warm_start(warm_start, warm, model, config, customer)

    input_paths = [model_path, dataset_path]
    if warm_start is not None:
        input_paths.append(warm_start)
    require_disjoint_output(output, input_paths)

    config_digest = canonical_digest(config.model_dump(mode="json"))
    command: Command = "train" if customer is None else "customize"
    parent_ids = [model.root.artifactId, dataset.root.artifactId]
    if warm is not None:
        parent_ids.append(warm.root.artifactId)
    dataset_inventory = {entry.path: entry for entry in dataset.root.files}
    with ArtifactTransaction(
        output,
        command,
        configuration=ConfigIdentity(kind="train", sha256=config_digest),
        parent_artifact_ids=parent_ids,
        dataset_artifact_id=dataset.root.artifactId,
    ) as transaction:
        data_path = transaction.workspace_path / "data"
        data_path.mkdir(mode=0o700)
        train_path = data_path / "train.jsonl"
        validation_path = data_path / "validation.jsonl"
        _copy_partition(
            dataset_path / dataset_details.chatFiles.train.path,
            train_path,
            dataset_inventory[dataset_details.chatFiles.train.path],
        )
        _copy_partition(
            dataset_path / dataset_details.chatFiles.validation.path,
            validation_path,
            dataset_inventory[dataset_details.chatFiles.validation.path],
        )
        adapter_output = transaction.workspace_path / "adapter-output"
        namespace = _training_namespace(config, model_path, data_path, adapter_output, warm_start)
        request = WorkerRequest(
            root=TrainWorkerRequest(
                schemaVersion=1,
                requestId=transaction.run_id,
                operation="train",
                namespace=namespace,
                trainPath=str(train_path.absolute()),
                validationPath=str(validation_path.absolute()),
                outputPath=str(adapter_output.absolute()),
            )
        )
        worker = run_worker(
            request,
            workspace=transaction.workspace_path,
            timeout_seconds=config.timeoutSeconds,
        )
        if not worker.root.ok:
            raise _worker_failure(worker.root)
        worker_result = cast(TrainWorkerResult, worker.root.result)
        _, worker_inventory = _inspect_worker_output(
            adapter_output, namespace, worker_result, warm_shapes
        )

        current_model = load_verified_artifact(model_path)
        current_dataset = load_verified_artifact(dataset_path)
        if not _same_manifest(model, current_model) or not _same_manifest(dataset, current_dataset):
            raise ModelError("INTEGRITY_FAILED", "Training input artifact changed during execution")
        if warm_start is not None and warm is not None:
            current_warm = load_verified_artifact(warm_start)
            if not _same_manifest(warm, current_warm):
                raise ModelError("INTEGRITY_FAILED", "Warm-start artifact changed during execution")

        worker_files = {entry.path: entry for entry in worker_inventory}
        for name in sorted(_PUBLISHED_ADAPTER_FILES):
            copy_verified_file(
                adapter_output / name,
                transaction.staging_path / name,
                worker_files[name],
            )

        lineage_artifacts = [
            VerifiedArtifact(model_path, model),
            VerifiedArtifact(dataset_path, dataset),
        ]
        if warm_start is not None and warm is not None:
            lineage_artifacts.append(VerifiedArtifact(warm_start, warm))
        inherited = materialize_inherited_leakage(
            transaction.staging_path / "inherited-leakage", lineage_artifacts
        )

        groups: list[list[LeakageEntry]] = []
        model_ref = leakage_reference(model.root)
        if model_ref is not None:
            groups.append(read_leakage_index(model_path / model_ref.path, model_ref))
        dataset_ref = dataset_details.leakageIndex.file
        dataset_entries = read_leakage_index(dataset_path / dataset_ref.path, dataset_ref)
        groups.append(
            [
                entry
                for entry in dataset_entries
                if dataset_details.assignments[entry.recordId].split in {"train", "validation"}
            ]
        )
        if warm_start is not None and warm is not None:
            warm_ref = leakage_reference(warm.root)
            if warm_ref is None:
                raise ModelError("LINEAGE_MISMATCH", "Warm-start adapter has no leakage index")
            groups.append(read_leakage_index(warm_start / warm_ref.path, warm_ref))
        used_entries = merge_leakage_entries(groups)
        used_ref = write_leakage_index(transaction.staging_path / "leakage.jsonl", used_entries)

        method = "qlora" if model.root.kind == "quantized" else "lora"
        backend_config = {
            "method": method,
            "maskPrompt": True,
            "seed": config.seed,
            "steps": config.steps,
            "batchSize": config.batchSize,
            "gradientAccumulation": config.gradientAccumulation,
            "maxSequenceLength": config.maxSequenceLength,
            "learningRate": config.learningRate,
            "numLayers": config.numLayers,
            "rank": config.rank,
            "scale": config.scale,
            "dropout": config.dropout,
            "gradientCheckpointing": config.gradientCheckpointing,
            "validationEvery": config.validationEvery,
            "validationBatches": config.validationBatches,
            "saveEvery": config.saveEvery,
            "modelArtifactId": model.root.artifactId,
            "datasetArtifactId": dataset.root.artifactId,
            "trainFileSha256": dataset_details.chatFiles.train.sha256,
            "validationFileSha256": dataset_details.chatFiles.validation.sha256,
        }
        details: dict[str, object] = {
            "modelArtifactId": model.root.artifactId,
            "datasetArtifactId": dataset.root.artifactId,
            "trainConfig": config.model_dump(mode="json"),
            "trainConfigSha256": config_digest,
            "backendConfig": backend_config,
            "usedDataLeakageIndex": {
                "file": used_ref.model_dump(mode="json"),
                "splits": ["train", "validation"],
                "entryCount": len(used_entries),
            },
            "inheritedLeakageIndexes": [item.model_dump(mode="json") for item in inherited],
            "finalLoss": worker_result.finalLoss,
            "validation": [item.model_dump(mode="json") for item in worker_result.validation],
            "trainableTensorNames": worker_result.trainableTensorNames,
            "peakMemoryBytes": {"value": worker_result.peakMemoryBytes},
            "elapsedSeconds": worker_result.elapsedSeconds,
            "adapterFormat": "mlx-lora-v1",
        }
        if warm is not None:
            details["warmStartArtifactId"] = warm.root.artifactId
        parents = [parent_snapshot(model), parent_snapshot(dataset)]
        manifests = [model, dataset]
        if warm is not None:
            parents.append(parent_snapshot(warm))
            manifests.append(warm)
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "adapter",
                "name": config.name,
                "createdAt": _timestamp(),
                "stage": "shared" if customer is None else "customer",
                **({} if customer is None else {"customer": customer}),
                "parents": [item.model_dump(mode="json") for item in parents],
                "sourceRights": [
                    item.model_dump(mode="json") for item in union_source_rights(manifests)
                ],
                "producer": producer_identity(command, _backend_components()).model_dump(
                    mode="json"
                ),
                "details": details,
            },
        )
        transaction.publish(manifest)
        return manifest

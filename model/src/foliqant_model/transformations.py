"""Parent orchestration for quantization, adapter fusion, and model export."""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from gguf import GGMLQuantizationType, GGUFReader

from .artifacts import (
    ArtifactTransaction,
    build_inventory,
    copy_verified_file,
    create_manifest,
    load_verified_artifact,
    parent_snapshot,
    producer_identity,
    require_disjoint_output,
    sha256_file,
)
from .configuration import read_document
from .contracts import (
    ArtifactManifest,
    FileEntry,
    GgufExportMetadata,
    GgufWorkerRequest,
    GgufWorkerResult,
    InheritedLeakageRef,
    MergeWorkerRequest,
    MergeWorkerResult,
    ModelIdentity,
    QuantizeWorkerRequest,
    QuantizeWorkerResult,
    VersionedComponent,
    WorkerFailure,
    WorkerRequest,
)
from .contracts.base import LeakageIndexRef, PrecisionChange, Split
from .errors import ModelError
from .execution import run_worker
from .lineage import (
    VerifiedArtifact,
    materialize_inherited_leakage,
    merge_leakage_entries,
    read_leakage_index,
    union_source_rights,
    write_leakage_index,
)
from .snapshots import SnapshotInspection, inspect_snapshot, snapshot_file_allowed

_DEFAULT_TIMEOUT_SECONDS = 3600
_MAX_TIMEOUT_SECONDS = 604_800


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_timeout(timeout_seconds: int) -> None:
    if (
        type(timeout_seconds) is not int
        or timeout_seconds < 1
        or timeout_seconds > _MAX_TIMEOUT_SECONDS
    ):
        raise ModelError("ARGUMENT_INVALID", "Timeout must be between 1 and 604800 seconds")


def _backend_components() -> list[VersionedComponent]:
    try:
        mlx_version = importlib.metadata.version("mlx")
        mlx_lm_version = importlib.metadata.version("mlx-lm")
    except importlib.metadata.PackageNotFoundError as error:
        raise ModelError("DEPENDENCY_MISSING", "Pinned MLX dependencies are unavailable") from error
    return [
        VersionedComponent(name="mlx-lm", version=mlx_lm_version, role="backend"),
        VersionedComponent(name="mlx", version=mlx_version, role="library"),
    ]


def _same_manifest(left: ArtifactManifest, right: ArtifactManifest) -> bool:
    return left.root.model_dump(mode="json") == right.root.model_dump(mode="json")


def _reverify(directory: Path, expected: ArtifactManifest) -> None:
    if not _same_manifest(expected, load_verified_artifact(directory)):
        raise ModelError("INTEGRITY_FAILED", "Transformation input changed during execution")


def _worker_failure(result: WorkerFailure, operation: str) -> ModelError:
    code = result.error.code
    if code == "INTERRUPTED":
        return ModelError("INTERRUPTED", f"{operation} worker was interrupted")
    if code == "UNSUPPORTED_ARCHITECTURE":
        return ModelError("ARCHITECTURE_UNSUPPORTED", "Model architecture is unsupported")
    if code in {"INVALID_REQUEST", "TOKENIZATION_FAILED", "SEQUENCE_TOO_LONG"}:
        return ModelError("CONFIG_INVALID", f"{operation} worker request is incompatible")
    if code in {"NONFINITE_METRIC", "OUTPUT_INVALID"}:
        return ModelError("OUTPUT_INVALID", f"{operation} worker produced invalid output")
    return ModelError("BACKEND_FAILED", f"{operation} worker failed")


def _copy_files(source: Path, destination: Path, entries: list[FileEntry]) -> None:
    for entry in entries:
        target = destination / entry.path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        copy_verified_file(source / entry.path, target, entry)


def _preserve_parent_semantics(
    model_path: Path,
    destination: Path,
    parent: ModelIdentity,
    inspection: SnapshotInspection,
    *,
    quantized: bool,
) -> SnapshotInspection:
    """Reject semantic drift and retain the exact original tokenizer assets."""
    if (
        inspection.model.architecture != parent.architecture
        or inspection.model.chatTemplateSha256 != parent.chatTemplateSha256
    ):
        raise ModelError(
            "OUTPUT_INVALID", "Conversion changed the model architecture or chat template"
        )
    before = read_document(model_path / "config.json", max_bytes=16 * 1024 * 1024)
    after = read_document(destination / "config.json", max_bytes=16 * 1024 * 1024)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ModelError("OUTPUT_INVALID", "Conversion model configuration is invalid")
    for value in (before, after):
        value.pop("quantization", None)
        value.pop("quantization_config", None)
    if before != after:
        raise ModelError("OUTPUT_INVALID", "Conversion changed the model inference configuration")
    # save_pretrained can normalize tokenizer metadata or move its chat template.
    # Conversion changes weights only; publish the exact parent tokenizer bundle.
    for relative in inspection.model.tokenizerFiles:
        (destination / relative).unlink()
    parent_inventory = {entry.path: entry for entry in build_inventory(model_path)}
    _copy_files(
        model_path,
        destination,
        [parent_inventory[path] for path in parent.tokenizerFiles],
    )
    retained_metadata = {
        "README.md",
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "NOTICE",
        "NOTICE.md",
        "generation_config.json",
    }
    for relative in sorted(retained_metadata & parent_inventory.keys()):
        target = destination / relative
        if target.exists():
            target.unlink()
        _copy_files(model_path, destination, [parent_inventory[relative]])
    preserved = inspect_snapshot(destination, quantized=quantized)
    if (
        preserved.model.tokenizerSha256 != parent.tokenizerSha256
        or preserved.model.chatTemplateSha256 != parent.chatTemplateSha256
    ):
        raise ModelError("OUTPUT_INVALID", "Conversion did not preserve the parent tokenizer")
    return preserved


def _copy_worker_snapshot(
    source: Path,
    destination: Path,
    *,
    quantized: bool,
) -> SnapshotInspection:
    before = build_inventory(source)
    inspection = inspect_snapshot(source, quantized=quantized)
    _copy_files(source, destination, before)
    if build_inventory(source) != before:
        raise ModelError("INTEGRITY_FAILED", "Worker model output changed during inspection")
    copied = inspect_snapshot(destination, quantized=quantized)
    if copied != inspection:
        raise ModelError("INTEGRITY_FAILED", "Copied model output changed identity")
    return inspection


def _validate_quantized_metadata(directory: Path, *, bits: int, group_size: int) -> None:
    value = read_document(directory / "config.json", max_bytes=16 * 1024 * 1024)
    if not isinstance(value, dict):
        raise ModelError("OUTPUT_INVALID", "Quantized model config must be an object")
    configurations = [
        value[name] for name in ("quantization", "quantization_config") if name in value
    ]
    if not configurations:
        raise ModelError("OUTPUT_INVALID", "Quantized model config has no quantization metadata")
    for configuration in configurations:
        if not isinstance(configuration, dict) or (
            configuration.get("bits") != bits
            or configuration.get("group_size") != group_size
            or configuration.get("mode") != "affine"
        ):
            raise ModelError("OUTPUT_INVALID", "Quantized model metadata does not match request")


def _leakage_index(manifest: ArtifactManifest) -> LeakageIndexRef | None:
    details = manifest.root.details
    for name in ("leakageIndex", "usedDataLeakageIndex", "exposureLeakageIndex"):
        value = getattr(details, name, None)
        if value is not None:
            return cast(LeakageIndexRef, value)
    return None


def _write_exposure(
    staging: Path,
    artifacts: list[VerifiedArtifact],
) -> tuple[LeakageIndexRef, list[InheritedLeakageRef]]:
    groups = []
    splits: set[Split] = set()
    for artifact in artifacts:
        index = _leakage_index(artifact.manifest)
        if index is None:
            continue
        groups.append(read_leakage_index(artifact.directory / index.file.path, index.file))
        splits.update(index.splits)
    entries = merge_leakage_entries(groups)
    current = write_leakage_index(staging / "leakage.jsonl", entries)
    inherited = materialize_inherited_leakage(staging / "inherited-leakage", artifacts)
    index = LeakageIndexRef(
        file=current,
        splits=sorted(splits),
        entryCount=len(entries),
    )
    return index, inherited


def _parent_precision_history(manifest: ArtifactManifest) -> list[PrecisionChange]:
    history = getattr(manifest.root.details, "precisionHistory", [])
    return [item.model_copy(deep=True) for item in cast(list[PrecisionChange], history)]


def _model_precision(manifest: ArtifactManifest) -> str:
    node = manifest.root
    if node.kind == "checkpoint":
        return node.details.weightPrecision
    history = _parent_precision_history(manifest)
    if not history:
        raise ModelError("LINEAGE_MISMATCH", "Model lineage has no recorded precision")
    return history[-1].toPrecision


def _scope_fields(manifest: ArtifactManifest) -> dict[str, object]:
    node = manifest.root
    fields: dict[str, object] = {"stage": node.stage}
    if node.customer is not None:
        fields["customer"] = node.customer
    return fields


def quantize_model(
    model_path: Path,
    output: Path,
    *,
    bits: int,
    group_size: int = 64,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> ArtifactManifest:
    """Create a verified 4-bit or 8-bit affine model artifact."""

    if type(bits) is not int or bits not in {4, 8}:
        raise ModelError("ARGUMENT_INVALID", "Quantization bits must be 4 or 8")
    if type(group_size) is not int or group_size != 64:
        raise ModelError("ARGUMENT_INVALID", "Quantization group size must be 64")
    _validate_timeout(timeout_seconds)
    model = load_verified_artifact(model_path)
    if model.root.kind not in {"checkpoint", "merged"}:
        raise ModelError("LINEAGE_MISMATCH", "Quantization requires checkpoint or merged input")
    require_disjoint_output(output, [model_path])

    with ArtifactTransaction(
        output,
        "quantize",
        parent_artifact_ids=[model.root.artifactId],
    ) as transaction:
        worker_output = transaction.workspace_path / "model-output"
        request = WorkerRequest(
            root=QuantizeWorkerRequest(
                schemaVersion=1,
                requestId=transaction.run_id,
                operation="quantize",
                modelPath=str(model_path.absolute()),
                outputPath=str(worker_output.absolute()),
                bits=cast(Literal[4, 8], bits),
                groupSize=64,
                mode="affine",
            )
        )
        worker = run_worker(
            request,
            workspace=transaction.workspace_path,
            timeout_seconds=timeout_seconds,
        )
        if not worker.root.ok:
            raise _worker_failure(worker.root, "Quantization")
        result = cast(QuantizeWorkerResult, worker.root.result)
        if Path(result.outputPath) != worker_output:
            raise ModelError("OUTPUT_INVALID", "Worker reported an unexpected model path")
        inspection = _copy_worker_snapshot(worker_output, transaction.staging_path, quantized=True)
        inspection = _preserve_parent_semantics(
            model_path,
            transaction.staging_path,
            model.root.details.model,
            inspection,
            quantized=True,
        )
        _validate_quantized_metadata(worker_output, bits=bits, group_size=group_size)
        if inspection.tensor_count != result.tensorCount:
            raise ModelError("OUTPUT_INVALID", "Worker tensor count does not match model output")
        if (
            result.fromPrecision != _model_precision(model)
            or result.toPrecision != f"{bits}-bit-affine"
        ):
            raise ModelError("OUTPUT_INVALID", "Worker precision report does not match output")
        _reverify(model_path, model)

        artifact = VerifiedArtifact(model_path, model)
        exposure, inherited = _write_exposure(transaction.staging_path, [artifact])
        history = _parent_precision_history(model)
        history.append(
            PrecisionChange(
                operation="quantize",
                fromPrecision=result.fromPrecision,
                toPrecision=result.toPrecision,
                lossy=True,
            )
        )
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "quantized",
                "name": f"q{bits}-{model.root.artifactId[:12]}",
                "createdAt": _timestamp(),
                **_scope_fields(model),
                "parents": [parent_snapshot(model).model_dump(mode="json")],
                "sourceRights": [
                    item.model_dump(mode="json") for item in union_source_rights([model])
                ],
                "producer": producer_identity("quantize", _backend_components()).model_dump(
                    mode="json"
                ),
                "details": {
                    "model": inspection.model.model_dump(mode="json"),
                    "bits": bits,
                    "groupSize": group_size,
                    "parentArtifactId": model.root.artifactId,
                    "precisionHistory": [item.model_dump(mode="json") for item in history],
                    "exposureLeakageIndex": exposure.model_dump(mode="json"),
                    "compatibility": [],
                    "inheritedLeakageIndexes": [item.model_dump(mode="json") for item in inherited],
                },
            },
        )
        transaction.publish(manifest)
        return manifest


def merge_model(
    model_path: Path,
    adapter_path: Path,
    output: Path,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> ArtifactManifest:
    """Fuse one exact adapter into its model parent and verify dense output."""

    _validate_timeout(timeout_seconds)
    model = load_verified_artifact(model_path)
    adapter = load_verified_artifact(adapter_path)
    if model.root.kind not in {"checkpoint", "quantized", "merged"}:
        raise ModelError("LINEAGE_MISMATCH", "Merge model kind is invalid")
    if adapter.root.kind != "adapter":
        raise ModelError("LINEAGE_MISMATCH", "Merge requires an adapter artifact")
    if adapter.root.details.modelArtifactId != model.root.artifactId:
        raise ModelError("LINEAGE_MISMATCH", "Adapter does not belong to the exact model")
    require_disjoint_output(output, [model_path, adapter_path])

    with ArtifactTransaction(
        output,
        "merge",
        parent_artifact_ids=[model.root.artifactId, adapter.root.artifactId],
    ) as transaction:
        worker_output = transaction.workspace_path / "model-output"
        dequantize = model.root.kind == "quantized"
        request = WorkerRequest(
            root=MergeWorkerRequest(
                schemaVersion=1,
                requestId=transaction.run_id,
                operation="merge",
                modelPath=str(model_path.absolute()),
                adapterPath=str(adapter_path.absolute()),
                outputPath=str(worker_output.absolute()),
                dequantize=dequantize,
            )
        )
        worker = run_worker(
            request,
            workspace=transaction.workspace_path,
            timeout_seconds=timeout_seconds,
        )
        if not worker.root.ok:
            raise _worker_failure(worker.root, "Merge")
        result = cast(MergeWorkerResult, worker.root.result)
        if Path(result.outputPath) != worker_output or result.dequantized != dequantize:
            raise ModelError("OUTPUT_INVALID", "Worker merge report does not match its request")
        inspection = _copy_worker_snapshot(worker_output, transaction.staging_path, quantized=False)
        inspection = _preserve_parent_semantics(
            model_path,
            transaction.staging_path,
            model.root.details.model,
            inspection,
            quantized=False,
        )
        if inspection.tensor_count != result.tensorCount:
            raise ModelError("OUTPUT_INVALID", "Worker tensor count does not match merged output")
        _reverify(model_path, model)
        _reverify(adapter_path, adapter)

        artifacts = [VerifiedArtifact(model_path, model), VerifiedArtifact(adapter_path, adapter)]
        exposure, inherited = _write_exposure(transaction.staging_path, artifacts)
        history = _parent_precision_history(model)
        input_precision = _model_precision(model)
        if dequantize:
            history.append(
                PrecisionChange(
                    operation="dequantize-for-fusion",
                    fromPrecision=input_precision,
                    toPrecision=inspection.weight_precision,
                    lossy=True,
                )
            )
            input_precision = inspection.weight_precision
        history.append(
            PrecisionChange(
                operation="fuse",
                fromPrecision=input_precision,
                toPrecision=inspection.weight_precision,
                lossy=False,
            )
        )
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "merged",
                "name": f"merged-{adapter.root.artifactId[:12]}",
                "createdAt": _timestamp(),
                **_scope_fields(adapter),
                "parents": [
                    parent_snapshot(model).model_dump(mode="json"),
                    parent_snapshot(adapter).model_dump(mode="json"),
                ],
                "sourceRights": [
                    item.model_dump(mode="json") for item in union_source_rights([model, adapter])
                ],
                "producer": producer_identity("merge", _backend_components()).model_dump(
                    mode="json"
                ),
                "details": {
                    "model": inspection.model.model_dump(mode="json"),
                    "modelParentArtifactId": model.root.artifactId,
                    "adapterArtifactId": adapter.root.artifactId,
                    "precisionHistory": [item.model_dump(mode="json") for item in history],
                    "exposureLeakageIndex": exposure.model_dump(mode="json"),
                    "inheritedLeakageIndexes": [item.model_dump(mode="json") for item in inherited],
                    "compatibility": [],
                },
            },
        )
        transaction.publish(manifest)
        return manifest


def export_model(
    model_path: Path,
    output: Path,
    *,
    format: Literal["checkpoint", "gguf"],
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> ArtifactManifest:
    """Export a verified merged artifact as checkpoint or GGUF."""

    if format not in {"checkpoint", "gguf"}:
        raise ModelError("ARGUMENT_INVALID", "Export format must be checkpoint or gguf")
    _validate_timeout(timeout_seconds)
    model = load_verified_artifact(model_path)
    if model.root.kind != "merged":
        raise ModelError("LINEAGE_MISMATCH", "Export requires a merged model artifact")
    if format == "gguf" and model.root.details.model.architecture not in {
        "llama",
        "mistral",
        "mixtral",
    }:
        raise ModelError("ARCHITECTURE_UNSUPPORTED", "Model architecture cannot export to GGUF")
    require_disjoint_output(output, [model_path])

    with ArtifactTransaction(
        output,
        "export",
        parent_artifact_ids=[model.root.artifactId],
    ) as transaction:
        history = _parent_precision_history(model)
        if format == "checkpoint":
            entries = [
                item
                for item in model.root.files
                if "/" not in item.path and snapshot_file_allowed(item.path)
            ]
            _copy_files(model_path, transaction.staging_path, entries)
            inspection = inspect_snapshot(transaction.staging_path, quantized=False)
            safetensors_count = sum(entry.path.endswith(".safetensors") for entry in entries)
            metadata: dict[str, object] = {
                "format": "checkpoint",
                "safetensorsFileCount": safetensors_count,
                "tensorCount": inspection.tensor_count,
            }
            identity = inspection.model
            compatibility_target = "transformers"
        else:
            gguf_path = transaction.workspace_path / "model.gguf"
            request = WorkerRequest(
                root=GgufWorkerRequest(
                    schemaVersion=1,
                    requestId=transaction.run_id,
                    operation="gguf",
                    modelPath=str(model_path.absolute()),
                    outputPath=str(gguf_path.absolute()),
                    outputPrecision="F16",
                )
            )
            worker = run_worker(
                request,
                workspace=transaction.workspace_path,
                timeout_seconds=timeout_seconds,
            )
            if not worker.root.ok:
                raise _worker_failure(worker.root, "GGUF export")
            result = cast(GgufWorkerResult, worker.root.result)
            if Path(result.outputPath) != gguf_path:
                raise ModelError("OUTPUT_INVALID", "Worker reported an unexpected GGUF path")
            metadata_model = _inspect_gguf(gguf_path)
            if metadata_model != result.metadata:
                raise ModelError(
                    "OUTPUT_INVALID", "Worker GGUF report does not match file metadata"
                )
            gguf_identity = sha256_file(gguf_path)
            model_inventory = {entry.path: entry for entry in model.root.files}
            _copy_files(
                model_path,
                transaction.staging_path,
                [model_inventory[path] for path in _nonweight_snapshot_paths(model)],
            )
            _copy_files(
                transaction.workspace_path,
                transaction.staging_path,
                [FileEntry(path="model.gguf", size=gguf_identity[0], sha256=gguf_identity[1])],
            )
            if (
                sha256_file(gguf_path) != gguf_identity
                or sha256_file(transaction.staging_path / "model.gguf") != gguf_identity
            ):
                raise ModelError("INTEGRITY_FAILED", "GGUF output changed while copying")
            identity = ModelIdentity.model_validate(
                {
                    **model.root.details.model.model_dump(mode="json"),
                    "weightFormat": "gguf",
                }
            )
            metadata = metadata_model.model_dump(mode="json")
            history.append(
                PrecisionChange(
                    operation="convert",
                    fromPrecision=_model_precision(model),
                    toPrecision="F16",
                    lossy=_model_precision(model).upper() != "F16",
                )
            )
            compatibility_target = "llama.cpp"
        _reverify(model_path, model)

        artifact = VerifiedArtifact(model_path, model)
        exposure, inherited = _write_exposure(transaction.staging_path, [artifact])
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "export",
                "name": f"export-{model.root.artifactId[:12]}",
                "createdAt": _timestamp(),
                **_scope_fields(model),
                "parents": [parent_snapshot(model).model_dump(mode="json")],
                "sourceRights": [
                    item.model_dump(mode="json") for item in union_source_rights([model])
                ],
                "producer": producer_identity(
                    "export", [] if format == "checkpoint" else _backend_components()
                ).model_dump(mode="json"),
                "details": {
                    "model": identity.model_dump(mode="json"),
                    "mergedArtifactId": model.root.artifactId,
                    "exportMetadata": metadata,
                    "precisionHistory": [item.model_dump(mode="json") for item in history],
                    "exposureLeakageIndex": exposure.model_dump(mode="json"),
                    "inheritedLeakageIndexes": [item.model_dump(mode="json") for item in inherited],
                    "compatibility": [
                        {
                            "target": compatibility_target,
                            "status": "unverified",
                            "evidence": None,
                        }
                    ],
                },
            },
        )
        transaction.publish(manifest)
        return manifest


def _nonweight_snapshot_paths(manifest: ArtifactManifest) -> list[str]:
    return [
        item.path
        for item in manifest.root.files
        if "/" not in item.path
        and snapshot_file_allowed(item.path)
        and not item.path.endswith(".safetensors")
        and item.path != "model.safetensors.index.json"
    ]


def _inspect_gguf(path: Path) -> GgufExportMetadata:
    if not path.is_file() or path.is_symlink():
        raise ModelError("OUTPUT_INVALID", "GGUF output is not a regular file")
    try:
        reader = GGUFReader(path)
        version = int(reader.fields["GGUF.version"].parts[-1][0])
        metadata_count = int(reader.fields["GGUF.kv_count"].parts[-1][0])
        tensor_count = len(reader.tensors)
    except Exception as error:
        raise ModelError("OUTPUT_INVALID", "GGUF output metadata is invalid") from error
    tensor_names = [tensor.name for tensor in reader.tensors]
    if (
        tensor_count == 0
        or metadata_count == 0
        or len(tensor_names) != len(set(tensor_names))
        or any(
            tensor.tensor_type != GGMLQuantizationType.F16
            or tensor.n_elements <= 0
            or tensor.n_bytes <= 0
            or any(int(dimension) <= 0 for dimension in tensor.shape)
            for tensor in reader.tensors
        )
    ):
        raise ModelError("OUTPUT_INVALID", "GGUF tensors do not match the F16 export contract")
    return GgufExportMetadata(
        format="gguf",
        ggufVersion=version,
        tensorCount=tensor_count,
        metadataKeyCount=metadata_count,
        quantizationType="F16",
    )

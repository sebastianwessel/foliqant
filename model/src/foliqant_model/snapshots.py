"""Inspect ordinary local Safetensors snapshots without loading remote model code."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from safetensors import SafetensorError, safe_open

from .artifacts import (
    ArtifactTransaction,
    build_inventory,
    copy_verified_file,
    create_manifest,
    producer_identity,
    require_disjoint_output,
    sha256_file,
)
from .configuration import read_document
from .contracts import ArtifactManifest, FileEntry, ModelIdentity, canonical_digest
from .contracts.details import ChatTemplateSource, CheckpointDetails
from .errors import ModelError

_TOKENIZER_NAMES = {
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenizer.model",
    "tokenizer.tiktoken",
    "vocab.json",
    "vocab.txt",
    "merges.txt",
    "chat_template.jinja",
    "added_tokens.json",
}
_METADATA_NAMES = {
    "config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "README.md",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "NOTICE",
    "NOTICE.md",
}
MAX_MODEL_METADATA_BYTES = 16 * 1024 * 1024
BOUNDED_MODEL_METADATA_NAMES = frozenset(
    {
        "config.json",
        "generation_config.json",
        "model.safetensors.index.json",
        "tokenizer_config.json",
    }
)


def snapshot_file_allowed(path: str) -> bool:
    """Return whether a root snapshot file is part of the supported text-model format."""
    return (
        path in _TOKENIZER_NAMES
        or path in _METADATA_NAMES
        or re.fullmatch(r"model[A-Za-z0-9._-]*\.safetensors", path) is not None
    )


def validated_snapshot_filenames(filenames: Iterable[str]) -> list[str]:
    """Select supported root files and reject an incomplete remote inventory."""

    names = sorted({name for name in filenames if snapshot_file_allowed(name)})
    if "config.json" not in names or "tokenizer_config.json" not in names:
        raise ModelError("OUTPUT_INVALID", "Snapshot is missing required model metadata")
    tokenizer_assets = {
        "tokenizer.json",
        "tokenizer.model",
        "tokenizer.tiktoken",
        "vocab.json",
        "vocab.txt",
    }
    if not tokenizer_assets.intersection(names):
        raise ModelError("OUTPUT_INVALID", "Snapshot has no supported tokenizer assets")
    weights = [name for name in names if name.endswith(".safetensors")]
    if not weights:
        raise ModelError("OUTPUT_INVALID", "Snapshot has no Safetensors weights")
    if len(weights) > 1 and "model.safetensors.index.json" not in names:
        raise ModelError("OUTPUT_INVALID", "Sharded weights require a complete weight index")
    return names


def _metadata(path: Path) -> dict[str, object]:
    try:
        value = read_document(path, max_bytes=MAX_MODEL_METADATA_BYTES)
    except ModelError as error:
        raise ModelError("OUTPUT_INVALID", "Invalid model metadata") from error
    if not isinstance(value, dict):
        raise ModelError("OUTPUT_INVALID", "Model metadata must be an object")

    def check(item: object) -> None:
        if isinstance(item, dict):
            if item.get("auto_map"):
                raise ModelError("ARCHITECTURE_UNSUPPORTED", "Remote model code is not supported")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)

    check(value)
    return cast(dict[str, object], value)


@dataclass(frozen=True)
class SnapshotInspection:
    """Verified format identity; compatibility still requires real runtime inference."""

    model: ModelIdentity
    weight_precision: str
    tensor_count: int


def _inspect_snapshot_metadata(
    directory: Path,
    inventory: list[FileEntry],
    *,
    quantized: bool,
    expected_weight_files: set[str],
) -> ModelIdentity:
    if any(not snapshot_file_allowed(entry.path) for entry in inventory):
        raise ModelError("OUTPUT_INVALID", "Snapshot contains an unsupported file")
    config = _metadata(directory / "config.json")
    architecture = config.get("model_type")
    if not isinstance(architecture, str) or not architecture:
        raise ModelError("OUTPUT_INVALID", "Snapshot must declare its model_type")
    observed_quantized = "quantization" in config or "quantization_config" in config
    if observed_quantized != quantized:
        if observed_quantized:
            raise ModelError("ARCHITECTURE_UNSUPPORTED", "Dense snapshot weights are required")
        raise ModelError("OUTPUT_INVALID", "Quantized snapshot metadata is missing")
    tokenizer = _metadata(directory / "tokenizer_config.json")
    if (directory / "generation_config.json").exists():
        _metadata(directory / "generation_config.json")
    index = directory / "model.safetensors.index.json"
    if index.exists():
        weight_map = _metadata(index).get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise ModelError("OUTPUT_INVALID", "Weight index has no valid weight map")
        indexed_files: set[str] = set()
        for tensor_name, filename in weight_map.items():
            if (
                not isinstance(tensor_name, str)
                or not tensor_name
                or not isinstance(filename, str)
                or not filename.endswith(".safetensors")
                or not snapshot_file_allowed(filename)
            ):
                raise ModelError("OUTPUT_INVALID", "Weight index has an invalid entry")
            indexed_files.add(filename)
        if indexed_files != expected_weight_files:
            raise ModelError("OUTPUT_INVALID", "Weight index does not match snapshot shards")
    elif len(expected_weight_files) > 1:
        raise ModelError("OUTPUT_INVALID", "Sharded weights require a complete weight index")
    token_entries = [entry for entry in inventory if entry.path in _TOKENIZER_NAMES]
    if not any(
        entry.path
        in {"tokenizer.json", "tokenizer.model", "tokenizer.tiktoken", "vocab.json", "vocab.txt"}
        for entry in token_entries
    ):
        raise ModelError("OUTPUT_INVALID", "Snapshot has no supported tokenizer assets")
    if (directory / "chat_template.jinja").exists():
        try:
            template = (directory / "chat_template.jinja").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ModelError("OUTPUT_INVALID", "Cannot read snapshot chat template") from error
        template_source = ChatTemplateSource(kind="file", value="chat_template.jinja")
    else:
        supplied = tokenizer.get("chat_template")
        if not isinstance(supplied, str) or not supplied:
            raise ModelError("ARCHITECTURE_UNSUPPORTED", "An explicit chat template is required")
        template = supplied
        template_source = ChatTemplateSource(
            kind="tokenizer-config",
            value="tokenizer_config.json#/chat_template",
        )
    if not template:
        raise ModelError("OUTPUT_INVALID", "Snapshot chat template must not be empty")
    return ModelIdentity(
        architecture=architecture,
        weightFormat="safetensors",
        configSha256=sha256_file(directory / "config.json")[1],
        tokenizerSha256=canonical_digest([item.model_dump(mode="json") for item in token_entries]),
        chatTemplateSha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
        tokenizerFiles=[entry.path for entry in token_entries],
        chatTemplateSource=template_source,
    )


def inspect_snapshot_metadata(
    directory: Path,
    *,
    expected_weight_files: Iterable[str],
    quantized: bool = False,
) -> ModelIdentity:
    """Validate model/tokenizer metadata without opening Safetensors weights."""

    return _inspect_snapshot_metadata(
        directory,
        build_inventory(directory),
        quantized=quantized,
        expected_weight_files=set(expected_weight_files),
    )


def inspect_snapshot(directory: Path, *, quantized: bool = False) -> SnapshotInspection:
    """Validate one dense or explicitly expected quantized Safetensors snapshot."""

    inventory = build_inventory(directory)
    weight_files = {entry.path for entry in inventory if entry.path.endswith(".safetensors")}
    model = _inspect_snapshot_metadata(
        directory,
        inventory,
        quantized=quantized,
        expected_weight_files=weight_files,
    )
    tensors: dict[str, str] = {}
    dtypes: set[str] = set()
    for entry in inventory:
        if not entry.path.endswith(".safetensors"):
            continue
        try:
            with safe_open(directory / entry.path, framework="numpy") as handle:
                for name in handle.keys():
                    if name in tensors:
                        raise ModelError("OUTPUT_INVALID", "Duplicate tensor across weight shards")
                    tensor = handle.get_slice(name)
                    if any(dimension <= 0 for dimension in tensor.get_shape()):
                        raise ModelError("OUTPUT_INVALID", "Snapshot has an empty tensor")
                    tensors[name] = entry.path
                    dtypes.add(tensor.get_dtype())
        except (OSError, SafetensorError) as error:
            raise ModelError("OUTPUT_INVALID", "Invalid Safetensors weights") from error
    if not tensors:
        raise ModelError("OUTPUT_INVALID", "Snapshot has no Safetensors tensors")
    index = directory / "model.safetensors.index.json"
    if index.exists():
        if _metadata(index).get("weight_map") != tensors:
            raise ModelError("OUTPUT_INVALID", "Weight index does not match the actual tensors")
    elif len(set(tensors.values())) > 1:
        raise ModelError("OUTPUT_INVALID", "Sharded weights require a complete weight index")
    return SnapshotInspection(model, "+".join(sorted(dtypes)), len(tensors))


def checkpoint_from_snapshot(
    source: Path,
    output: Path,
    *,
    repo: str,
    revision: str,
    license_ref: str,
) -> ArtifactManifest:
    """Copy a verified local upstream snapshot into an immutable fetch artifact."""
    source_inventory = build_inventory(source)
    inspection = inspect_snapshot(source)
    details = CheckpointDetails(
        model=inspection.model,
        upstreamRepo=repo,
        upstreamRevision=revision,
        licenseRef=license_ref,
        weightPrecision=inspection.weight_precision,
        compatibility=[],
    )
    require_disjoint_output(output, [source])
    with ArtifactTransaction(output, "fetch") as transaction:
        for entry in source_inventory:
            target = transaction.staging_path / entry.path
            copy_verified_file(source / entry.path, target, entry)
        if build_inventory(source) != source_inventory:
            raise ModelError("INTEGRITY_FAILED", "Source snapshot changed during acquisition")
        if build_inventory(transaction.staging_path) != source_inventory:
            raise ModelError("INTEGRITY_FAILED", "Snapshot copy does not match source identity")
        manifest = create_manifest(
            transaction.staging_path,
            {
                "schemaVersion": 1,
                "kind": "checkpoint",
                "name": f"checkpoint-{revision[:12]}",
                "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "stage": "upstream",
                "parents": [],
                "sourceRights": [],
                "producer": producer_identity("fetch").model_dump(mode="json"),
                "details": details.model_dump(mode="json"),
            },
        )
        transaction.publish(manifest)
        return manifest

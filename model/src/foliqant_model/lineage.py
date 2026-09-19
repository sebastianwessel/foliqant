"""Reusable lineage, rights, and leakage-index operations."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from .artifacts import write_private_bytes
from .contracts import (
    ArtifactManifest,
    InheritedLeakageRef,
    InventoryFileRef,
    LeakageEntry,
    ParentRef,
    SourceRight,
)
from .contracts.artifacts import ArtifactManifestValue
from .errors import ModelError

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class VerifiedArtifact:
    """A verified manifest paired with the directory whose bytes it inventories."""

    directory: Path
    manifest: ArtifactManifest


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _lineage_failure(message: str) -> ModelError:
    return ModelError("LINEAGE_MISMATCH", message)


def _snapshot_payload(node: ArtifactManifestValue | ParentRef) -> dict[str, object]:
    payload = node.model_dump(mode="json")
    payload.pop("schemaVersion", None)
    return payload


def _read_regular(path: Path, *, label: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ModelError("UNSAFE_ARTIFACT_PATH", f"{label} must be a regular file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ModelError("INTEGRITY_FAILED", f"{label} changed while opening")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise ModelError("INTEGRITY_FAILED", f"{label} changed while reading")
        return b"".join(chunks)
    except ModelError:
        raise
    except OSError as error:
        raise ModelError("IO_FAILED", f"{label} could not be read") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def collect_ancestor_nodes(manifest: ArtifactManifest) -> dict[str, ParentRef]:
    """Return each recursive parent snapshot, rejecting inconsistent repeats."""

    nodes: dict[str, ParentRef] = {}

    def visit(node: ParentRef) -> None:
        previous = nodes.get(node.artifactId)
        if previous is not None:
            if _snapshot_payload(previous) != _snapshot_payload(node):
                raise _lineage_failure("Repeated lineage snapshot is inconsistent")
            return
        nodes[node.artifactId] = node
        for parent in node.parents:
            visit(parent)

    for parent in manifest.root.parents:
        visit(parent)
    return nodes


def has_customer_ancestry(node: ArtifactManifestValue | ParentRef) -> bool:
    """Return whether a node or any recursive parent is customer scoped."""

    return node.stage == "customer" or any(has_customer_ancestry(parent) for parent in node.parents)


def leakage_reference(
    node: ArtifactManifestValue | ParentRef,
) -> InventoryFileRef | None:
    """Return the node's own leakage index reference when it has one."""

    for name in ("leakageIndex", "usedDataLeakageIndex", "exposureLeakageIndex"):
        value = getattr(node.details, name, None)
        if value is not None:
            return cast(InventoryFileRef, value.file)
    return None


def read_leakage_index(path: Path, reference: InventoryFileRef) -> list[LeakageEntry]:
    """Read and validate a canonical leakage JSONL file against its reference."""

    data = _read_regular(path, label="leakage index")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ModelError("INTEGRITY_FAILED", "Leakage index digest does not match its reference")
    lines = data.splitlines(keepends=True)
    if any(not line.endswith(b"\n") for line in lines):
        raise ModelError("INTEGRITY_FAILED", "Leakage index rows must end with LF")
    entries: list[LeakageEntry] = []
    for line in lines:
        raw = line[:-1]
        try:
            value = json.loads(raw.decode("utf-8"))
            entry = LeakageEntry.model_validate(value, strict=True)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise ModelError(
                "INTEGRITY_FAILED", "Leakage index row does not satisfy its contract"
            ) from error
        if raw != _canonical_bytes(entry.model_dump(mode="json")):
            raise ModelError("INTEGRITY_FAILED", "Leakage index row is not canonical JSON")
        entries.append(entry)
    record_ids = [entry.recordId for entry in entries]
    if record_ids != sorted(record_ids) or len(record_ids) != len(set(record_ids)):
        raise ModelError("INTEGRITY_FAILED", "Leakage index rows must be sorted and unique")
    if len(entries) != reference.recordCount:
        raise ModelError("INTEGRITY_FAILED", "Leakage index count does not match its reference")
    return entries


def merge_leakage_entries(
    groups: Iterable[Iterable[LeakageEntry]],
) -> list[LeakageEntry]:
    """Form a sorted conservative exposure union and reject conflicting identities."""

    merged: dict[str, LeakageEntry] = {}
    for group in groups:
        for entry in group:
            previous = merged.get(entry.recordId)
            if previous is not None and previous.model_dump(mode="json") != entry.model_dump(
                mode="json"
            ):
                raise _lineage_failure("Leakage ancestry contains a conflicting record")
            merged[entry.recordId] = entry
    return [merged[record_id] for record_id in sorted(merged)]


def write_leakage_index(path: Path, entries: Sequence[LeakageEntry]) -> InventoryFileRef:
    """Write a sorted canonical leakage index and return its inventory reference."""

    record_ids = [entry.recordId for entry in entries]
    if record_ids != sorted(record_ids) or len(record_ids) != len(set(record_ids)):
        raise ModelError("OUTPUT_INVALID", "Leakage entries must be sorted and unique")
    data = b"".join(_canonical_bytes(entry.model_dump(mode="json")) + b"\n" for entry in entries)
    write_private_bytes(path, data)
    return InventoryFileRef(
        path=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        recordCount=len(entries),
        format="jsonl",
    )


def _indexed_nodes(artifact: VerifiedArtifact) -> dict[str, ArtifactManifestValue | ParentRef]:
    nodes: dict[str, ArtifactManifestValue | ParentRef] = {
        artifact.manifest.root.artifactId: artifact.manifest.root
    }
    nodes.update(collect_ancestor_nodes(artifact.manifest))
    return {artifact_id: node for artifact_id, node in nodes.items() if leakage_reference(node)}


def _source_reference(
    artifact: VerifiedArtifact,
    artifact_id: str,
) -> tuple[Path, InventoryFileRef]:
    if artifact_id == artifact.manifest.root.artifactId:
        reference = leakage_reference(artifact.manifest.root)
        if reference is None:
            raise _lineage_failure("Indexed lineage node has no leakage reference")
        return artifact.directory / reference.path, reference
    inherited = getattr(artifact.manifest.root.details, "inheritedLeakageIndexes", [])
    matches = [item for item in inherited if item.ancestorArtifactId == artifact_id]
    if len(matches) != 1:
        raise _lineage_failure("Artifact does not retain an exact ancestor leakage index")
    return artifact.directory / matches[0].file.path, matches[0].file


def materialize_inherited_leakage(
    destination: Path,
    artifacts: Sequence[VerifiedArtifact],
) -> list[InheritedLeakageRef]:
    """Copy every indexed parent/ancestor byte-for-byte into a child staging tree."""

    nodes: dict[str, ArtifactManifestValue | ParentRef] = {}
    sources: dict[str, tuple[Path, InventoryFileRef]] = {}
    for artifact in artifacts:
        for artifact_id, node in _indexed_nodes(artifact).items():
            previous = nodes.get(artifact_id)
            if previous is not None and _snapshot_payload(previous) != _snapshot_payload(node):
                raise _lineage_failure("Repeated indexed lineage snapshot is inconsistent")
            nodes[artifact_id] = node
            source = _source_reference(artifact, artifact_id)
            existing = sources.get(artifact_id)
            if existing is not None and (
                existing[1].sha256 != source[1].sha256
                or existing[1].recordCount != source[1].recordCount
                or existing[1].format != source[1].format
            ):
                raise _lineage_failure("Repeated ancestor leakage reference is inconsistent")
            sources[artifact_id] = source

    if not nodes:
        return []
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    result: list[InheritedLeakageRef] = []
    for artifact_id in sorted(nodes):
        source_path, source_reference = sources[artifact_id]
        data = _read_regular(source_path, label="ancestor leakage index")
        if hashlib.sha256(data).hexdigest() != source_reference.sha256:
            raise ModelError(
                "INTEGRITY_FAILED", "Ancestor leakage bytes do not match their reference"
            )
        target = destination / f"{artifact_id}.jsonl"
        write_private_bytes(target, data)
        result.append(
            InheritedLeakageRef(
                ancestorArtifactId=artifact_id,
                file=InventoryFileRef(
                    path=f"inherited-leakage/{artifact_id}.jsonl",
                    sha256=source_reference.sha256,
                    recordCount=source_reference.recordCount,
                    format="jsonl",
                ),
            )
        )
    return result


def union_source_rights(manifests: Sequence[ArtifactManifest]) -> list[SourceRight]:
    """Return the sorted exact transitive rights union for a new child artifact."""

    rights: dict[str, SourceRight] = {}
    for manifest in manifests:
        for right in manifest.root.sourceRights:
            previous = rights.get(right.sourceId)
            if previous is not None and previous.model_dump(mode="json") != right.model_dump(
                mode="json"
            ):
                raise _lineage_failure("Lineage contains conflicting source rights")
            rights[right.sourceId] = right
    return [rights[source_id] for source_id in sorted(rights)]

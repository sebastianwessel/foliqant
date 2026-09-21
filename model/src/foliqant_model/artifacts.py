"""Safe artifact IO, integrity verification, and publication lifecycle."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import stat
import subprocess
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from .contracts import (
    ArtifactManifest,
    ConfigIdentity,
    DatasetDetails,
    FileEntry,
    InheritedLeakageRef,
    InventoryFileRef,
    LeakageEntry,
    LockOwner,
    ParentRef,
    ProducerIdentity,
    RunFailure,
    RunState,
    VersionedComponent,
    canonical_digest,
)
from .contracts.artifacts import ArtifactManifestValue
from .contracts.base import CliErrorCode, Command, SafePath
from .dataset_integrity import verify_dataset_contents
from .errors import ModelError

_MANIFEST = "manifest.json"
_CHUNK_SIZE = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_PARENT_ADAPTER: TypeAdapter[ParentRef] = TypeAdapter(ParentRef)
_SAFE_PATH_ADAPTER = TypeAdapter(SafePath)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _integrity(message: str, *, code: CliErrorCode = "INTEGRITY_FAILED") -> ModelError:
    return ModelError(code, message)


def _strict_json(data: bytes, *, label: str) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"nonfinite number {value}")

    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _integrity(f"{label} is not strict UTF-8 JSON") from error


def sha256_file(path: Path) -> tuple[int, str]:
    """Hash one regular file without following a final-component symlink."""

    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise _integrity(
                "artifact inventory entry is not a regular file", code="UNSAFE_ARTIFACT_PATH"
            )
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise _integrity("artifact file changed during inspection")
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(descriptor, _CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(descriptor)
            if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise _integrity("artifact file changed during hashing")
            return size, digest.hexdigest()
        finally:
            os.close(descriptor)
    except ModelError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _integrity(
                "artifact inventory contains a symlink", code="UNSAFE_ARTIFACT_PATH"
            ) from error
        raise ModelError("IO_FAILED", "artifact file could not be read") from error


def require_disjoint_output(output: Path, inputs: Iterable[Path]) -> None:
    """Reject an output path that contains, equals, or is contained by an input path."""

    try:
        resolved_output = output.resolve(strict=False)
        resolved_inputs = [path.resolve(strict=True) for path in inputs]
    except OSError as error:
        raise ModelError("INPUT_NOT_FOUND", "Artifact input path could not be resolved") from error
    for resolved_input in resolved_inputs:
        if (
            resolved_output == resolved_input
            or resolved_output.is_relative_to(resolved_input)
            or resolved_input.is_relative_to(resolved_output)
        ):
            raise ModelError("ARGUMENT_INVALID", "Artifact output must not overlap an input path")


def copy_verified_file(source: Path, target: Path, expected: FileEntry) -> None:
    """Copy one expected regular file through descriptors without following final links."""

    source_descriptor: int | None = None
    parent_descriptor: int | None = None
    target_descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    completed = False
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise _integrity("copy source is not a regular file", code="UNSAFE_ARTIFACT_PATH")
        source_descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        opened = os.fstat(source_descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise _integrity("copy source changed during inspection")
        if opened.st_size != expected.size:
            raise _integrity("copy source size does not match its inventory")

        parent_descriptor = os.open(
            target.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW,
        )
        parent = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent.st_mode):
            raise _integrity(
                "copy target parent is not a real directory", code="UNSAFE_ARTIFACT_PATH"
            )
        target_descriptor = os.open(
            target.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        created = os.fstat(target_descriptor)
        if not stat.S_ISREG(created.st_mode):
            raise _integrity("copy target is not a regular file", code="UNSAFE_ARTIFACT_PATH")
        created_identity = (created.st_dev, created.st_ino)
        os.fchmod(target_descriptor, 0o600)

        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(source_descriptor, min(_CHUNK_SIZE, expected.size - size + 1)):
            if size + len(chunk) > expected.size:
                raise _integrity("copy source exceeds its inventoried size")
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_descriptor, view)
                view = view[written:]
        after = os.fstat(source_descriptor)
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino) or (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise _integrity("copy source changed while it was read")
        if size != expected.size or digest.hexdigest() != expected.sha256:
            raise _integrity("copy source does not match its inventory")
        os.fsync(target_descriptor)
        copied = os.fstat(target_descriptor)
        if not stat.S_ISREG(copied.st_mode) or copied.st_size != expected.size:
            raise _integrity("copied file does not match its inventory")
        completed = True
    except ModelError:
        raise
    except FileExistsError as error:
        raise ModelError("OUTPUT_EXISTS", "Copy target already exists") from error
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _integrity(
                "copy path contains a link or non-directory", code="UNSAFE_ARTIFACT_PATH"
            ) from error
        raise ModelError("IO_FAILED", "Verified file could not be copied") from error
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        if not completed and parent_descriptor is not None and created_identity is not None:
            try:
                current = os.stat(target.name, dir_fd=parent_descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == created_identity:
                    os.unlink(target.name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)


def _validate_root(directory: Path) -> None:
    try:
        info = directory.lstat()
    except OSError as error:
        raise ModelError("INPUT_NOT_FOUND", "artifact directory does not exist") from error
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
        raise _integrity("artifact path is not a real directory", code="UNSAFE_ARTIFACT_PATH")


def build_inventory(directory: Path, *, exclude: Collection[str] = (_MANIFEST,)) -> list[FileEntry]:
    """Build a sorted complete inventory while rejecting links and special files."""

    _validate_root(directory)
    excluded = set(exclude)
    entries: list[FileEntry] = []

    def visit(current: Path, parts: tuple[str, ...]) -> None:
        try:
            children = sorted(os.scandir(current), key=lambda child: child.name)
        except OSError as error:
            raise ModelError("IO_FAILED", "artifact directory could not be inspected") from error
        for child in children:
            relative = "/".join((*parts, child.name))
            try:
                safe_relative = _SAFE_PATH_ADAPTER.validate_python(relative)
            except ValidationError as error:
                raise _integrity(
                    "artifact contains an unsafe path", code="UNSAFE_ARTIFACT_PATH"
                ) from error
            try:
                if child.is_symlink():
                    raise _integrity("artifact contains a symlink", code="UNSAFE_ARTIFACT_PATH")
                if child.is_dir(follow_symlinks=False):
                    before_count = len(entries)
                    visit(Path(child.path), (*parts, child.name))
                    if len(entries) == before_count:
                        raise _integrity("artifact contains an unlisted empty directory")
                elif child.is_file(follow_symlinks=False):
                    if safe_relative not in excluded:
                        size, digest = sha256_file(Path(child.path))
                        entries.append(FileEntry(path=safe_relative, size=size, sha256=digest))
                else:
                    raise _integrity(
                        "artifact contains a nonregular entry", code="UNSAFE_ARTIFACT_PATH"
                    )
            except OSError as error:
                raise ModelError("IO_FAILED", "artifact entry could not be inspected") from error

    visit(directory, ())
    return entries


def _private_mode(path: Path, mode: int) -> None:
    if os.name == "posix":
        try:
            os.chmod(path, mode, follow_symlinks=False)
        except OSError as error:
            raise ModelError(
                "IO_FAILED", "private artifact permissions could not be set"
            ) from error


def _make_tree_private(directory: Path) -> None:
    _validate_root(directory)
    _private_mode(directory, 0o700)
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise _integrity("artifact contains a symlink", code="UNSAFE_ARTIFACT_PATH")
            _private_mode(path, 0o700)
        for name in files:
            path = current_path / name
            if path.is_symlink():
                raise _integrity("artifact contains a symlink", code="UNSAFE_ARTIFACT_PATH")
            _private_mode(path, 0o600)


def write_private_bytes(path: Path, data: bytes) -> None:
    """Atomically write private bytes through an exclusive sibling temporary file."""

    parent = path.parent
    temporary = parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        _private_mode(path, 0o600)
    except OSError as error:
        raise ModelError("IO_FAILED", "private file could not be written") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def write_private_json(path: Path, value: object) -> None:
    """Write canonical JSON plus one trailing line feed."""

    try:
        data = _canonical_bytes(value) + b"\n"
    except (TypeError, ValueError) as error:
        raise ModelError(
            "OUTPUT_INVALID", "value cannot be serialized as canonical JSON"
        ) from error
    write_private_bytes(path, data)


def create_manifest(directory: Path, fields: Mapping[str, object]) -> ArtifactManifest:
    """Inventory an artifact, derive its identity, validate it, and write its manifest."""

    manifest_path = directory / _MANIFEST
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ModelError("OUTPUT_EXISTS", "artifact manifest already exists")
    _make_tree_private(directory)
    if _MANIFEST in fields or "artifactId" in fields or "files" in fields:
        raise ModelError("OUTPUT_INVALID", "manifest-owned fields must not be supplied")
    payload = dict(fields)
    payload["files"] = [item.model_dump(mode="json") for item in build_inventory(directory)]
    payload["artifactId"] = canonical_digest(payload)
    try:
        manifest = ArtifactManifest.model_validate(payload)
    except ValidationError as error:
        raise ModelError(
            "OUTPUT_INVALID", "artifact manifest does not satisfy its contract"
        ) from error
    write_private_json(manifest_path, manifest.root.model_dump(mode="json"))
    verified = load_verified_artifact(directory)
    if verified.root.artifactId != manifest.root.artifactId:
        raise _integrity("finalized artifact identity changed during verification")
    return verified


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise _integrity(f"{label} is not a regular file", code="UNSAFE_ARTIFACT_PATH")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise _integrity(f"{label} changed during inspection")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, _CHUNK_SIZE):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise _integrity(f"{label} changed during inspection")
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ModelError("IO_FAILED", f"{label} could not be read") from error
    return data


def _walk_models(value: object) -> Iterable[BaseModel]:
    if isinstance(value, BaseModel):
        yield value
        for field in type(value).model_fields:
            yield from _walk_models(getattr(value, field))
    elif isinstance(value, list):
        for item in value:
            yield from _walk_models(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_models(item)


def _verify_node_inventory_references(node: ArtifactManifestValue | ParentRef) -> None:
    inventory = {entry.path: entry for entry in node.files}
    for value in _walk_models(node.details):
        if not isinstance(value, InventoryFileRef):
            continue
        entry = inventory.get(value.path)
        if entry is None or entry.sha256 != value.sha256:
            raise _integrity("manifest inventory reference does not match the artifact inventory")


def _verify_inventory_references(manifest: ArtifactManifest) -> None:
    _verify_node_inventory_references(manifest.root)
    for node in _ancestor_nodes(manifest).values():
        _verify_node_inventory_references(node)


def _parse_leakage(directory: Path, reference: InventoryFileRef) -> list[LeakageEntry]:
    data = _read_regular(directory / reference.path, label="leakage index")
    lines = data.splitlines(keepends=True)
    if any(not line.endswith(b"\n") for line in lines):
        raise _integrity("leakage index lines must end with LF")
    result: list[LeakageEntry] = []
    for line in lines:
        raw = line[:-1]
        value = _strict_json(raw, label="leakage index row")
        try:
            entry = LeakageEntry.model_validate(value)
        except ValidationError as error:
            raise _integrity("leakage index row does not satisfy its contract") from error
        if raw != _canonical_bytes(entry.model_dump(mode="json")):
            raise _integrity("leakage index row is not canonical JSON")
        result.append(entry)
    ids = [entry.recordId for entry in result]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise _integrity("leakage index rows must be sorted and unique")
    if len(result) != reference.recordCount:
        raise _integrity("leakage index record count does not match its reference")
    return result


def _node_leakage_reference(node: ArtifactManifestValue | ParentRef) -> InventoryFileRef | None:
    details = node.details
    for name in ("leakageIndex", "usedDataLeakageIndex", "exposureLeakageIndex"):
        value = getattr(details, name, None)
        if value is not None:
            return cast(InventoryFileRef, value.file)
    return None


def _ancestor_nodes(manifest: ArtifactManifest) -> dict[str, ParentRef]:
    nodes: dict[str, ParentRef] = {}

    def visit(node: ParentRef) -> None:
        existing = nodes.get(node.artifactId)
        if existing is not None:
            if existing.model_dump(mode="json") != node.model_dump(mode="json"):
                raise _integrity(
                    "repeated lineage snapshot is inconsistent", code="LINEAGE_MISMATCH"
                )
            return
        nodes[node.artifactId] = node
        for parent in node.parents:
            visit(parent)

    for parent in manifest.root.parents:
        visit(parent)
    return nodes


def _merge_leakage(groups: Iterable[Iterable[LeakageEntry]]) -> list[LeakageEntry]:
    merged: dict[str, LeakageEntry] = {}
    for group in groups:
        for entry in group:
            previous = merged.get(entry.recordId)
            if previous is not None and previous.model_dump(mode="json") != entry.model_dump(
                mode="json"
            ):
                raise _integrity(
                    "leakage ancestry contains a conflicting record", code="LINEAGE_MISMATCH"
                )
            merged[entry.recordId] = entry
    return [merged[key] for key in sorted(merged)]


def _verify_inherited_leakage(directory: Path, manifest: ArtifactManifest) -> None:
    inherited = getattr(manifest.root.details, "inheritedLeakageIndexes", None)
    current_ref = _node_leakage_reference(manifest.root)
    if inherited is None:
        if current_ref is not None:
            _parse_leakage(directory, current_ref)
        return

    ancestors = _ancestor_nodes(manifest)
    expected = {
        artifact_id: reference
        for artifact_id, node in ancestors.items()
        if (reference := _node_leakage_reference(node)) is not None
    }
    actual = {
        item.ancestorArtifactId: item.file for item in cast(list[InheritedLeakageRef], inherited)
    }
    if set(actual) != set(expected):
        raise _integrity(
            "inherited leakage indexes do not cover exact indexed ancestry", code="LINEAGE_MISMATCH"
        )

    copied: dict[str, list[LeakageEntry]] = {}
    for artifact_id, ancestor_ref in expected.items():
        copied_ref = actual[artifact_id]
        if (
            copied_ref.sha256 != ancestor_ref.sha256
            or copied_ref.recordCount != ancestor_ref.recordCount
        ):
            raise _integrity(
                "inherited leakage copy does not match its ancestor", code="LINEAGE_MISMATCH"
            )
        copied[artifact_id] = _parse_leakage(directory, copied_ref)

    if current_ref is None:
        return
    current = _parse_leakage(directory, current_ref)
    parents = manifest.root.parents
    kind = manifest.root.kind
    source_groups: list[list[LeakageEntry]] = []
    if kind in {"quantized", "export"}:
        parent_ref = _node_leakage_reference(parents[0])
        if parent_ref is not None:
            source_groups.append(copied[parents[0].artifactId])
    elif kind == "merged":
        for parent in parents:
            if _node_leakage_reference(parent) is not None:
                source_groups.append(copied[parent.artifactId])
    elif kind == "adapter":
        model_parent, dataset_parent = parents[:2]
        if _node_leakage_reference(model_parent) is not None:
            source_groups.append(copied[model_parent.artifactId])
        dataset_rows = copied[dataset_parent.artifactId]
        assignments = cast(DatasetDetails, dataset_parent.details).assignments
        source_groups.append(
            [
                row
                for row in dataset_rows
                if assignments[row.recordId].split in {"train", "validation"}
            ]
        )
        if len(parents) == 3:
            source_groups.append(copied[parents[2].artifactId])
    expected_current = _merge_leakage(source_groups)
    if [row.model_dump(mode="json") for row in current] != [
        row.model_dump(mode="json") for row in expected_current
    ]:
        raise _integrity(
            "artifact exposure leakage index does not match its retained ancestry",
            code="LINEAGE_MISMATCH",
        )


def _verify_snapshot_model_identity(node: ArtifactManifestValue | ParentRef) -> None:
    model = getattr(node.details, "model", None)
    if model is None:
        return
    inventory = {entry.path: entry for entry in node.files}
    config = inventory.get("config.json")
    if config is None or config.sha256 != model.configSha256:
        raise _integrity("model config identity does not match config.json")
    try:
        tokenizer_entries = [inventory[path] for path in model.tokenizerFiles]
    except KeyError as error:
        raise _integrity("model tokenizer identity references an absent file") from error
    tokenizer_payload = [entry.model_dump(mode="json") for entry in tokenizer_entries]
    if canonical_digest(tokenizer_payload) != model.tokenizerSha256:
        raise _integrity("model tokenizer identity does not match its inventory")


def _verify_model_identity(directory: Path, manifest: ArtifactManifest) -> None:
    _verify_snapshot_model_identity(manifest.root)
    for node in _ancestor_nodes(manifest).values():
        _verify_snapshot_model_identity(node)
    model = getattr(manifest.root.details, "model", None)
    if model is None:
        return
    source = model.chatTemplateSource
    template: bytes | None = None
    if source.kind == "file":
        template = _read_regular(directory / source.value, label="chat template")
    elif source.kind == "tokenizer-config":
        parsed = _strict_json(
            _read_regular(directory / "tokenizer_config.json", label="tokenizer config"),
            label="tokenizer config",
        )
        if (
            not isinstance(parsed, dict)
            or not isinstance(parsed.get("chat_template"), str)
            or not parsed["chat_template"]
        ):
            raise _integrity("tokenizer config has no usable chat template")
        template = cast(str, parsed["chat_template"]).encode("utf-8")
    else:
        raise _integrity("registered default chat templates are not available for verification")
    if template is not None and hashlib.sha256(template).hexdigest() != model.chatTemplateSha256:
        raise _integrity("model chat template identity does not match its bytes")


def load_verified_artifact(directory: Path) -> ArtifactManifest:
    """Load and comprehensively verify a standalone immutable artifact."""

    _validate_root(directory)
    manifest_path = directory / _MANIFEST
    value = _strict_json(
        _read_regular(manifest_path, label="artifact manifest"), label="artifact manifest"
    )
    try:
        manifest = ArtifactManifest.model_validate(value)
    except ValidationError as error:
        raise _integrity("artifact manifest does not satisfy its contract") from error
    actual = build_inventory(directory)
    if [entry.model_dump(mode="json") for entry in actual] != [
        entry.model_dump(mode="json") for entry in manifest.root.files
    ]:
        raise _integrity("artifact inventory does not match its files")
    _verify_inventory_references(manifest)
    if manifest.root.kind == "dataset":
        details = manifest.root.details
        splits = ("train", "validation", "calibration", "test")
        paths = [
            *(getattr(details.recordFiles, split).path for split in splits),
            *(getattr(details.chatFiles, split).path for split in splits),
            details.leakageIndex.file.path,
        ]
        retained = {
            path: _read_regular(directory / path, label="retained dataset content")
            for path in paths
        }
        verify_dataset_contents(details, manifest.root.sourceRights, retained)
        from .native_data import verify_upgrade_provenance

        verify_upgrade_provenance(directory, manifest)
    _verify_model_identity(directory, manifest)
    _verify_inherited_leakage(directory, manifest)
    for node in [manifest.root, *_ancestor_nodes(manifest).values()]:
        if node.kind == "adapter":
            components = {(item.role, item.name, item.version) for item in node.producer.components}
            if ("library", "foliqant-completion-loss", "1") not in components:
                raise _integrity("adapter producer is missing the completion-loss identity")
    return manifest


def parent_snapshot(manifest: ArtifactManifest) -> ParentRef:
    """Return the exact recursive snapshot embedded in a child manifest."""

    value = manifest.root.model_dump(mode="json")
    value.pop("schemaVersion")
    return _PARENT_ADAPTER.validate_python(value)


def producer_identity(
    command: Command, components: Iterable[VersionedComponent] = ()
) -> ProducerIdentity:
    """Capture package, interpreter, platform, and honest Git producer identity."""

    try:
        version = importlib.metadata.version("foliqant-model")
    except importlib.metadata.PackageNotFoundError:
        from . import __version__

        version = __version__
    revision: str | None = None
    dirty: bool | None = None
    candidates: list[Path] = []
    discovered = shutil.which("git")
    if discovered is not None:
        candidates.append(Path(discovered))
    repository = Path(__file__).resolve().parents[3]
    for executable in candidates:
        if not executable.is_file():
            continue
        try:
            head = subprocess.run(
                [str(executable), "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            status_output = subprocess.run(
                [str(executable), "-C", str(repository), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            if len(head) == 40 and all(character in "0123456789abcdef" for character in head):
                revision = head
                dirty = bool(status_output)
                break
        except (OSError, subprocess.SubprocessError):
            continue
    payload: dict[str, object] = {
        "name": "foliqant-model",
        "version": version,
        "command": command,
        "pythonVersion": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine() or "unknown",
        "codeRevision": revision,
        "codeDirty": dirty,
        "components": [
            item.model_dump(mode="json")
            for item in sorted(components, key=lambda item: (item.role, item.name))
        ],
    }
    if revision is None:
        payload["codeIdentityUnavailableReason"] = "Git identity unavailable"
    return ProducerIdentity.model_validate(payload)


def _timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    system = platform.system()
    library = ctypes.CDLL(None, use_errno=True)
    if system == "Darwin":
        function = library.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(source_bytes, destination_bytes, 0x00000004)  # RENAME_EXCL
    elif system == "Linux" and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(-100, source_bytes, -100, destination_bytes, 1)  # RENAME_NOREPLACE
    elif system == "Windows":
        try:
            os.rename(source, destination)
            return
        except FileExistsError as error:
            raise ModelError("OUTPUT_EXISTS", "artifact output already exists") from error
        except OSError as error:
            raise ModelError("IO_FAILED", "artifact output could not be published") from error
    else:
        raise ModelError("ENVIRONMENT_UNSUPPORTED", "atomic no-replace publication is unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ModelError("OUTPUT_EXISTS", "artifact output already exists")
    raise ModelError("IO_FAILED", "artifact output could not be published") from OSError(
        error_number, os.strerror(error_number)
    )


def publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a staged directory, failing if the destination exists."""

    _validate_root(source)
    _rename_directory_noreplace(source, destination)


class ArtifactTransaction:
    """Own one exclusive artifact lock, private run workspace, and staging tree."""

    output: Path
    command: Command
    run_id: str
    lock_path: Path
    workspace_path: Path
    staging_path: Path

    def __init__(
        self,
        output: Path,
        command: Command,
        *,
        configuration: ConfigIdentity | None = None,
        parent_artifact_ids: Sequence[str] = (),
        dataset_artifact_id: str | None = None,
    ) -> None:
        self.output = output
        self.command = command
        self.run_id = uuid.uuid4().hex
        self.lock_path = output.parent / f".{output.name}.lock"
        self.workspace_path = output.parent / f".{output.name}.run-{self.run_id}"
        self.staging_path = output.parent / f".{output.name}.staging-{self.run_id}"
        self._configuration = configuration
        self._parent_ids = list(parent_artifact_ids)
        self._dataset_id = dataset_artifact_id
        self._started_at = _timestamp()
        self._terminal = False
        self._published = False
        self._lock_owned = False
        self._reserve()

    def _reserve(self) -> None:
        if self.output.exists() or self.output.is_symlink():
            raise ModelError("OUTPUT_EXISTS", "artifact output already exists")
        try:
            descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise ModelError("OUTPUT_EXISTS", "artifact output is locked by another run") from error
        except OSError as error:
            raise ModelError("IO_FAILED", "artifact lock could not be created") from error
        self._lock_owned = True
        try:
            owner = LockOwner(
                runId=self.run_id, pid=os.getpid(), workspacePath=str(self.workspace_path)
            )
            data = _canonical_bytes(owner.model_dump(mode="json")) + b"\n"
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        except Exception:
            self._remove_owned_lock()
            raise
        finally:
            os.close(descriptor)
        try:
            self.workspace_path.mkdir(mode=0o700)
            self.staging_path.mkdir(mode=0o700)
            write_private_bytes(self.workspace_path / "backend.log", b"")
            self._write_state("running")
        except Exception:
            self._remove_owned_lock()
            raise

    def _write_state(
        self,
        status: str,
        *,
        failure: RunFailure | None = None,
        artifact_id: str | None = None,
    ) -> None:
        state = RunState.model_validate(
            {
                "schemaVersion": 1,
                "runId": self.run_id,
                "command": self.command,
                "startedAt": self._started_at,
                "updatedAt": _timestamp(),
                "status": status,
                "pid": os.getpid(),
                "configuration": None
                if self._configuration is None
                else self._configuration.model_dump(mode="json"),
                "parentArtifactIds": self._parent_ids,
                "datasetArtifactId": self._dataset_id,
                "failure": None if failure is None else failure.model_dump(mode="json"),
                "logPath": "backend.log",
                "finalizedArtifactId": artifact_id,
            }
        )
        write_private_json(self.workspace_path / "run.json", state.model_dump(mode="json"))

    def publish(self, manifest: ArtifactManifest) -> Path:
        """Verify staging, publish it atomically without replacement, and complete the run."""

        if self._terminal:
            raise ModelError("INTERNAL_ERROR", "artifact transaction is already terminal")
        verified = load_verified_artifact(self.staging_path)
        if verified.root.artifactId != manifest.root.artifactId:
            raise _integrity("staging artifact does not match the requested manifest")
        publish_directory_no_replace(self.staging_path, self.output)
        self._published = True
        self._write_state("completed", artifact_id=verified.root.artifactId)
        self._terminal = True
        self._remove_owned_lock()
        return self.output

    def fail(self, error: ModelError) -> None:
        if self._terminal:
            return
        self._write_state("failed", failure=RunFailure(code=error.code, message=error.message))
        self._terminal = True
        self._remove_owned_lock()

    def interrupt(self, message: str = "operation interrupted") -> None:
        if self._terminal:
            return
        self._write_state("interrupted", failure=RunFailure(code="INTERRUPTED", message=message))
        self._terminal = True
        self._remove_owned_lock()

    def _remove_owned_lock(self) -> None:
        if not self._lock_owned:
            return
        try:
            before = self.lock_path.lstat()
            value = _strict_json(
                _read_regular(self.lock_path, label="artifact lock"), label="artifact lock"
            )
            owner = LockOwner.model_validate(value)
            after = self.lock_path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                return
            if (
                owner.runId != self.run_id
                or owner.pid != os.getpid()
                or owner.workspacePath != str(self.workspace_path)
            ):
                return
            self.lock_path.unlink()
            self._lock_owned = False
        except FileNotFoundError:
            self._lock_owned = False
        except (ModelError, OSError, ValidationError):
            return

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: object,
        exception: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        if self._terminal:
            return False
        if isinstance(exception, KeyboardInterrupt):
            self.interrupt()
        elif isinstance(exception, ModelError):
            self.fail(exception)
        elif exception is not None:
            self.fail(ModelError("INTERNAL_ERROR", "unexpected lifecycle failure"))
        else:
            self.fail(
                ModelError("INTERNAL_ERROR", "artifact transaction exited without publication")
            )
        return False

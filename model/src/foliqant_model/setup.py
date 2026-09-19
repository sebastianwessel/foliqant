"""Prepare the pinned local model exercise without training or uploading data."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import suppress
from importlib.resources import files
from pathlib import Path

from .acquisition import download_asset
from .artifacts import load_verified_artifact, sha256_file, write_private_json
from .bootstrap_data import banking77_records
from .configuration import load_config
from .contracts import ArtifactManifest, DatasetConfig, FileEntry, LockOwner, canonical_digest
from .contracts.setup import SetupProfile, SetupReceipt, SetupResult
from .errors import ModelError
from .snapshots import checkpoint_from_snapshot


def _bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def builtin_profile() -> SetupProfile:
    """Load the packaged acquisition manifest, never model or dataset contents."""
    return SetupProfile.model_validate_json(
        files("foliqant_model.profiles").joinpath("smoke-v1.json").read_bytes()
    )


def check_workspace_git_policy(workspace: Path) -> None:
    """Refuse a data workspace in a worktree unless the whole directory is ignored."""
    root = next(
        (parent for parent in (workspace, *workspace.parents) if (parent / ".git").exists()), None
    )
    if root is None:
        return
    executable = shutil.which("git")
    if executable is None:
        raise ModelError("ENVIRONMENT_UNSUPPORTED", "Git is needed to check workspace exclusions")
    relative = workspace.relative_to(root).as_posix()
    if relative == ".":
        raise ModelError("ARGUMENT_INVALID", "A Git worktree root cannot be a data workspace")
    try:
        ignored = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                relative + "/",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if ignored.returncode == 1:
            raise ModelError("ARGUMENT_INVALID", "The data workspace must be ignored by Git")
        if ignored.returncode != 0:
            raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot verify the Git workspace exclusion")
        tracked = subprocess.run(
            [executable, "-C", str(root), "ls-files", "-z", "--", relative + "/"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if tracked.returncode != 0:
            raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot inspect tracked workspace files")
        if tracked.stdout:
            raise ModelError("ARGUMENT_INVALID", "The data workspace contains tracked files")
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot verify Git workspace safety") from error


def _directory(path: Path) -> None:
    if path.is_symlink():
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Setup directories must not be symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ModelError("IO_FAILED", "Setup path must be a directory")


def _write_or_verify(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        import hashlib

        if sha256_file(path) != (len(content), hashlib.sha256(content).hexdigest()):
            raise ModelError("INTEGRITY_FAILED", "An existing setup output was modified")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _file_entry(root: Path, path: Path) -> FileEntry:
    size, digest = sha256_file(path)
    return FileEntry(path=path.relative_to(root).as_posix(), size=size, sha256=digest)


def _dataset_recipe(profile: SetupProfile, pool: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "name": f"smoke-{pool}",
        "sources": [
            {
                "id": "banking77",
                "path": f"../records/{pool}.jsonl",
                "license": profile.datasetLicense,
                "licenseEvidence": (
                    f"{profile.datasetRepo}/blob/{profile.datasetRevision}/LICENSE"
                ),
                "trainingAllowed": True,
                "sharedTrainingAllowed": True,
                "redistributionAllowed": True,
                "privacy": "public",
                "commercialUse": "allowed",
                "restrictions": ["Retain attribution and indicate modifications."],
                "attribution": (
                    "Casanueva et al., Efficient Intent Detection with Dual Sentence "
                    "Encoders; PolyAI-LDN/task-specific-datasets; " + profile.datasetRevision
                ),
            }
        ],
    }


def _verify_dataset_inputs(manifest: ArtifactManifest, config_path: Path, source: Path) -> None:
    if manifest.root.kind != "dataset":
        raise ModelError("INTEGRITY_FAILED", "Setup expected a dataset artifact")
    expected = load_config(config_path, DatasetConfig).model_dump(mode="json")
    for declaration in expected["sources"]:
        declaration.pop("path")
    if manifest.root.details.datasetConfig.model_dump(mode="json") != expected:
        raise ModelError("INTEGRITY_FAILED", "Setup dataset recipe does not match its artifact")
    size, digest = sha256_file(source)
    summaries = manifest.root.details.sourceFiles
    if (
        len(summaries) != 1
        or summaries[0].sourceId != "banking77"
        or summaries[0].size != size
        or summaries[0].sha256 != digest
        or summaries[0].recordCount != 154
    ):
        raise ModelError("INTEGRITY_FAILED", "Setup dataset source does not match its artifact")


def run_setup(
    workspace: Path | None = None,
    *,
    offline: bool = False,
    timeout_seconds: int = 3600,
) -> SetupResult:
    """Download, verify and prepare the complete small local exercise, idempotently."""
    if not 1 <= timeout_seconds <= 604800:
        raise ModelError("ARGUMENT_INVALID", "Setup timeout is out of range")
    started = time.monotonic()
    workspace = (workspace or Path.home() / ".local/share/foliqant").expanduser().absolute()
    check_workspace_git_policy(workspace)
    profile = builtin_profile()
    profile_id = canonical_digest(profile.model_dump(mode="json"))
    root = workspace / "setups" / f"{profile.name}-{profile_id[:12]}"
    for directory in (workspace, workspace / "setups", root):
        _directory(directory)
    lock_path = root.parent / f".{root.name}.lock"
    owner = LockOwner(runId=uuid.uuid4().hex, pid=os.getpid(), workspacePath=str(root))
    lock_bytes = _bytes(owner.model_dump(mode="json"))
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ModelError("OUTPUT_EXISTS", "Setup is locked; inspect the existing owner") from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(lock_bytes)
    try:
        for name in ("downloads", "records", "config", "artifacts"):
            _directory(root / name)
        receipt_path = root / "setup.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            if receipt_path.is_symlink():
                raise ModelError("UNSAFE_ARTIFACT_PATH", "Setup receipt must not be a symlink")
            previous = load_config(receipt_path, SetupReceipt)
            if previous.profileSha256 != profile_id or previous.profileName != profile.name:
                raise ModelError("INTEGRITY_FAILED", "Setup receipt belongs to a different profile")
            for entry in previous.assets:
                if sha256_file(root / "downloads" / entry.path) != (entry.size, entry.sha256):
                    raise ModelError("INTEGRITY_FAILED", "A completed setup asset was modified")
            for entry in previous.generatedFiles:
                if sha256_file(root / entry.path) != (entry.size, entry.sha256):
                    raise ModelError("INTEGRITY_FAILED", "A completed setup output was modified")
        downloaded = reused = 0
        for asset in profile.assets:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise ModelError("TIMEOUT", "Setup exceeded its deadline")
            state = download_asset(
                asset, root / "downloads" / asset.path, offline=offline, timeout=remaining
            )
            downloaded += state == "downloaded"
            reused += state == "reused"
        shared, customer = banking77_records(root / "downloads/banking77")
        generated: list[Path] = []
        for pool, records in (("shared", shared), ("customer", customer)):
            path = root / "records" / f"{pool}.jsonl"
            _write_or_verify(
                path, b"".join(_bytes(record.model_dump(mode="json")) for record in records)
            )
            generated.append(path)
            config_path = root / "config" / f"{pool}.json"
            _write_or_verify(config_path, _bytes(_dataset_recipe(profile, pool)))
            generated.append(config_path)
        train_path, eval_path = root / "config/train.json", root / "config/evaluation.json"
        _write_or_verify(
            train_path,
            _bytes(
                {
                    "schemaVersion": 1,
                    "name": "smoke-shared",
                    "steps": 2,
                    "batchSize": 1,
                    "gradientAccumulation": 1,
                    "maxSequenceLength": 2048,
                    "numLayers": 2,
                    "rank": 4,
                    "validationEvery": 1,
                    "validationBatches": 1,
                    "saveEvery": 1,
                }
            ),
        )
        _write_or_verify(eval_path, _bytes({"schemaVersion": 1, "maxTokens": 32, "maxExamples": 4}))
        generated.extend((train_path, eval_path))
        model_path = root / "artifacts/upstream"
        if not model_path.exists() and not model_path.is_symlink():
            checkpoint_from_snapshot(
                root / "downloads/model",
                model_path,
                repo=profile.modelRepo,
                revision=profile.modelRevision,
                license_ref=profile.modelLicense,
            )
        model = load_verified_artifact(model_path)
        expected_model_files = [
            FileEntry(path=asset.path.removeprefix("model/"), size=asset.size, sha256=asset.sha256)
            for asset in profile.assets
            if asset.path.startswith("model/")
        ]
        if (
            model.root.kind != "checkpoint"
            or model.root.files != expected_model_files
            or model.root.details.upstreamRepo != profile.modelRepo
            or model.root.details.upstreamRevision != profile.modelRevision
            or model.root.details.licenseRef != profile.modelLicense
        ):
            raise ModelError("INTEGRITY_FAILED", "Setup model differs from pinned profile")
        # Lazy import keeps acquisition/conversion available without command orchestration.
        from .data import prepare_dataset

        prepared: dict[str, ArtifactManifest] = {}
        for pool in ("shared", "customer"):
            dataset_path = root / "artifacts" / pool
            config_path = root / "config" / f"{pool}.json"
            if not dataset_path.exists() and not dataset_path.is_symlink():
                prepare_dataset(config_path, dataset_path)
            dataset = load_verified_artifact(dataset_path)
            _verify_dataset_inputs(dataset, config_path, root / "records" / f"{pool}.jsonl")
            prepared[pool] = dataset
        receipt = SetupReceipt(
            schemaVersion=1,
            profileName=profile.name,
            profileSha256=profile_id,
            assets=[
                _file_entry(root / "downloads", root / "downloads" / asset.path)
                for asset in profile.assets
            ],
            generatedFiles=[_file_entry(root, path) for path in sorted(generated)],
            modelArtifactId=model.root.artifactId,
            sharedDatasetArtifactId=prepared["shared"].root.artifactId,
            customerDatasetArtifactId=prepared["customer"].root.artifactId,
        )
        if time.monotonic() - started > timeout_seconds:
            raise ModelError("TIMEOUT", "Setup exceeded its deadline")
        receipt_path = root / "setup.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            _write_or_verify(receipt_path, _bytes(receipt.model_dump(mode="json")))
        else:
            write_private_json(receipt_path, receipt.model_dump(mode="json"))
        return SetupResult(
            command="setup",
            workspacePath=str(workspace),
            profilePath=str(root),
            receiptPath=str(receipt_path),
            profileSha256=profile_id,
            downloadedFiles=downloaded,
            reusedFiles=reused,
            modelPath=str(model_path),
            sharedDatasetPath=str(root / "artifacts/shared"),
            customerDatasetPath=str(root / "artifacts/customer"),
            trainConfigPath=str(train_path),
            evaluationConfigPath=str(eval_path),
        )
    except OSError as error:
        raise ModelError("IO_FAILED", "Setup could not read or store local files") from error
    finally:
        with suppress(FileNotFoundError):
            if not lock_path.is_symlink() and lock_path.read_bytes() == lock_bytes:
                lock_path.unlink()

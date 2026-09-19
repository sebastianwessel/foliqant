"""Explicit pinned acquisition of supported Hugging Face model snapshots."""

from __future__ import annotations

import tempfile
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils.tqdm import disable_progress_bars
from pydantic import TypeAdapter, ValidationError

from .artifacts import copy_verified_file, sha256_file
from .contracts import ArtifactManifest, FileEntry
from .contracts.base import Commit, NonEmptyStr, RepoId
from .errors import ModelError
from .snapshots import (
    BOUNDED_MODEL_METADATA_NAMES,
    MAX_MODEL_METADATA_BYTES,
    checkpoint_from_snapshot,
    inspect_snapshot_metadata,
    validated_snapshot_filenames,
)


def fetch_model(*, repo: str, revision: str, license_ref: str, output: Path) -> ArtifactManifest:
    """Download only permitted files at an immutable commit and verify their format.

    The declared license is retained as provenance, not treated as permission
    granted by this program. Hugging Face authentication uses the user's normal
    local credentials; this operation never accepts gated agreements.
    """
    try:
        TypeAdapter(RepoId).validate_python(repo)
        TypeAdapter(Commit).validate_python(revision)
        TypeAdapter(NonEmptyStr).validate_python(license_ref)
    except ValidationError as error:
        raise ModelError(
            "ARGUMENT_INVALID", "A repository, immutable commit and license are required"
        ) from error
    if output.exists() or output.is_symlink():
        raise ModelError("OUTPUT_EXISTS", "Artifact output already exists")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ModelError("INPUT_NOT_FOUND", "Create a real output parent directory before fetching")
    # Keep network progress out of the single-result stdout contract.
    disable_progress_bars()
    with tempfile.TemporaryDirectory(prefix="foliqant-fetch-") as temporary:
        snapshot = Path(temporary)
        try:
            info = HfApi(endpoint="https://huggingface.co").model_info(
                repo,
                revision=revision,
                timeout=30,
                files_metadata=True,
            )
            if info.sha != revision:
                raise ModelError(
                    "INTEGRITY_FAILED", "Remote revision does not match the requested commit"
                )
            siblings = info.siblings or []
            names = validated_snapshot_filenames(item.rfilename for item in siblings)
            declared_sizes = {item.rfilename: getattr(item, "size", None) for item in siblings}
            metadata_names = [name for name in names if not name.endswith(".safetensors")]
            weight_names = [name for name in names if name.endswith(".safetensors")]
            for name in metadata_names:
                if name not in BOUNDED_MODEL_METADATA_NAMES:
                    continue
                size = declared_sizes.get(name)
                if size is not None and (
                    type(size) is not int or size < 0 or size > MAX_MODEL_METADATA_BYTES
                ):
                    raise ModelError("OUTPUT_INVALID", "Remote model metadata is too large")

            def download(name: str) -> None:
                cached = Path(
                    hf_hub_download(
                        repo_id=repo,
                        revision=revision,
                        filename=name,
                        endpoint="https://huggingface.co",
                        etag_timeout=30,
                    )
                ).resolve(strict=True)
                # HF cache entries may be symlinks; final artifacts never are.
                before = sha256_file(cached)
                if name in BOUNDED_MODEL_METADATA_NAMES and before[0] > MAX_MODEL_METADATA_BYTES:
                    raise ModelError("OUTPUT_INVALID", "Remote model metadata is too large")
                target = snapshot / name
                copy_verified_file(
                    cached,
                    target,
                    FileEntry(path=name, size=before[0], sha256=before[1]),
                )
                if sha256_file(cached) != before or sha256_file(target) != before:
                    raise ModelError("INTEGRITY_FAILED", "Cached model changed during acquisition")

            for name in metadata_names:
                download(name)
            inspect_snapshot_metadata(snapshot, expected_weight_files=weight_names)
            for name in weight_names:
                download(name)
        except ModelError:
            raise
        except Exception as error:
            raise ModelError(
                "NETWORK_FAILED", "Cannot acquire the pinned model; check access and connectivity"
            ) from error
        return checkpoint_from_snapshot(
            snapshot, output, repo=repo, revision=revision, license_ref=license_ref
        )

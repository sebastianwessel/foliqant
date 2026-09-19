"""Artifact integrity and transaction tests using only temporary files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from foliqant_model.artifacts import (
    ArtifactTransaction,
    build_inventory,
    copy_verified_file,
    create_manifest,
    load_verified_artifact,
    parent_snapshot,
    require_disjoint_output,
)
from foliqant_model.contracts import FileEntry, canonical_digest
from foliqant_model.errors import ModelError


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_model_files(directory: Path) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    (directory / "config.json").write_bytes(b"{}")
    (directory / "tokenizer_config.json").write_bytes(b'{"chat_template":"template"}')
    (directory / "model.safetensors").write_bytes(b"minimal-test-weight-bytes")


def _producer(command: str) -> dict[str, object]:
    return {
        "name": "foliqant-model",
        "version": "0.1.0",
        "command": command,
        "pythonVersion": "3.12.0",
        "platform": "test",
        "machine": "test",
        "codeRevision": None,
        "codeDirty": None,
        "codeIdentityUnavailableReason": "test fixture has no repository",
        "components": [],
    }


def _model_identity(directory: Path) -> dict[str, object]:
    tokenizer = directory / "tokenizer_config.json"
    tokenizer_entry = FileEntry(
        path="tokenizer_config.json",
        size=tokenizer.stat().st_size,
        sha256=_digest(tokenizer.read_bytes()),
    )
    return {
        "architecture": "fixture",
        "weightFormat": "safetensors",
        "configSha256": _digest((directory / "config.json").read_bytes()),
        "tokenizerSha256": canonical_digest([tokenizer_entry.model_dump(mode="json")]),
        "chatTemplateSha256": _digest(b"template"),
        "tokenizerFiles": ["tokenizer_config.json"],
        "chatTemplateSource": {
            "kind": "tokenizer-config",
            "value": "tokenizer_config.json#/chat_template",
        },
    }


def _checkpoint_fields(directory: Path) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "checkpoint",
        "name": "checkpoint-fixture",
        "createdAt": "2026-09-19T00:00:00Z",
        "stage": "upstream",
        "parents": [],
        "producer": _producer("fetch"),
        "sourceRights": [],
        "details": {
            "model": _model_identity(directory),
            "upstreamRepo": "owner/model",
            "upstreamRevision": "a" * 40,
            "licenseRef": "fixture-license",
            "weightPrecision": "float16",
            "compatibility": [],
        },
    }


def _make_checkpoint(directory: Path):  # type: ignore[no-untyped-def]
    _write_model_files(directory)
    return create_manifest(directory, _checkpoint_fields(directory))


def test_create_manifest_is_canonical_private_and_verifiable(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    manifest = _make_checkpoint(directory)

    assert load_verified_artifact(directory).root.artifactId == manifest.root.artifactId
    encoded = (directory / "manifest.json").read_bytes()
    assert (
        encoded
        == json.dumps(
            json.loads(encoded), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    assert stat_mode(directory) == 0o700
    assert all(stat_mode(path) == 0o600 for path in directory.iterdir())
    assert parent_snapshot(manifest).artifactId == manifest.root.artifactId

    with pytest.raises(ModelError) as captured:
        create_manifest(directory, _checkpoint_fields(directory))
    assert captured.value.code == "OUTPUT_EXISTS"
    assert load_verified_artifact(directory).root.artifactId == manifest.root.artifactId


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_verification_rejects_tampering_and_unlisted_files(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    _make_checkpoint(directory)
    (directory / "config.json").write_bytes(b'{"changed":true}')
    with pytest.raises(ModelError, match="inventory"):
        load_verified_artifact(directory)

    directory = tmp_path / "extra"
    _make_checkpoint(directory)
    (directory / "extra.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ModelError, match="inventory"):
        load_verified_artifact(directory)


def test_inventory_rejects_symlinks_without_following_them(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    directory.mkdir()
    target = tmp_path / "outside"
    target.write_text("private", encoding="utf-8")
    (directory / "linked").symlink_to(target)
    with pytest.raises(ModelError, match="symlink"):
        build_inventory(directory)


def test_inventory_rejects_unlisted_empty_directories(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    directory.mkdir()
    (directory / "empty").mkdir()
    with pytest.raises(ModelError, match="empty directory"):
        build_inventory(directory)


def test_output_containment_is_rejected_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    output = source / "nested-output"

    with pytest.raises(ModelError) as captured:
        require_disjoint_output(output, [source])

    assert captured.value.code == "ARGUMENT_INVALID"
    assert list(source.iterdir()) == []


def test_verified_copy_rejects_source_swapped_to_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"expected")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    outside.chmod(0o644)
    expected = FileEntry(path="source", size=8, sha256=_digest(b"expected"))
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    target = target_directory / "copied"
    real_open = __import__("os").open
    swapped = False

    def racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and Path(path) == source:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("foliqant_model.artifacts.os.open", racing_open)
    with pytest.raises(ModelError) as captured:
        copy_verified_file(source, target, expected)

    assert captured.value.code == "UNSAFE_ARTIFACT_PATH"
    assert not target.exists()
    assert stat_mode(outside) == 0o644


def test_verification_rejects_symlinked_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    _make_checkpoint(directory)
    manifest = directory / "manifest.json"
    external = tmp_path / "external-manifest.json"
    external.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(external)
    with pytest.raises(ModelError):
        load_verified_artifact(directory)


def test_registered_default_template_is_rejected_without_a_registry(tmp_path: Path) -> None:
    directory = tmp_path / "artifact"
    _write_model_files(directory)
    fields = _checkpoint_fields(directory)
    details = fields["details"]
    assert isinstance(details, dict)
    model = details["model"]
    assert isinstance(model, dict)
    model["chatTemplateSource"] = {"kind": "registered-default", "value": "fixture"}
    with pytest.raises(ModelError, match="registered default"):
        create_manifest(directory, fields)


def test_quantized_empty_exposure_is_verified_standalone(tmp_path: Path) -> None:
    checkpoint_directory = tmp_path / "checkpoint"
    checkpoint = _make_checkpoint(checkpoint_directory)
    quantized = tmp_path / "quantized"
    _write_model_files(quantized)
    leakage = quantized / "leakage.jsonl"
    leakage.write_bytes(b"")
    fields = {
        "schemaVersion": 1,
        "kind": "quantized",
        "name": "q4-fixture",
        "createdAt": "2026-09-19T00:00:01Z",
        "stage": "upstream",
        "parents": [parent_snapshot(checkpoint).model_dump(mode="json")],
        "producer": _producer("quantize"),
        "sourceRights": [],
        "details": {
            "model": _model_identity(quantized),
            "bits": 4,
            "groupSize": 64,
            "parentArtifactId": checkpoint.root.artifactId,
            "precisionHistory": [
                {
                    "operation": "quantize",
                    "fromPrecision": "float16",
                    "toPrecision": "int4",
                    "lossy": True,
                }
            ],
            "exposureLeakageIndex": {
                "file": {
                    "path": "leakage.jsonl",
                    "sha256": _digest(b""),
                    "recordCount": 0,
                    "format": "jsonl",
                },
                "splits": [],
                "entryCount": 0,
            },
            "compatibility": [],
            "inheritedLeakageIndexes": [],
        },
    }
    manifest = create_manifest(quantized, fields)
    assert load_verified_artifact(quantized).root.artifactId == manifest.root.artifactId

    leakage.write_bytes(b"{}\n")
    with pytest.raises(ModelError):
        load_verified_artifact(quantized)


def test_transaction_publishes_once_and_retains_completed_workspace(tmp_path: Path) -> None:
    output = tmp_path / "published"
    transaction = ArtifactTransaction(output, "fetch")
    with transaction:
        _write_model_files(transaction.staging_path)  # directory already exists
        manifest = create_manifest(
            transaction.staging_path, _checkpoint_fields(transaction.staging_path)
        )
        assert transaction.publish(manifest) == output

    assert output.is_dir()
    assert not transaction.lock_path.exists()
    state = json.loads((transaction.workspace_path / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["finalizedArtifactId"] == manifest.root.artifactId


def test_transaction_never_replaces_destination_created_during_run(tmp_path: Path) -> None:
    output = tmp_path / "published"
    transaction = ArtifactTransaction(output, "fetch")
    with pytest.raises(ModelError) as captured:
        with transaction:
            _write_model_files(transaction.staging_path)
            manifest = create_manifest(
                transaction.staging_path, _checkpoint_fields(transaction.staging_path)
            )
            output.mkdir()
            (output / "owner").write_text("other invocation", encoding="utf-8")
            transaction.publish(manifest)
    assert captured.value.code == "OUTPUT_EXISTS"
    assert (output / "owner").read_text(encoding="utf-8") == "other invocation"
    assert transaction.staging_path.exists()
    assert not transaction.lock_path.exists()
    state = json.loads((transaction.workspace_path / "run.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"


def test_existing_lock_is_never_removed_as_stale(tmp_path: Path) -> None:
    output = tmp_path / "published"
    lock = tmp_path / ".published.lock"
    lock.write_text("operator must inspect this", encoding="utf-8")
    with pytest.raises(ModelError) as captured:
        ArtifactTransaction(output, "fetch")
    assert captured.value.code == "OUTPUT_EXISTS"
    assert lock.read_text(encoding="utf-8") == "operator must inspect this"

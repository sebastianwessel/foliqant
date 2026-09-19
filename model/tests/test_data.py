"""Dataset preparation tests using synthetic records created under tmp_path."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts import canonical_digest
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError


def _record(
    identifier: str,
    group: str,
    *,
    prompt: str | None = None,
    answer: str | None = None,
    origin: str = "human",
    reviewed: bool = True,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": identifier,
        "sourceId": "source",
        "language": "en",
        "groupKeys": [group],
        "messages": [
            {"role": "user", "content": prompt or f"question {identifier}"},
            {"role": "assistant", "content": answer or f"answer {identifier}"},
        ],
        "tags": [],
        "origin": origin,
        "reviewed": reviewed,
    }


def _write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _write_config(path: Path, source: Path, *, max_record_bytes: int = 1_048_576) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "fixture-data",
                "sources": [
                    {
                        "id": "source",
                        "path": source.name,
                        "license": "test-only",
                        "licenseEvidence": "local synthetic fixture",
                        "trainingAllowed": True,
                        "sharedTrainingAllowed": True,
                        "redistributionAllowed": False,
                        "privacy": "public",
                        "commercialUse": "unknown",
                        "restrictions": ["test-only"],
                    }
                ],
                "seed": 7,
                "validationFraction": 0.1,
                "calibrationFraction": 0.1,
                "testFraction": 0.1,
                "maxRecords": 100,
                "maxRecordBytes": max_record_bytes,
            }
        ),
        encoding="utf-8",
    )


def test_prepare_dataset_emits_verified_deterministic_partitions(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    records = [
        _record("a", "linked"),
        _record("b", "linked"),
        _record("c", "c"),
        _record("d", "d", origin="synthetic"),
        _record("e", "e"),
    ]
    _write_records(source, records)
    config = tmp_path / "dataset.json"
    _write_config(config, source)

    manifest = prepare_dataset(config, tmp_path / "output")
    verified = load_verified_artifact(tmp_path / "output")

    assert verified.root.artifactId == manifest.root.artifactId
    assert manifest.root.kind == "dataset"
    assert (
        manifest.root.details.assignments["a"].componentId
        == manifest.root.details.assignments["b"].componentId
    )
    assert (
        manifest.root.details.assignments["a"].split == manifest.root.details.assignments["b"].split
    )
    assert manifest.root.details.diagnostic is True
    assert (
        sum(
            getattr(manifest.root.details.partitions, split).records
            for split in ("train", "validation", "calibration", "test")
        )
        == 5
    )
    assert (tmp_path / "output" / "records" / "test.jsonl").is_file()


def test_transitive_groups_stay_together_and_four_components_fill_all_splits(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.jsonl"
    records = [
        _record("a", "thread"),
        _record("b", "thread"),
        _record("c", "translation"),
        _record("d", "d"),
        _record("e", "e"),
        _record("f", "f"),
    ]
    records[1]["groupKeys"] = ["thread", "translation"]
    _write_records(source, records)
    config = tmp_path / "dataset.json"
    _write_config(config, source)

    manifest = prepare_dataset(config, tmp_path / "output")
    assignments = manifest.root.details.assignments

    assert {assignments[item].componentId for item in ("a", "b", "c")} == {
        assignments["a"].componentId
    }
    assert {assignments[item].split for item in ("a", "b", "c")} == {assignments["a"].split}
    assert {
        getattr(manifest.root.details.partitions, split).components
        for split in ("train", "validation", "calibration", "test")
    } == {1}


def test_input_order_does_not_change_dataset_content_identity(tmp_path: Path) -> None:
    records = [_record(identifier, identifier) for identifier in ("a", "b", "c", "d", "e")]
    first_source = tmp_path / "first.jsonl"
    second_source = tmp_path / "second.jsonl"
    _write_records(first_source, records)
    _write_records(second_source, list(reversed(records)))
    first_config = tmp_path / "first.json"
    second_config = tmp_path / "second.json"
    _write_config(first_config, first_source)
    _write_config(second_config, second_source)

    first = prepare_dataset(first_config, tmp_path / "first-output")
    second = prepare_dataset(second_config, tmp_path / "second-output")

    assert first.root.details.datasetContentId == second.root.details.datasetContentId
    assert first.root.details.assignments == second.root.details.assignments
    for split in ("train", "validation", "calibration", "test"):
        assert (tmp_path / "first-output" / "records" / f"{split}.jsonl").read_bytes() == (
            tmp_path / "second-output" / "records" / f"{split}.jsonl"
        ).read_bytes()


def test_normalized_duplicate_conversations_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    records = [
        _record("a", "a", prompt="Cafe\u0301\r\nline", answer="same"),
        _record("b", "b", prompt="Café\nline", answer="same"),
        _record("c", "c"),
        _record("d", "d"),
    ]
    _write_records(source, records)
    config = tmp_path / "dataset.json"
    _write_config(config, source)

    with pytest.raises(ModelError) as captured:
        prepare_dataset(config, tmp_path / "output")
    assert captured.value.code == "DATA_DUPLICATE_CONVERSATION"
    assert not (tmp_path / "output").exists()


def test_duplicate_ids_and_wrong_declared_source_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    records = [_record("a", "a"), _record("a", "b"), _record("c", "c"), _record("d", "d")]
    _write_records(source, records)
    config = tmp_path / "dataset.json"
    _write_config(config, source)
    with pytest.raises(ModelError) as captured:
        prepare_dataset(config, tmp_path / "duplicate-output")
    assert captured.value.code == "DATA_DUPLICATE_ID"

    records[1]["id"] = "b"
    records[1]["sourceId"] = "undeclared"
    _write_records(source, records)
    with pytest.raises(ModelError) as captured:
        prepare_dataset(config, tmp_path / "wrong-source-output")
    assert captured.value.code == "DATA_RECORD_INVALID"


def test_too_few_components_and_oversized_rows_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    _write_records(
        source,
        [_record("a", "one"), _record("b", "one"), _record("c", "two"), _record("d", "three")],
    )
    config = tmp_path / "dataset.json"
    _write_config(config, source)
    with pytest.raises(ModelError) as captured:
        prepare_dataset(config, tmp_path / "few-output")
    assert captured.value.code == "DATA_PARTITION_INVALID"

    _write_records(source, [_record(identifier, identifier) for identifier in ("a", "b", "c", "d")])
    _write_config(config, source, max_record_bytes=32)
    with pytest.raises(ModelError) as captured:
        prepare_dataset(config, tmp_path / "large-output")
    assert captured.value.code == "DATA_RECORD_INVALID"


@pytest.mark.parametrize("forged_diagnostic", [False, True])
def test_verify_rejects_self_checksummed_forged_dataset_semantics(
    tmp_path: Path, forged_diagnostic: bool
) -> None:
    source = tmp_path / "source.jsonl"
    _write_records(
        source,
        [
            _record("a", "a", reviewed=False),
            _record("b", "b"),
            _record("c", "c"),
            _record("d", "d"),
        ],
    )
    config = tmp_path / "dataset.json"
    _write_config(config, source)
    output = tmp_path / "output"
    prepare_dataset(config, output)

    leakage_path = output / "leakage.jsonl"
    forged_rows = []
    for line in leakage_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        row["groupKeyHashes"] = ["0" * 64]
        row["conversationHash"] = "1" * 64
        row["promptHash"] = "2" * 64
        forged_rows.append(row)
    forged_leakage = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in forged_rows
    ).encode("utf-8")
    leakage_path.write_bytes(forged_leakage)

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    forged_digest = hashlib.sha256(forged_leakage).hexdigest()
    for entry in manifest["files"]:
        if entry["path"] == "leakage.jsonl":
            entry["size"] = len(forged_leakage)
            entry["sha256"] = forged_digest
    manifest["details"]["leakageIndex"]["file"]["sha256"] = forged_digest
    manifest["details"]["diagnostic"] = forged_diagnostic
    identity_payload = dict(manifest)
    identity_payload.pop("artifactId")
    manifest["artifactId"] = canonical_digest(identity_payload)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ModelError, match="diagnostic|leakage"):
        load_verified_artifact(output)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_fifo_source_is_rejected_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    os.mkfifo(source, mode=0o600)
    config = tmp_path / "dataset.json"
    _write_config(config, source)
    package_root = Path(__file__).parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(package_root), environment.get("PYTHONPATH", "")]
    )
    script = """
import sys
from pathlib import Path
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError
try:
    prepare_dataset(Path(sys.argv[1]), Path(sys.argv[2]))
except ModelError as error:
    raise SystemExit(0 if error.code == 'UNSAFE_ARTIFACT_PATH' else 2)
raise SystemExit(3)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(config), str(tmp_path / "output")],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


def test_input_at_size_limit_can_expand_when_defaults_are_materialized(tmp_path: Path) -> None:
    records = [_record(str(index), str(index)) for index in range(4)]
    for record in records:
        record.pop("tags")
    rows = [json.dumps(record, separators=(",", ":")) for record in records]
    maximum = max(len(row.encode("utf-8")) for row in rows)
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(rows) + "\n")
    config = tmp_path / "dataset.json"
    _write_config(config, source, max_record_bytes=maximum)
    output = tmp_path / "prepared"
    manifest = prepare_dataset(config, output)
    assert load_verified_artifact(output) == manifest
    retained = [
        line
        for path in (output / "records").glob("*.jsonl")
        for line in path.read_bytes().splitlines()
    ]
    assert all(len(line) > maximum for line in retained)
    assert all(json.loads(line)["tags"] == [] for line in retained)

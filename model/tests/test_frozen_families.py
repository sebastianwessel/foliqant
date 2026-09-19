"""Frozen source-family partition and generation-lineage regressions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts import canonical_digest
from foliqant_model.contracts.inputs import DataRecord
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError


def _generation(*parents: str) -> dict[str, object]:
    return {
        "provider": "openai-compatible",
        "modelId": "local-teacher",
        "modelIdentitySha256": "1" * 64,
        "promptSha256": "2" * 64,
        "parametersSha256": "3" * 64,
        "requestSha256": "4" * 64,
        "parentRecordIds": sorted(parents),
    }


def _record(
    identifier: str,
    family: str,
    *,
    group: str | None = None,
    origin: str = "human",
    reviewed: bool = True,
    generation: dict[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "id": identifier,
        "sourceId": "source",
        "language": "en",
        "groupKeys": [group or identifier],
        "messages": [
            {"role": "user", "content": f"question {identifier}"},
            {"role": "assistant", "content": f"answer {identifier}"},
        ],
        "tags": [],
        "origin": origin,
        "reviewed": reviewed,
        "familyId": family,
    }
    if generation is not None:
        value["generation"] = generation
    return value


def _families() -> dict[str, dict[str, object]]:
    return {
        "family-a": {"split": "train", "sourceSplits": ["train"]},
        "family-b": {"split": "validation", "sourceSplits": ["train"]},
        "family-c": {"split": "calibration", "sourceSplits": ["validation"]},
        "family-d": {"split": "test", "sourceSplits": ["test"]},
    }


def _write_input(
    root: Path,
    records: list[dict[str, object]],
    *,
    families: dict[str, dict[str, object]] | None = None,
) -> Path:
    source = root / "records.jsonl"
    source.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    config = root / "dataset.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "frozen-fixture",
                "sources": [
                    {
                        "id": "source",
                        "path": source.name,
                        "license": "test-only",
                        "licenseEvidence": "local fixture",
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
                "maxRecordBytes": 1_048_576,
                "frozenFamilies": families or _families(),
            }
        ),
        encoding="utf-8",
    )
    return config


def _base_records() -> list[dict[str, object]]:
    return [
        _record("a", "family-a"),
        _record("b", "family-b"),
        _record("c", "family-c"),
        _record("d", "family-d"),
    ]


def test_frozen_assignments_survive_generated_variants(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = prepare_dataset(_write_input(first_root, _base_records()), first_root / "output")
    augmented = [
        *_base_records(),
        _record(
            "a-de",
            "family-a",
            origin="teacher",
            reviewed=False,
            generation=_generation("a"),
        ),
    ]
    second = prepare_dataset(_write_input(second_root, augmented), second_root / "output")

    assert {key: value.split for key, value in first.root.details.assignments.items()} == {
        "a": "train",
        "b": "validation",
        "c": "calibration",
        "d": "test",
    }
    assert second.root.details.assignments["a"].split == "train"
    assert second.root.details.assignments["a-de"].split == "train"
    assert (
        second.root.details.assignments["a"].componentId
        == second.root.details.assignments["a-de"].componentId
    )
    family_hash = hashlib.sha256(b"family\0family-a").hexdigest()
    assert family_hash in second.root.details.assignments["a-de"].groupIds
    assert family_hash in (second_root / "output" / "leakage.jsonl").read_text()


def test_connected_groups_cannot_bridge_frozen_splits(tmp_path: Path) -> None:
    records = _base_records()
    records[0]["groupKeys"] = ["bridge"]
    records[3]["groupKeys"] = ["bridge"]
    with pytest.raises(ModelError) as failure:
        prepare_dataset(_write_input(tmp_path, records), tmp_path / "output")
    assert failure.value.code == "DATA_PARTITION_INVALID"
    assert "spans" in failure.value.message


def test_frozen_map_is_bound_to_content_identity_and_tampering_fails(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = prepare_dataset(_write_input(first_root, _base_records()), first_root / "output")
    changed = _families()
    changed["family-a"] = {"split": "train", "sourceSplits": ["official-train"]}
    second = prepare_dataset(
        _write_input(second_root, _base_records(), families=changed), second_root / "output"
    )
    assert first.root.details.assignments == second.root.details.assignments
    assert first.root.details.recordFiles == second.root.details.recordFiles
    assert first.root.details.datasetContentId != second.root.details.datasetContentId

    manifest_path = first_root / "output" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["details"]["datasetConfig"]["frozenFamilies"]["family-a"]["sourceSplits"] = ["forged"]
    payload = dict(manifest)
    payload.pop("artifactId")
    manifest["artifactId"] = canonical_digest(payload)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelError, match="artifact manifest"):
        load_verified_artifact(first_root / "output")


@pytest.mark.parametrize(
    ("origin", "reviewed"),
    [("human", False), ("teacher", True), ("synthetic", True)],
)
def test_generation_provenance_never_claims_human_or_reviewed(origin: str, reviewed: bool) -> None:
    with pytest.raises(ValidationError):
        DataRecord.model_validate(
            _record(
                "generated",
                "family-a",
                origin=origin,
                reviewed=reviewed,
                generation=_generation("parent"),
            ),
            strict=True,
        )


@pytest.mark.parametrize("lineage", ["missing", "other-family", "cycle"])
def test_generation_parents_must_exist_in_same_acyclic_family(tmp_path: Path, lineage: str) -> None:
    records = _base_records()
    if lineage == "missing":
        records.append(
            _record(
                "generated",
                "family-a",
                origin="teacher",
                reviewed=False,
                generation=_generation("absent"),
            )
        )
    elif lineage == "other-family":
        records.append(
            _record(
                "generated",
                "family-a",
                origin="teacher",
                reviewed=False,
                generation=_generation("b"),
            )
        )
    else:
        records[0] = _record(
            "a",
            "family-a",
            origin="teacher",
            reviewed=False,
            generation=_generation("generated"),
        )
        records.append(
            _record(
                "generated",
                "family-a",
                origin="teacher",
                reviewed=False,
                generation=_generation("a"),
            )
        )
    with pytest.raises(ModelError) as failure:
        prepare_dataset(_write_input(tmp_path, records), tmp_path / "output")
    assert failure.value.code == "DATA_RECORD_INVALID"

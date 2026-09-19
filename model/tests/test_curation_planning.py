from __future__ import annotations

from collections import Counter

import pytest

from foliqant_model.contracts import DataRecord, ResolvedSourceDeclaration
from foliqant_model.curation.contracts import CurationPlan, ImportedRecord, SourceBatch
from foliqant_model.curation.planning import freeze_sources
from foliqant_model.errors import ModelError

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _row(
    record_id: str,
    *,
    split: str = "train",
    prompt: str | None = None,
    answer: str | None = None,
    group: str | None = None,
    source: str = "source",
) -> ImportedRecord:
    record = DataRecord.model_validate(
        {
            "schemaVersion": 1,
            "id": record_id,
            "sourceId": source,
            "language": "en",
            "groupKeys": [group or "group-" + record_id],
            "messages": [
                {"role": "user", "content": prompt or "prompt " + record_id},
                {"role": "assistant", "content": answer or "answer " + record_id},
            ],
            "tags": [],
            "origin": "human",
            "reviewed": True,
        }
    )
    return ImportedRecord.model_validate(
        {
            "record": record.model_dump(mode="json"),
            "originalSplit": split,
            "task": "decision",
            "originalId": "original-" + record_id,
        }
    )


def _batch(*rows: ImportedRecord, source: str = "source") -> SourceBatch:
    rights = ResolvedSourceDeclaration.model_validate(
        {
            "id": source,
            "license": "test-only",
            "licenseEvidence": "local test",
            "trainingAllowed": True,
            "sharedTrainingAllowed": True,
            "redistributionAllowed": False,
            "privacy": "public",
            "attribution": "",
            "commercialUse": "unknown",
            "restrictions": [],
        }
    )
    return SourceBatch(
        source=rights,
        revision="revision-1",
        assets=[],
        records=list(rows),
        totalAvailable=len(rows),
    )


def _plan(*rows: ImportedRecord) -> CurationPlan:
    return freeze_sources(
        [_batch(*rows)],
        seed=42,
        configuration_digest=_DIGEST_A,
        catalog_digest=_DIGEST_B,
    )


def _family_by_record(plan: CurationPlan) -> dict[str, str]:
    return {
        row.record.id: row.record.familyId
        for row in plan.records
        if row.record.familyId is not None
    }


def test_duplicate_holdout_constrains_family_before_collapse() -> None:
    plan = _plan(
        _row("duplicate-train", split="train", prompt="same", answer="same"),
        _row("duplicate-test", split="test", prompt="same", answer="same"),
        *[_row(f"flex-{index}") for index in range(5)],
    )
    emitted = {row.record.id for row in plan.records}
    assert "duplicate-test" in emitted
    assert "duplicate-train" not in emitted
    family = _family_by_record(plan)["duplicate-test"]
    assert plan.frozenFamilies[family].split == "test"
    assert plan.frozenFamilies[family].sourceSplits == ["source:test", "source:train"]
    assert plan.excludedCounts["duplicate-conversation"] == 1


def test_group_links_preserve_every_observed_source_split() -> None:
    plan = _plan(
        _row("linked-validation", split="validation", group="shared"),
        _row("linked-train", split="train", group="shared"),
        *[_row(f"independent-{index}") for index in range(5)],
    )
    families = _family_by_record(plan)
    assert families["linked-validation"] == families["linked-train"]
    assignment = plan.frozenFamilies[families["linked-validation"]]
    assert assignment.split in {"validation", "calibration"}
    assert assignment.sourceSplits == ["source:train", "source:validation"]


def test_test_group_member_prevents_training_promotion() -> None:
    plan = _plan(
        _row("group-test", split="test", group="connected"),
        _row("group-train", split="train", group="connected"),
        *[_row(f"flex-{index}") for index in range(5)],
    )
    families = _family_by_record(plan)
    assert families["group-test"] == families["group-train"]
    assert plan.frozenFamilies[families["group-test"]].split == "test"


def test_cross_source_duplicate_retains_all_source_split_evidence() -> None:
    train = _row(
        "source-a-train",
        split="train",
        prompt="duplicate",
        answer="same",
        source="source-a",
    )
    test = _row(
        "source-b-test",
        split="test",
        prompt="duplicate",
        answer="same",
        source="source-b",
    )
    plan = freeze_sources(
        [
            _batch(train, source="source-a"),
            _batch(test, source="source-b"),
            _batch(
                *[_row(f"flex-{index}", source="source-c") for index in range(5)],
                source="source-c",
            ),
        ],
        seed=42,
        configuration_digest=_DIGEST_A,
        catalog_digest=_DIGEST_B,
    )
    family = _family_by_record(plan)["source-b-test"]
    assert plan.frozenFamilies[family].split == "test"
    assert plan.frozenFamilies[family].sourceSplits == [
        "source-a:train",
        "source-b:test",
    ]


def test_four_training_families_fill_all_partitions_once() -> None:
    plan = _plan(*[_row(f"record-{index}") for index in range(4)])
    assert Counter(item.split for item in plan.frozenFamilies.values()) == {
        "train": 1,
        "validation": 1,
        "calibration": 1,
        "test": 1,
    }
    assert len(plan.records) == 4


def test_original_validation_never_moves_to_train_or_test() -> None:
    plan = _plan(
        _row("validation-a", split="validation"),
        _row("validation-b", split="validation"),
        *[_row(f"flex-{index}") for index in range(5)],
    )
    families = _family_by_record(plan)
    assert plan.frozenFamilies[families["validation-a"]].split in {
        "validation",
        "calibration",
    }
    assert plan.frozenFamilies[families["validation-b"]].split in {
        "validation",
        "calibration",
    }


def test_all_test_families_cannot_be_promoted_to_training() -> None:
    with pytest.raises(ModelError) as raised:
        _plan(*[_row(f"test-{index}", split="test") for index in range(4)])
    assert raised.value.code == "DATA_PARTITION_INVALID"


def test_generated_variant_inherits_frozen_family_without_replanning() -> None:
    plan = _plan(*[_row(f"record-{index}") for index in range(4)])
    parent = plan.records[0].record
    assert parent.familyId is not None
    original = plan.frozenFamilies[parent.familyId]
    variant = DataRecord.model_validate(
        {
            **parent.model_dump(mode="json"),
            "id": "generated-variant",
            "origin": "synthetic",
            "reviewed": False,
            "messages": [
                {"role": "user", "content": "variant wording"},
                parent.messages[-1].model_dump(mode="json"),
            ],
        }
    )
    assert variant.familyId == parent.familyId
    assert plan.frozenFamilies[variant.familyId] == original
    assert set(plan.frozenFamilies) == {
        row.record.familyId for row in plan.records if row.record.familyId is not None
    }

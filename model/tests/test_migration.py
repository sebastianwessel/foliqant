from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from foliqant_decisions import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceResult,
    Citation,
    DecisionInput,
    DecisionOption,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    Explanation,
)
from pydantic import ValidationError

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts import ChatMessage, DataRecord, GenerationProvenance
from foliqant_model.contracts.inputs import FrozenFamilyAssignment, ResolvedSourceDeclaration
from foliqant_model.curation import migration
from foliqant_model.curation.contracts import (
    CandidateJob,
    CandidateOutcome,
    CurationConfig,
    ImportedRecord,
)
from foliqant_model.curation.decision_contracts import DecisionDataSettings
from foliqant_model.curation.decision_generation import canonical_decision_output
from foliqant_model.curation.decision_seeds import DecisionSeed, project_source
from foliqant_model.curation.endpoint import EndpointModelIdentity
from foliqant_model.curation.migration_contracts import (
    MigrationEntry,
    MigrationPlan,
)
from foliqant_model.curation.runner import _publish
from foliqant_model.curation.storage import load_object, store_object
from foliqant_model.errors import ModelError


def _rights(source_id: str, *, license_name: str = "Test-1.0") -> ResolvedSourceDeclaration:
    return ResolvedSourceDeclaration(
        id=source_id,
        license=license_name,
        licenseEvidence="Pinned test license evidence for " + source_id,
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=True,
        privacy="public",
        attribution="Test source " + source_id,
        commercialUse="allowed",
        restrictions=["Preserve the test notice"],
    )


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="saved-local-model",
        modelType="llm",
        publisher=None,
        architecture=None,
        format=None,
        quantization=None,
        sizeBytes=None,
        maxContextLength=None,
        metadataSha256="a" * 64,
    )


def _seed(
    record_id: str,
    *,
    source_id: str = "authored",
    system: str = "Current native instructions.",
    original_source_record: str | None = None,
    mode: Literal["rewrite", "annotate"] = "annotate",
) -> DecisionSeed:
    text = f"The receipt for request {record_id} was requested."
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 1,
            "state": {"sources": [{"id": "message-1", "kind": "message", "text": text}]},
            "questions": [
                {
                    "id": "receipt",
                    "type": "predicate",
                    "prompt": "Was a receipt requested?",
                    "criteria": ["Use an explicit receipt request."],
                    "allowedSourceIds": ["message-1"],
                }
            ],
        },
        strict=True,
    )
    output = DecisionOutput.model_validate(
        {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "receipt",
                    "type": "predicate",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {"value": "true"},
                    "explanation": {
                        "summary": "The receipt request is explicit.",
                        "evidence": [{"sourceId": "message-1", "quote": text}],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ],
        },
        strict=True,
    )
    tags = ["native-decision", "question-type:predicate"]
    if original_source_record is not None:
        tags.append("original-source-record:" + original_source_record)
    return DecisionSeed(
        parent=DataRecord(
            schemaVersion=1,
            id=record_id,
            sourceId=source_id,
            language="en",
            groupKeys=["group-" + record_id],
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=task.model_dump_json()),
                ChatMessage(role="assistant", content=output.model_dump_json()),
            ],
            tags=tags,
            origin="synthetic",
            reviewed=False,
            familyId="family-" + record_id,
        ),
        scenario="receipt-request",
        mode=mode,
        rewriteSourceIds=["message-1"] if mode == "rewrite" else [],
    )


def _generated(seed: DecisionSeed) -> DataRecord:
    task = seed.input.model_copy(deep=True)
    task.state.sources[0].text = "Case details: " + task.state.sources[0].text
    output = canonical_decision_output(seed, task)
    return DataRecord(
        schemaVersion=1,
        id="generated-authored-accepted",
        sourceId="generated-" + seed.parent.sourceId,
        language=seed.parent.language,
        groupKeys=seed.parent.groupKeys,
        messages=[
            *seed.parent.messages[:-2],
            ChatMessage(role="user", content=task.model_dump_json()),
            ChatMessage(role="assistant", content=output.model_dump_json()),
        ],
        tags=seed.parent.tags,
        origin="teacher",
        reviewed=False,
        familyId=seed.parent.familyId,
        generation=GenerationProvenance(
            provider="openai-compatible",
            modelId="saved-local-model",
            modelIdentitySha256="a" * 64,
            promptSha256="b" * 64,
            parametersSha256="c" * 64,
            requestSha256="d" * 64,
            parentRecordIds=[seed.parent.id],
        ),
    )


def _source_row(source_id: str, user: object, assistant: object) -> ImportedRecord:
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id=f"{source_id}.record.original",
            sourceId=source_id,
            language="en",
            groupKeys=[f"{source_id}:group:original"],
            messages=[
                ChatMessage(role="system", content="Source task."),
                ChatMessage(role="user", content=json.dumps(user)),
                ChatMessage(role="assistant", content=json.dumps(assistant)),
            ],
            tags=[source_id],
            origin="human" if source_id == "banking77" else "synthetic",
            reviewed=False,
            familyId=f"{source_id}.family.original",
        ),
        originalSplit="train",
        task="classification" if source_id == "banking77" else "question-answering",
        originalId="original",
    )


def _old_banking77(row: ImportedRecord, current: DecisionSeed) -> DecisionSeed:
    source = json.loads(row.record.messages[-2].content)
    annotation = json.loads(row.record.messages[-1].content)
    labels = source["labels"]
    selected = labels.index(annotation["intent"])
    task = DecisionInput(
        state=DecisionState(
            sources=[DecisionSource(id="request", kind="message", text=source["request"])]
        ),
        questions=[
            ChoiceQuestion(
                id="intent",
                type="choice",
                prompt="Choose the single best matching banking request category.",
                criteria=["Use the meaning of the complete request."],
                allowedSourceIds=["request"],
                options=[
                    DecisionOption(id=f"label-{index:03d}", description=label)
                    for index, label in enumerate(labels)
                ],
            )
        ],
    )
    result = DecisionOutput(
        results=[
            ChoiceResult(
                questionId="intent",
                type="choice",
                answerability=Answerability(status="answerable", issues=[]),
                answer=ChoiceAnswer(optionId=f"label-{selected:03d}"),
                explanation=Explanation(
                    summary="The source annotation selects the matching category.",
                    evidence=[Citation(sourceId="request", quote=source["request"])],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    parent = current.parent.model_copy(deep=True)
    parent.id = "historical-banking77"
    parent.messages = [
        ChatMessage(role="system", content="Historical native instructions."),
        ChatMessage(role="user", content=task.model_dump_json()),
        ChatMessage(role="assistant", content=result.model_dump_json()),
    ]
    parent.tags = [
        tag for tag in parent.tags if not tag.startswith(("source-label:", "category-catalog:"))
    ]
    return DecisionSeed(
        parent=parent,
        scenario="projection:banking77",
        mode="annotate",
        rewriteSourceIds=[],
    )


def _entry(seed: DecisionSeed, *, status: str = "authored-reference") -> MigrationEntry:
    return MigrationEntry.model_validate(
        {
            "record": seed.parent.model_dump(mode="json"),
            "provenance": {
                "recordId": seed.parent.id,
                "operation": "retained",
                "evidenceStatus": status,
                "parentRecordIds": [],
                "sources": [
                    {
                        "datasetId": seed.parent.sourceId,
                        "recordId": seed.parent.id,
                        "originalId": seed.parent.id,
                        "snapshotSha256": "e" * 64,
                        "rightsSourceId": seed.parent.sourceId,
                    }
                ],
                "rule": "test-retention-v1",
            },
        }
    )


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()
    }


def _parent_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    Path,
    DecisionSeed,
    DataRecord,
    DecisionSeed,
    DecisionSeed,
    SimpleNamespace,
]:
    parent = tmp_path / "completed-parent"
    config = CurationConfig(decisionData=DecisionDataSettings(examplesPerScenario=4))

    current_authored = _seed("authored-parent", mode="rewrite")
    old_authored = _seed(
        "authored-parent", system="Historical native instructions.", mode="rewrite"
    )
    derivative = _generated(old_authored)
    current_calibration = _seed("authored-calibration")
    old_calibration = _seed("authored-calibration", system="Historical native instructions.")
    current_test = _seed("authored-test")
    old_test = _seed("authored-test", system="Historical native instructions.")

    banking = _source_row(
        "banking77",
        {
            "labels": ["Refund_not_showing_up", "reverted_card_payment?"],
            "request": "My card payment was reversed.",
        },
        {"intent": "reverted_card_payment?"},
    )
    current_banking = project_source(banking)
    assert current_banking is not None
    old_banking = _old_banking77(banking, current_banking)

    tatqa = _source_row(
        "tatqa",
        {"table": [["Year", "Value"], ["2024", "10"], ["2025", "12"]]},
        {"answer": "2025"},
    )
    tatqa_projection = _seed(
        "tatqa-projection",
        source_id="native-tatqa",
        original_source_record=tatqa.record.id,
    ).model_copy(update={"scenario": "projection:tatqa"})

    assert old_authored.parent.familyId is not None
    assert old_banking.parent.familyId is not None
    assert old_calibration.parent.familyId is not None
    assert old_test.parent.familyId is not None
    assert tatqa_projection.parent.familyId is not None

    families = {
        old_authored.parent.familyId: FrozenFamilyAssignment(
            split="train", sourceSplits=["authored:train"]
        ),
        old_banking.parent.familyId: FrozenFamilyAssignment(
            split="validation", sourceSplits=["banking77:validation"]
        ),
        old_calibration.parent.familyId: FrozenFamilyAssignment(
            split="calibration", sourceSplits=["authored:calibration"]
        ),
        old_test.parent.familyId: FrozenFamilyAssignment(
            split="test", sourceSplits=["authored:test"]
        ),
    }
    records = [
        old_authored.parent,
        derivative,
        old_banking.parent,
        old_calibration.parent,
        old_test.parent,
    ]
    parent_rights = [
        _rights("authored"),
        _rights("generated-authored", license_name="Test-1.0; model terms unresolved"),
        _rights("native-banking77", license_name="CC-BY-4.0"),
    ]
    families = dict(sorted(families.items()))
    artifact = _publish(parent, "native-decisions", records, parent_rights, families, config.seed)

    raw_rows = [banking, tatqa]
    store_object(
        parent / "source-plan.json",
        {"records": [row.model_dump(mode="json") for row in raw_rows]},
    )
    store_object(
        parent / "native-seeds.json",
        [
            seed.model_dump(mode="json")
            for seed in [old_authored, old_banking, old_calibration, old_test]
        ],
    )
    store_object(
        parent / "native-families.json",
        {key: value.model_dump(mode="json") for key, value in sorted(families.items())},
    )
    store_object(parent / "configuration.json", config.model_dump(mode="json"))
    store_object(parent / "sources/banking77.json", {"source": "frozen-banking77"})
    store_object(parent / "sources/tatqa.json", {"source": "frozen-tatqa"})
    store_object(parent / "model-identity.json", _identity().model_dump(mode="json"))
    job = CandidateJob(
        jobId="1" * 64,
        parentRecordId=old_authored.parent.id,
        familyId=old_authored.parent.familyId,
        split="train",
        language="en",
        purpose="decision-training",
        operation="native-decision",
    )
    store_object(parent / "jobs.json", [job.model_dump(mode="json")])
    store_object(
        parent / "outcomes" / (job.jobId + ".json"),
        CandidateOutcome(
            jobId=job.jobId,
            status="accepted",
            reason="automated-checks-passed",
            attempts=1,
            requestSha256="2" * 64,
            responseSha256="3" * 64,
            record=derivative,
        ).model_dump(mode="json"),
    )
    store_object(
        parent / "completed-report.json",
        {
            "status": "completed",
            "generatedAccepted": 1,
            "generatedQuarantined": 0,
            "datasets": [{"artifactId": artifact.root.artifactId}],
        },
    )

    snapshot = SimpleNamespace(
        config=config,
        plan=SimpleNamespace(records=raw_rows),
        seeds=[old_authored, old_banking, old_calibration, old_test],
        families=families,
        batches=[],
    )
    current_seeds = [current_authored, current_banking, current_calibration, current_test]
    projection = SimpleNamespace(
        sources=[_rights("native-tatqa", license_name="CC-BY-4.0")],
        frozenFamilies={
            tatqa_projection.parent.familyId: FrozenFamilyAssignment(
                split="train", sourceSplits=["tatqa:train"]
            )
        },
        seeds=[tatqa_projection],
        report=SimpleNamespace(
            selectedSourceCounts={"tatqa": 1}, exclusionCounts={"not-selected": 2}
        ),
    )
    monkeypatch.setattr(migration, "_load_parent", lambda _path: snapshot)
    monkeypatch.setattr(migration, "_validate_parent", lambda _snapshot: None)
    monkeypatch.setattr(migration, "_preserve_family_rights", lambda *_args: [])
    monkeypatch.setattr(
        migration,
        "_seeds_and_families",
        lambda *_args: (current_seeds, families, [], {}),
    )
    monkeypatch.setattr(migration, "build_projection_plan", lambda _path: projection)
    monkeypatch.setattr(migration, "derive_question_variants", lambda _record: [])
    return (
        parent,
        old_authored,
        derivative,
        current_banking,
        tatqa_projection,
        projection,
    )


def test_build_plan_retains_verified_bytes_and_marks_new_source_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, old_authored, derivative, current_banking, tatqa_projection, _ = _parent_fixture(
        tmp_path, monkeypatch
    )
    before = _snapshot(parent)

    plan = migration._build_plan(parent)

    entries = {entry.record.id: entry for entry in plan.entries}
    assert entries[old_authored.parent.id].record == old_authored.parent
    assert entries[old_authored.parent.id].provenance.evidenceStatus == "authored-reference"
    assert entries[derivative.id].record == derivative
    assert entries[derivative.id].provenance.evidenceStatus == "historical-accepted"
    assert entries[derivative.id].record.generation == derivative.generation
    projected = entries[current_banking.parent.id]
    assert projected.record == current_banking.parent
    assert projected.provenance.operation == "reprojected"
    assert projected.provenance.evidenceStatus == "source-annotation"
    assert projected.provenance.parentRecordIds == ["historical-banking77"]
    assert projected.provenance.sources[0].datasetId == "banking77"
    assert projected.provenance.sources[0].rightsSourceId == "native-banking77"
    addition = entries[tatqa_projection.parent.id]
    assert addition.provenance.operation == "source-projection"
    assert addition.provenance.evidenceStatus == "deterministic-computation"
    assert addition.provenance.sources[0].recordId == "tatqa.record.original"
    assert not plan.pending and not plan.review
    assert {source.id for source in plan.sources} == {
        "authored",
        "generated-authored",
        "native-banking77",
        "native-tatqa",
    }
    assert tatqa_projection.parent.familyId is not None
    assert plan.families[tatqa_projection.parent.familyId].split == "train"
    assert plan.projectionCounts == {"tatqa": 1, "excluded:not-selected": 2}
    assert _snapshot(parent) == before


def test_build_publish_load_and_repeat_are_offline_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, *_ = _parent_fixture(tmp_path, monkeypatch)
    plan = migration._build_plan(parent)
    destination = tmp_path / "migration"

    first = migration.publish_migration(destination, plan)
    first_bytes = _snapshot(destination)
    loaded = migration.load_migration(destination)
    second = migration.publish_migration(destination, plan)

    assert loaded == plan
    assert second == first
    assert _snapshot(destination) == first_bytes
    artifact = load_verified_artifact(Path(str(first["datasetPath"])))
    assert artifact.root.artifactId == first["artifactId"]
    report = cast(dict[str, object], load_object(destination / "migration-report.json"))
    assert report["operations"] == {
        "reprojected": 1,
        "retained": 4,
        "source-projection": 1,
    }
    annotations = [
        json.loads(line)
        for line in (destination / "source-annotations.jsonl").read_text().splitlines()
    ]
    assert {row["record"]["id"] for row in annotations} == {
        "banking77.record.original",
        "tatqa.record.original",
    }
    rights = cast(list[dict[str, object]], load_object(destination / "source-rights.json"))
    assert {source["id"] for source in rights} == {
        "authored",
        "generated-authored",
        "native-banking77",
        "native-tatqa",
    }


def test_load_rejects_changed_parent_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, *_ = _parent_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "migration"
    migration.publish_migration(destination, migration._build_plan(parent))
    (parent / "source-plan.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ModelError) as error:
        migration.load_migration(destination)

    assert error.value.code == "INTEGRITY_FAILED"


def test_build_plan_rejects_projection_rights_and_frozen_family_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, old_authored, *_, projection = _parent_fixture(tmp_path, monkeypatch)
    projection.sources = [_rights("authored", license_name="Conflicting-2.0")]
    projection.seeds = []
    projection.frozenFamilies = {}

    with pytest.raises(ModelError) as rights_error:
        migration._build_plan(parent)
    assert rights_error.value.code == "DATA_RIGHTS_DENIED"

    projection.sources = []
    assert old_authored.parent.familyId is not None
    projection.frozenFamilies = {
        old_authored.parent.familyId: FrozenFamilyAssignment(
            split="test", sourceSplits=["authored:test"]
        )
    }
    with pytest.raises(ModelError) as family_error:
        migration._build_plan(parent)
    assert family_error.value.code == "DATA_PARTITION_INVALID"


def test_deduplicate_keeps_exact_duplicate_but_rejects_conflict_and_leakage() -> None:
    first_seed = _seed("first")
    duplicate_seed = _seed("duplicate")
    duplicate_seed.parent.messages = first_seed.parent.messages
    duplicate_seed.parent.familyId = first_seed.parent.familyId
    duplicate_seed.parent.groupKeys = first_seed.parent.groupKeys
    first = _entry(first_seed)
    duplicate = _entry(duplicate_seed)

    kept, review = migration._deduplicate([first, duplicate])

    assert kept == [first]
    assert review[0].recordId == duplicate.record.id
    assert review[0].reason == "duplicate-derived-task"

    conflicting = duplicate.model_copy(deep=True)
    answer = DecisionOutput.model_validate_json(conflicting.record.messages[-1].content)
    assert answer.results[0].type == "predicate"
    answer.results[0].answer.value = "false"
    conflicting.record.messages[-1] = ChatMessage(
        role="assistant", content=answer.model_dump_json()
    )
    with pytest.raises(ModelError) as target_error:
        migration._deduplicate([first, conflicting])
    assert target_error.value.code == "DATA_RECORD_INVALID"

    leaked = duplicate.model_copy(deep=True)
    leaked.record.familyId = "family-other"
    with pytest.raises(ModelError) as leakage_error:
        migration._deduplicate([first, leaked])
    assert leakage_error.value.code == "LEAKAGE_DETECTED"


def test_migration_plan_requires_frozen_family_and_matching_source_rights(
    tmp_path: Path,
) -> None:
    seed = _seed("record")
    entry = _entry(seed)
    base = {
        "schemaVersion": 1,
        "recipeSha256": "a" * 64,
        "parentRun": str(tmp_path / "parent"),
        "parentArtifactId": "b" * 64,
        "parentFiles": {"source-plan.json": "c" * 64},
        "config": CurationConfig().model_dump(mode="json"),
        "model": _identity().model_dump(mode="json"),
        "sources": [_rights("authored").model_dump(mode="json")],
        "families": {
            seed.parent.familyId: FrozenFamilyAssignment(
                split="train", sourceSplits=["authored:train"]
            ).model_dump(mode="json")
        },
        "entries": [entry.model_dump(mode="json")],
        "pending": [],
        "review": [],
        "projectionCounts": {},
    }
    without_family = {**base, "families": {}}
    with pytest.raises(ValidationError, match="frozen family"):
        MigrationPlan.model_validate(without_family)
    wrong_rights = {**base, "sources": [_rights("other").model_dump(mode="json")]}
    with pytest.raises(ValidationError, match="source rights"):
        MigrationPlan.model_validate(wrong_rights)

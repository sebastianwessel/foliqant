from __future__ import annotations

import json
from pathlib import Path

import pytest

import foliqant_model.curation.projection_preparation as preparation
from foliqant_model.contracts import ChatMessage, DataRecord
from foliqant_model.contracts.base import canonical_digest
from foliqant_model.curation.contracts import (
    CurationConfig,
    ImportedRecord,
    SourceBatch,
    SourceSelection,
)
from foliqant_model.curation.decision_contracts import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceResult,
    Citation,
    DecisionDataSettings,
    DecisionInput,
    DecisionOption,
    DecisionOutput,
    DecisionSource,
    DecisionState,
    Explanation,
    MultiselectAnswer,
    MultiselectQuestion,
    MultiselectResult,
)
from foliqant_model.curation.decision_seeds import _projected_seed
from foliqant_model.curation.planning import freeze_sources
from foliqant_model.curation.source_projections import ProjectionResult
from foliqant_model.curation.sources import _load_catalog, source_catalog_digest
from foliqant_model.curation.storage import load_object, run_lock, store_object
from foliqant_model.errors import ModelError


def _source_row(
    source_id: str,
    index: int,
    *,
    family: str | None = None,
    tags: list[str] | None = None,
) -> ImportedRecord:
    record_id = f"{source_id}.record.{index:04d}"
    family_id = family or f"{source_id}.family.{index:04d}"
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id=record_id,
            sourceId=source_id,
            language="en",
            groupKeys=[f"{source_id}:group:{family_id}"],
            messages=[
                ChatMessage(role="system", content="Auxiliary source task."),
                ChatMessage(role="user", content=json.dumps({"input": record_id})),
                ChatMessage(role="assistant", content=json.dumps({"answer": record_id})),
            ],
            tags=tags or [],
            origin="human",
            reviewed=False,
            familyId=family_id,
        ),
        originalSplit="train",
        task="decision",
        originalId=f"original-{index:04d}",
    )


def _fake_projection(row: ImportedRecord) -> ProjectionResult:
    text = row.record.id
    task = DecisionInput(
        state=DecisionState(sources=[DecisionSource(id="source", kind="document", text=text)]),
        questions=[
            ChoiceQuestion(
                id="decision",
                type="choice",
                prompt=f"Choose for {text}",
                criteria=["Use the supplied source."],
                allowedSourceIds=["source"],
                options=[
                    DecisionOption(id="yes", description="Yes"),
                    DecisionOption(id="no", description="No"),
                ],
            )
        ],
    )
    oracle = DecisionOutput(
        results=[
            ChoiceResult(
                questionId="decision",
                type="choice",
                answerability=Answerability(status="answerable", issues=[]),
                answer=ChoiceAnswer(optionId="yes"),
                explanation=Explanation(
                    summary="The supplied source selects yes.",
                    evidence=[Citation(sourceId="source", quote=text)],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    seed = _projected_seed(row, task, oracle)
    seed = seed.model_copy(
        update={
            "parent": seed.parent.model_copy(
                update={
                    "id": "native-"
                    + row.record.sourceId
                    + ".record."
                    + canonical_digest(row.record.id)[:32],
                    "tags": sorted(set(seed.parent.tags) | set(row.record.tags)),
                }
            )
        }
    )
    return ProjectionResult(seed=seed, reason="projected")


def _conflicting_projection(row: ImportedRecord) -> ProjectionResult:
    if "conflict" not in row.record.tags:
        return _fake_projection(row)
    text = "The same answer-bearing source."
    task = DecisionInput(
        state=DecisionState(sources=[DecisionSource(id="source", kind="document", text=text)]),
        questions=[
            ChoiceQuestion(
                id="decision",
                type="choice",
                prompt="Choose for the shared task.",
                criteria=["Use the supplied source."],
                allowedSourceIds=["source"],
                options=[
                    DecisionOption(id="yes", description="Yes"),
                    DecisionOption(id="no", description="No"),
                ],
            )
        ],
    )
    answer = "yes" if row.record.id.endswith("0000") else "no"
    oracle = DecisionOutput(
        results=[
            ChoiceResult(
                questionId="decision",
                type="choice",
                answerability=Answerability(status="answerable", issues=[]),
                answer=ChoiceAnswer(optionId=answer),
                explanation=Explanation(
                    summary=f"The supplied source selects {answer}.",
                    evidence=[Citation(sourceId="source", quote=text)],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    return ProjectionResult(seed=_projected_seed(row, task, oracle), reason="projected")


def _multi_intent_projection(row: ImportedRecord) -> ProjectionResult:
    text = row.record.id
    task = DecisionInput(
        state=DecisionState(sources=[DecisionSource(id="source", kind="document", text=text)]),
        questions=[
            MultiselectQuestion(
                id="intents",
                type="multiselect",
                prompt=f"Select intents for {text}",
                criteria=["Return every active intent."],
                allowedSourceIds=["source"],
                options=[
                    DecisionOption(id="first", description="First"),
                    DecisionOption(id="second", description="Second"),
                ],
                minSelections=1,
                maxSelections=2,
            )
        ],
    )
    oracle = DecisionOutput(
        results=[
            MultiselectResult(
                questionId="intents",
                type="multiselect",
                answerability=Answerability(status="answerable", issues=[]),
                answer=MultiselectAnswer(optionIds=["first", "second"]),
                explanation=Explanation(
                    summary="Both intents are active.",
                    evidence=[Citation(sourceId="source", quote=text)],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    seed = _projected_seed(row, task, oracle)
    seed = seed.model_copy(
        update={
            "parent": seed.parent.model_copy(
                update={"tags": sorted(set(seed.parent.tags) | set(row.record.tags))}
            )
        }
    )
    return ProjectionResult(seed=seed, reason="projected")


def _write_parent(
    tmp_path: Path,
    rows: dict[str, list[ImportedRecord]],
    *,
    cap: int = 500,
) -> Path:
    parent = tmp_path / "workspace" / "curation" / "parent-run"
    catalog = _load_catalog()
    config = CurationConfig(
        name="projection-test",
        sources=[
            SourceSelection(id=source_id, maxRecords=max(4, len(values)))
            for source_id, values in rows.items()
        ],
        decisionData=DecisionDataSettings(sourceExamplesPerSource=cap),
        seed=17,
    )
    batches: list[SourceBatch] = []
    for source_id, records in rows.items():
        entry = next(value for key, value in catalog.sources.items() if key == source_id)
        batches.append(
            SourceBatch(
                source=entry.source,
                revision=entry.revision,
                assets=entry.assets,
                records=records,
                totalAvailable=len(records),
            )
        )
    config_digest = canonical_digest(config.model_dump(mode="json"))
    plan = freeze_sources(
        batches,
        seed=config.seed,
        configuration_digest=config_digest,
        catalog_digest=source_catalog_digest(),
    )
    store_object(parent / "configuration.json", config.model_dump(mode="json"))
    store_object(parent / "source-plan.json", plan.model_dump(mode="json"))
    store_object(parent / "native-seeds.json", [])
    store_object(parent / "native-families.json", {})
    for batch in batches:
        store_object(
            parent / "sources" / f"{batch.source.id}.json",
            batch.model_dump(mode="json"),
        )
    return parent


def _input_bytes(parent: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(parent)): path.read_bytes() for path in sorted(parent.rglob("*.json"))
    }


def test_prepare_is_offline_deterministic_and_does_not_lock_or_mutate_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _write_parent(
        tmp_path,
        {"typed-decisions": [_source_row("typed-decisions", index) for index in range(8)]},
        cap=2,
    )
    before = _input_bytes(parent)
    monkeypatch.setattr(preparation, "project_auxiliary", _fake_projection)
    monkeypatch.setattr(
        preparation,
        "projection_priority",
        lambda row: (0 if "multi" in row.record.tags else 1, row.record.id),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline preparation attempted external work")

    monkeypatch.setattr("foliqant_model.acquisition.download_asset", forbidden)
    monkeypatch.setattr("foliqant_model.curation.endpoint.discover_models", forbidden)
    with run_lock(parent):
        first = preparation.prepare_source_projections(parent)
    second = preparation.prepare_source_projections(parent)

    assert first == second
    assert first.rawRecords == 8
    assert first.eligibleRecords == 8
    assert first.selectedRecords == 2
    assert _input_bytes(parent) == before
    plan = preparation.load_projection_plan(Path(first.planPath))
    assert plan.report.questionTypeCounts == {"choice": 8}
    assert plan.report.selectedQuestionTypeCounts == {"choice": 2}
    assert sum(plan.report.selectedSplitCounts.values()) == 2
    assert plan.report.exclusionCounts == {"over-limit": 6}


def test_multi_intent_priority_precedes_seeded_hash_and_output_cannot_overlap_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [_source_row("multidogo-finance", index) for index in range(8)]
    rows[-1] = rows[-1].model_copy(
        update={"record": rows[-1].record.model_copy(update={"tags": ["multi"]})}
    )
    parent = _write_parent(tmp_path, {"multidogo-finance": rows}, cap=1)
    monkeypatch.setattr(preparation, "project_auxiliary", _fake_projection)
    monkeypatch.setattr(
        preparation,
        "projection_priority",
        lambda row: (0 if "multi" in row.record.tags else 1, row.record.id),
    )

    plan = preparation.build_projection_plan(parent)
    assert len(plan.seeds) == 1
    assert "original-source-record:multidogo-finance.record.0007" in plan.seeds[0].parent.tags
    with pytest.raises(ModelError, match="overlap"):
        preparation.prepare_source_projections(parent, parent / "projection")


def test_tatqa_selects_lowest_record_id_per_frozen_family_and_counts_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _source_row("tatqa", 0, family="tatqa.family.shared"),
        _source_row("tatqa", 1, family="tatqa.family.shared"),
        *[_source_row("tatqa", index) for index in range(2, 6)],
    ]
    parent = _write_parent(tmp_path, {"tatqa": rows})
    monkeypatch.setattr(preparation, "project_auxiliary", _fake_projection)

    plan = preparation.build_projection_plan(parent)
    selected_originals = {
        tag.removeprefix("original-source-record:")
        for seed in plan.seeds
        for tag in seed.parent.tags
        if tag.startswith("original-source-record:")
    }
    assert "tatqa.record.0000" in selected_originals
    assert "tatqa.record.0001" not in selected_originals
    assert plan.report.exclusionCounts == {"tatqa-family-sibling": 1}
    assert plan.report.selectedFamilies == 5


def test_conflicting_answer_bearing_group_excludes_every_member_before_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _source_row(
            "multidogo-finance",
            0,
            family="multidogo-finance.family.conflict",
            tags=["conflict", "multi"],
        ),
        _source_row(
            "multidogo-finance",
            1,
            family="multidogo-finance.family.conflict",
            tags=["conflict", "multi"],
        ),
        *[_source_row("multidogo-finance", index) for index in range(2, 6)],
    ]
    parent = _write_parent(tmp_path, {"multidogo-finance": rows}, cap=5)
    monkeypatch.setattr(preparation, "project_auxiliary", _conflicting_projection)
    monkeypatch.setattr(
        preparation,
        "projection_priority",
        lambda row: (0 if "multi" in row.record.tags else 1, row.record.id),
    )

    plan = preparation.build_projection_plan(parent)
    selected_originals = {
        tag.removeprefix("original-source-record:")
        for seed in plan.seeds
        for tag in seed.parent.tags
        if tag.startswith("original-source-record:")
    }
    assert "multidogo-finance.record.0000" not in selected_originals
    assert "multidogo-finance.record.0001" not in selected_originals
    assert plan.report.selectedRecords == 4
    assert plan.report.exclusionCounts == {"conflicting-target": 2}


def test_corrupt_plan_and_changed_parent_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _write_parent(
        tmp_path,
        {"typed-decisions": [_source_row("typed-decisions", index) for index in range(6)]},
    )
    monkeypatch.setattr(preparation, "project_auxiliary", _fake_projection)
    result = preparation.prepare_source_projections(parent)
    plan_path = Path(result.planPath)
    plan_path.write_bytes(plan_path.read_bytes().replace(b'"full"', b'"pilot"'))
    with pytest.raises(ModelError, match="checksum"):
        preparation.load_projection_plan(plan_path)

    other = preparation.build_projection_plan(parent)
    config_path = parent / "configuration.json"
    envelope = load_object(config_path)
    assert isinstance(envelope, dict)
    changed = dict(envelope)
    changed["seed"] = 18
    config_path.unlink()
    store_object(config_path, changed)
    with pytest.raises(ModelError):
        preparation.prepare_source_projections(parent, tmp_path / "changed-output")
    assert other.parentInputsSha256 != canonical_digest(
        preparation._hash_parent_files(parent, ["typed-decisions"])
    )


def test_parent_and_output_symlinks_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _write_parent(
        tmp_path,
        {"typed-decisions": [_source_row("typed-decisions", index) for index in range(4)]},
    )
    monkeypatch.setattr(preparation, "project_auxiliary", _fake_projection)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(parent, target_is_directory=True)
    with pytest.raises(ModelError, match="symlink"):
        preparation.build_projection_plan(parent_link)

    output_target = tmp_path / "output-target"
    output_target.mkdir()
    output_link = tmp_path / "output-link"
    output_link.symlink_to(output_target, target_is_directory=True)
    with pytest.raises(ModelError, match="symlink"):
        preparation.prepare_source_projections(parent, output_link)


def test_pilot_selects_exact_training_quotas_and_typed_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows = preparation._PILOT_TYPED_WORKFLOWS
    rows = {
        "typed-decisions": [
            _source_row("typed-decisions", index, tags=[workflows[index % 4]])
            for index in range(80)
        ],
        "multidogo-finance": [
            _source_row("multidogo-finance", index, tags=["multi"] if index < 16 else [])
            for index in range(24)
        ],
        "tatqa": [_source_row("tatqa", index) for index in range(24)],
    }
    parent = _write_parent(tmp_path, rows)
    monkeypatch.setattr(
        preparation,
        "project_auxiliary",
        lambda row: (
            _multi_intent_projection(row)
            if row.record.sourceId == "multidogo-finance" and "multi" in row.record.tags
            else _fake_projection(row)
        ),
    )
    monkeypatch.setattr(
        preparation,
        "projection_priority",
        lambda row: (0 if "multi" in row.record.tags else 1, row.record.id),
    )

    plan = preparation.build_projection_plan(parent, pilot=True)
    assert plan.selection == "pilot"
    assert plan.report.selectedRecords == 32
    assert plan.report.selectedSourceCounts == {
        "multidogo-finance": 8,
        "tatqa": 8,
        "typed-decisions": 16,
    }
    multidogo = plan.report.sourceCounts["multidogo-finance"]
    assert multidogo.eligibleMultiIntentRecords >= 8
    assert multidogo.selectedMultiIntentRecords == 8
    assert all(value.split == "train" for value in plan.frozenFamilies.values())
    assert {
        workflow: sum(workflow in seed.parent.tags for seed in plan.seeds) for workflow in workflows
    } == {workflow: 4 for workflow in workflows}

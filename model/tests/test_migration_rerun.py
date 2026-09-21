"""Pending-only migration reruns preserve data, traces, rights and resume boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from foliqant_decisions import DecisionInput, DecisionOutput

from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import (
    ChatMessage,
    DataRecord,
    FrozenFamilyAssignment,
    ResolvedSourceDeclaration,
)
from foliqant_model.curation import generation
from foliqant_model.curation import migration_rerun as rerun
from foliqant_model.curation.contracts import CurationConfig, GenerationSettings
from foliqant_model.curation.decision_seeds import DecisionSeed
from foliqant_model.curation.endpoint import EndpointModelIdentity, GenerationResponse
from foliqant_model.curation.migration_contracts import (
    MigrationEntry,
    MigrationPending,
    MigrationPlan,
    MigrationProvenance,
    MigrationReview,
    MigrationSource,
)
from foliqant_model.curation.runtime import CurationControl
from foliqant_model.curation.storage import load_object, store_object
from foliqant_model.errors import ModelError


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _seed(record_id: str, *, mode: Literal["annotate", "rewrite"] = "annotate") -> DecisionSeed:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 1,
            "state": {
                "sources": [
                    {
                        "id": "message-1",
                        "kind": "message",
                        "text": f"The receipt for request {record_id} was requested.",
                    }
                ]
            },
            "questions": [
                {
                    "id": "receipt",
                    "type": "predicate",
                    "prompt": "Was a receipt requested?",
                    "criteria": ["Use an explicit receipt request."],
                    "allowedSourceIds": ["message-1"],
                }
            ],
        }
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
                        "evidence": [
                            {"sourceId": "message-1", "quote": task.state.sources[0].text}
                        ],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ],
        }
    )
    return DecisionSeed(
        parent=DataRecord(
            schemaVersion=1,
            id=record_id,
            sourceId="authored",
            language="en",
            groupKeys=["family-" + record_id],
            familyId="family-" + record_id,
            messages=[
                ChatMessage(role="system", content="Answer typed questions from evidence."),
                ChatMessage(role="user", content=task.model_dump_json()),
                ChatMessage(role="assistant", content=output.model_dump_json()),
            ],
            origin="synthetic",
            reviewed=False,
        ),
        scenario="receipt-request",
        mode=mode,
        rewriteSourceIds=["message-1"] if mode == "rewrite" else [],
    )


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="saved-model",
        modelType="llm",
        publisher=None,
        architecture=None,
        format=None,
        quantization=None,
        sizeBytes=None,
        maxContextLength=None,
        metadataSha256="a" * 64,
    )


def _link(seed: DecisionSeed) -> MigrationSource:
    return MigrationSource(
        datasetId="authored",
        recordId=seed.parent.id,
        originalId=seed.parent.id,
        snapshotSha256="b" * 64,
        rightsSourceId="authored",
    )


def _plan(tmp_path: Path, *, rewrite: bool = False, count: int = 2) -> MigrationPlan:
    accepted = _seed("accepted")
    pending = [
        _seed("pending-" + chr(97 + index), mode="rewrite" if rewrite else "annotate")
        for index in range(count)
    ]
    return MigrationPlan(
        schemaVersion=1,
        recipeSha256="c" * 64,
        parentRun=str(tmp_path / "original"),
        parentArtifactId="d" * 64,
        parentFiles={"config.json": "e" * 64},
        config=CurationConfig(generation=GenerationSettings(maxAttempts=3)),
        model=_identity(),
        sources=[
            ResolvedSourceDeclaration(
                id="authored",
                license="Research only",
                licenseEvidence="Pinned test rights evidence",
                trainingAllowed=True,
                sharedTrainingAllowed=True,
                redistributionAllowed=False,
                commercialUse="restricted",
                attribution="Test source author",
                privacy="public",
                restrictions=["Preserve research restrictions"],
            )
        ],
        families={
            seed.parent.familyId: FrozenFamilyAssignment(
                split="train", sourceSplits=["authored:train"]
            )
            for seed in [accepted, *pending]
        },
        entries=[
            MigrationEntry(
                record=accepted.parent,
                provenance=MigrationProvenance(
                    recordId=accepted.parent.id,
                    operation="retained",
                    evidenceStatus="historical-accepted",
                    parentRecordIds=[],
                    sources=[_link(accepted)],
                    rule="retain-exact-parent-v1",
                ),
            )
        ],
        pending=[
            MigrationPending(seed=seed, reason="pending-validation", sources=[_link(seed)])
            for seed in pending
        ],
        review=[MigrationReview(recordId="review-only", reason="disputed-reference")],
        projectionCounts={"authored": count + 1},
    )


def _install_publication(
    monkeypatch: pytest.MonkeyPatch, parent: Path, plan: MigrationPlan
) -> list[MigrationPlan]:
    """Isolate rerun orchestration; the migration module owns artifact verification tests."""
    store_object(parent / "migration-plan.json", plan.model_dump(mode="json"))
    published: list[MigrationPlan] = []

    def load(path: Path) -> MigrationPlan:
        return MigrationPlan.model_validate(load_object(path / "migration-plan.json"), strict=True)

    def publish(path: Path, child: MigrationPlan) -> dict[str, object]:
        ids = {entry.record.id for entry in child.entries}
        for entry in child.entries:
            if entry.record.generation is not None:
                assert set(entry.record.generation.parentRecordIds) <= ids
        store_object(path / "migration-plan.json", child.model_dump(mode="json"))
        published.append(child)
        return {
            "command": "migrate-decisions",
            "migrationPath": str(path),
            "datasetPath": str(path / "datasets" / "native-decisions"),
            "artifactId": canonical_digest(child.model_dump(mode="json")),
            "reportPath": str(path / "migration-report.json"),
            "records": len(child.entries),
            "pendingTasks": len(child.pending),
            "reviewItems": len(child.review),
        }

    monkeypatch.setattr(rerun, "load_migration", load)
    monkeypatch.setattr(rerun, "publish_migration", publish)
    monkeypatch.setattr(rerun, "migration_recipe_digest", lambda: "c" * 64)
    return published


def _endpoint(
    monkeypatch: pytest.MonkeyPatch,
    outputs: list[dict[str, object] | ModelError],
    *,
    pause: CurationControl | None = None,
) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    def generate(config, **kwargs):  # type: ignore[no-untyped-def]
        requests.append(kwargs)
        assert kwargs["observed_identity"] == _identity()
        assert outputs, "Unexpected extra model request"
        output = outputs.pop(0)
        if isinstance(output, ModelError):
            raise output
        if pause is not None:
            pause._handle_sigint(2, None)
        return GenerationResponse(
            model=kwargs["observed_identity"],
            output=output,
            finishReason="stop",
            requestSha256=generation._generation_request_sha256(
                config,
                model_id=kwargs["model_id"],
                messages=kwargs["messages"],
                schema=kwargs["schema"],
                seed=kwargs["seed"],
            ),
            schemaSha256=canonical_digest(kwargs["schema"]),
            rawResponseSha256=canonical_digest(output),
            finalAssistantResponse=_json(output),
            elapsedSeconds=0.1,
        )

    monkeypatch.setattr(generation, "generate_json", generate)
    return requests


def _output(
    seed: DecisionSeed, *, value: str = "true", text: str | None = None
) -> dict[str, object]:
    payload = seed.oracle.model_dump(mode="json")
    payload["results"][0]["answer"]["value"] = value
    if text is not None:
        payload["results"][0]["explanation"]["evidence"][0]["quote"] = text
    return payload


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes() for item in path.rglob("*") if item.is_file()
    }


def test_only_selected_pending_tasks_run_and_preserve_existing_entries_and_rights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, plan)
    before = _snapshot(parent)
    calls = _endpoint(monkeypatch, [_output(plan.pending[0].seed)])
    result = rerun.rerun_migration(parent, limit=1, job_ids=["pending-b", "pending-a"])
    child = published[-1]
    assert result["command"] == "rerun-migrated-decisions"
    assert set(result) == {
        "command",
        "migrationPath",
        "datasetPath",
        "artifactId",
        "reportPath",
        "records",
        "pendingTasks",
        "reviewItems",
    }
    assert len(calls) == 1
    assert child.entries[0] == plan.entries[0]
    assert child.entries[1].record == plan.pending[0].seed.parent
    assert child.entries[1].provenance.sources == plan.pending[0].sources
    assert child.entries[1].provenance.evidenceStatus == "model-verified"
    assert child.pending == [plan.pending[1]] and child.review == plan.review
    assert child.sources == plan.sources and child.families == plan.families
    assert child.parentFiles == plan.parentFiles and child.parentArtifactId == plan.parentArtifactId
    assert before == _snapshot(parent)
    run = Path(str(result["migrationPath"]))
    assert run.parent == parent.parent / "reruns"
    receipt = load_object(run / "rerun-plan.json")
    assert receipt["parentPlanSha256"] == canonical_digest(plan.model_dump(mode="json"))
    assert receipt["selectedRecordIds"] == ["pending-a"]


@pytest.mark.parametrize(
    "ids", [["accepted"], ["review-only"], ["unknown"], ["pending-a", "pending-a"], []]
)
def test_rerun_refuses_nonpending_or_duplicate_selection_without_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ids: list[str]
) -> None:
    parent = tmp_path / "migration"
    _install_publication(monkeypatch, parent, _plan(tmp_path))
    calls = _endpoint(monkeypatch, [])
    with pytest.raises(ModelError) as error:
        rerun.rerun_migration(parent, job_ids=ids)
    assert error.value.code == "ARGUMENT_INVALID" and not calls
    assert not (tmp_path / "reruns").exists()


def test_completed_quarantines_move_to_review_and_same_selection_never_calls_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, count=3)
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, plan)
    calls = _endpoint(
        monkeypatch, [_output(plan.pending[0].seed), _output(plan.pending[1].seed, value="false")]
    )
    first = rerun.rerun_migration(parent, job_ids=["pending-b", "pending-a"])
    run = Path(str(first["migrationPath"]))
    before = _snapshot(run)
    assert rerun.rerun_migration(parent, job_ids=["pending-a", "pending-b"]) == first
    assert len(calls) == 2 and before == _snapshot(run)
    child = published[-1]
    assert child.pending == [plan.pending[2]]
    assert child.review[-1].recordId == "pending-b"
    assert child.review[-1].reason == "solver-semantic-mismatch"
    assert child.review[-1].previousJobId is not None
    assert len(list((run / "outcomes").glob("*.json"))) == 2
    with pytest.raises(ModelError, match="uniquely select pending"):
        rerun.rerun_migration(run, job_ids=["pending-b"])
    assert len(calls) == 2


@pytest.mark.parametrize(
    "code", ["NETWORK_FAILED", "BACKEND_FAILED", "INTEGRITY_FAILED", "TIMEOUT"]
)
def test_fatal_failure_never_retries_or_fabricates_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, _plan(tmp_path, count=1))
    calls = _endpoint(monkeypatch, [ModelError(code, "Safe failure")])  # type: ignore[arg-type]
    run = tmp_path / "child"
    with pytest.raises(ModelError) as error:
        rerun.rerun_migration(parent, run)
    assert error.value.code == code
    assert len(calls) == 1 and not published
    assert not list((run / "outcomes").glob("*.json"))
    assert not (run / "migration-plan.json").exists()


def test_partial_failure_resumes_without_regenerating_completed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, plan)
    calls = _endpoint(
        monkeypatch,
        [
            _output(plan.pending[0].seed),
            ModelError("TIMEOUT", "deadline"),
            _output(plan.pending[1].seed),
        ],
    )
    run = tmp_path / "child"
    with pytest.raises(ModelError, match="deadline"):
        rerun.rerun_migration(parent, run)
    completed = next((run / "outcomes").glob("*.json"))
    before = completed.read_bytes()
    assert not published and len(calls) == 2
    result = rerun.rerun_migration(parent, run)
    assert len(calls) == 3 and completed.read_bytes() == before
    assert result["pendingTasks"] == 0 and result["records"] == 3


def test_rewrite_success_keeps_seed_ancestry_source_rights_and_repair_traces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, rewrite=True, count=1)
    item = plan.pending[0]
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, plan)
    rewritten = "A receipt was requested for request pending-a."
    payload = {"sources": [{"id": "message-1", "kind": "message", "text": rewritten}]}
    calls = _endpoint(
        monkeypatch,
        [
            payload,
            _output(item.seed, value="unknown", text=rewritten),
            _output(item.seed, text=rewritten),
        ],
    )
    result = rerun.rerun_migration(parent)
    assert len(calls) == 3
    child = published[-1]
    assert child.entries[0] == plan.entries[0]
    assert child.entries[1].record == item.seed.parent
    generated = child.entries[2].record
    assert generated.generation is not None and generated.generation.parentRecordIds == [
        item.seed.parent.id
    ]
    assert generated.sourceId == "generated-authored"
    assert child.sources[0] == plan.sources[0]
    assert child.sources[-1].commercialUse == "restricted"
    assert set(plan.sources[0].restrictions) <= set(child.sources[-1].restrictions)
    run = Path(str(result["migrationPath"]))
    outcome = load_object(next((run / "outcomes").glob("*.json")))
    assert [[call["phase"] for call in trace["calls"]] for trace in outcome["attemptTrace"]] == [
        ["rewrite", "solver"],
        ["solver"],
    ]
    assert len(list((run / "requests" / "calls").glob("*.json"))) == 3
    assert rerun.rerun_migration(parent) == result and len(calls) == 3


def test_graceful_pause_persists_outcome_then_resume_publishes_without_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, count=1)
    parent = tmp_path / "migration"
    published = _install_publication(monkeypatch, parent, plan)
    control = CurationControl()
    calls = _endpoint(monkeypatch, [_output(plan.pending[0].seed)], pause=control)
    run = tmp_path / "child"
    with pytest.raises(ModelError) as error:
        rerun.rerun_migration(parent, run, control=control)
    assert error.value.code == "INTERRUPTED" and not published
    assert len(list((run / "outcomes").glob("*.json"))) == 1
    result = rerun.rerun_migration(parent, run)
    assert result["pendingTasks"] == 0 and len(calls) == 1


@pytest.mark.parametrize("target", ["parent", "outcome", "cache"])
def test_rerun_detects_tampering_before_any_additional_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    plan = _plan(tmp_path, count=1)
    parent = tmp_path / "migration"
    _install_publication(monkeypatch, parent, plan)
    calls = _endpoint(monkeypatch, [_output(plan.pending[0].seed)])
    run = tmp_path / "child"
    rerun.rerun_migration(parent, run)
    path = {
        "parent": parent / "migration-plan.json",
        "outcome": next((run / "outcomes").glob("*.json")),
        "cache": next((run / "requests" / "calls").glob("*.json")),
    }[target]
    envelope = json.loads(path.read_text())
    if target == "outcome":
        envelope["payload"]["jobId"] = "f" * 64
        envelope["sha256"] = canonical_digest(envelope["payload"])
    elif target == "cache":
        envelope["payload"]["response"]["model"]["modelId"] = "other-model"
        envelope["sha256"] = canonical_digest(envelope["payload"])
    else:
        envelope["payload"]["parentArtifactId"] = "f" * 64
    path.write_text(_json(envelope))
    with pytest.raises(ModelError) as error:
        rerun.rerun_migration(parent, run)
    assert error.value.code == "INTEGRITY_FAILED" and len(calls) == 1


def test_changed_selection_cannot_reuse_explicit_child_or_overlap_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path)
    parent = tmp_path / "migration"
    _install_publication(monkeypatch, parent, plan)
    calls = _endpoint(monkeypatch, [_output(plan.pending[0].seed)])
    run = tmp_path / "child"
    rerun.rerun_migration(parent, run, limit=1)
    with pytest.raises(ModelError) as changed:
        rerun.rerun_migration(parent, run, job_ids=["pending-b"])
    assert changed.value.code == "INTEGRITY_FAILED" and len(calls) == 1
    with pytest.raises(ModelError, match="separate from its parent"):
        rerun.rerun_migration(parent, parent / "nested")
    assert len(calls) == 1


def test_real_migration_publication_keeps_rerun_receipt_and_ancestry_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_model.artifacts import load_verified_artifact
    from foliqant_model.curation import migration
    from foliqant_model.curation.runner import _publish

    plan = _plan(tmp_path, rewrite=True, count=1)
    for split in ("validation", "calibration", "test"):
        heldout = _seed("heldout-" + split)
        plan.entries.append(
            MigrationEntry(
                record=heldout.parent,
                provenance=MigrationProvenance(
                    recordId=heldout.parent.id,
                    operation="retained",
                    evidenceStatus="authored-reference",
                    parentRecordIds=[],
                    sources=[_link(heldout)],
                    rule="retain-heldout-reference-v1",
                ),
            )
        )
        plan.families[heldout.parent.familyId] = FrozenFamilyAssignment(
            split=split, sourceSplits=["authored:" + split]
        )
    original = Path(plan.parentRun)
    rows = [entry.record for entry in plan.entries] + [plan.pending[0].seed.parent]
    store_object(
        original / "source-plan.json",
        {
            "records": [
                {"record": record.model_dump(mode="json"), "originalId": record.id}
                for record in rows
            ]
        },
    )
    artifact = _publish(
        original,
        "native-decisions",
        [entry.record for entry in plan.entries],
        plan.sources,
        dict(
            sorted(
                {
                    entry.record.familyId: plan.families[entry.record.familyId]
                    for entry in plan.entries
                }.items()
            )
        ),
        plan.config.seed,
    )
    original_files = migration._inventory(original)
    plan = plan.model_copy(
        deep=True,
        update={
            "parentArtifactId": artifact.root.artifactId,
            "parentFiles": original_files,
            "recipeSha256": migration.migration_recipe_digest(),
        },
    )
    for entry in plan.entries:
        for source in entry.provenance.sources:
            source.snapshotSha256 = original_files["source-plan.json"]
    for pending in plan.pending:
        for source in pending.sources:
            source.snapshotSha256 = original_files["source-plan.json"]
    parent = tmp_path / "migration"
    migration.publish_migration(parent, plan)
    before = _snapshot(parent)
    rewritten = "A receipt was requested for request pending-a."
    calls = _endpoint(
        monkeypatch,
        [
            {"sources": [{"id": "message-1", "kind": "message", "text": rewritten}]},
            _output(plan.pending[0].seed, text=rewritten),
        ],
    )
    result = rerun.rerun_migration(parent)
    child = Path(str(result["migrationPath"]))
    validated = migration.load_migration(child)
    assert validated.entries[0] == plan.entries[0]
    assert len(validated.entries) == 6 and not validated.pending
    completed = load_object(child / "migration-complete.json")
    assert "rerun-plan.json" in completed["files"] and "rerun-report.json" in completed["files"]
    artifact = load_verified_artifact(Path(str(result["datasetPath"])))
    assert artifact.root.artifactId == result["artifactId"]
    child_before = _snapshot(child)
    assert rerun.rerun_migration(parent) == result
    assert len(calls) == 2 and before == _snapshot(parent)
    assert child_before == _snapshot(child)


def test_changed_migration_recipe_refuses_frozen_task_reuse_before_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "migration"
    _install_publication(monkeypatch, parent, _plan(tmp_path))
    calls = _endpoint(monkeypatch, [])
    monkeypatch.setattr(rerun, "migration_recipe_digest", lambda: "f" * 64)
    with pytest.raises(ModelError, match="prepare a new migration") as error:
        rerun.rerun_migration(parent)
    assert error.value.code == "CONFIG_INVALID" and not calls
    assert not (tmp_path / "reruns").exists()


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_rerun_limit_is_rejected_before_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    parent = tmp_path / "migration"
    _install_publication(monkeypatch, parent, _plan(tmp_path))
    calls = _endpoint(monkeypatch, [])
    with pytest.raises(ModelError, match="positive integer"):
        rerun.rerun_migration(parent, limit=limit)
    assert not calls

"""End-to-end tests for resumable curation orchestration and publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts.artifacts import DatasetParent
from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import (
    ChatMessage,
    DataRecord,
    GenerationProvenance,
    ResolvedSourceDeclaration,
)
from foliqant_model.curation import generation, runner, sources
from foliqant_model.curation.contracts import (
    CandidateJob,
    CandidateOutcome,
    ImportedRecord,
    SourceBatch,
)
from foliqant_model.curation.endpoint import EndpointModelIdentity
from foliqant_model.errors import ModelError


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="local-test-model",
        modelType="llm",
        publisher="local",
        architecture="test",
        format="gguf",
        quantization="q4",
        sizeBytes=1024,
        maxContextLength=8192,
        metadataSha256="a" * 64,
        immutableRevision=None,
        structuredOutput="unknown",
    )


def _rights(source_id: str, *, restriction: str = "test-only") -> ResolvedSourceDeclaration:
    return ResolvedSourceDeclaration(
        id=source_id,
        license=f"license-{source_id}",
        licenseEvidence=f"evidence-{source_id}",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="unknown",
        attribution=f"attribution-{source_id}",
        restrictions=[restriction],
    )


def _row(
    record_id: str,
    split: str,
    *,
    source_id: str = "source-one",
    prompt: str | None = None,
    answer: str | None = None,
) -> ImportedRecord:
    record = DataRecord(
        schemaVersion=1,
        id=record_id,
        sourceId=source_id,
        language="en",
        groupKeys=["group-" + record_id],
        messages=[
            ChatMessage(role="user", content=prompt or "Input for " + record_id),
            ChatMessage(role="assistant", content=answer or "Answer for " + record_id),
        ],
        tags=["test-source"],
        origin="human",
        reviewed=False,
    )
    return ImportedRecord(
        record=record,
        originalSplit=split,  # type: ignore[arg-type]
        task="decision",
        originalId="original-" + record_id,
    )


def _batch(source_id: str, rows: list[ImportedRecord]) -> SourceBatch:
    return SourceBatch(
        source=_rights(source_id),
        revision="revision-1",
        assets=[],
        records=rows,
        totalAvailable=len(rows),
    )


def _ordinary_batches() -> list[SourceBatch]:
    return [
        _batch(
            "source-one",
            [
                _row("train-a", "train"),
                _row("train-b", "train"),
                _row("validation-a", "validation"),
                _row("calibration-a", "calibration"),
                _row("test-a", "test"),
            ],
        )
    ]


def _write_config(
    path: Path,
    *,
    sources_: list[str] | None = None,
    candidates: int = 4,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "runner-test",
                "sources": [
                    {"id": source_id, "maxRecords": 10}
                    for source_id in (sources_ or ["source-one"])
                ],
                "endpoint": {
                    "baseUrl": "http://127.0.0.1:1234/v1",
                    "model": "local-test-model",
                    "timeoutSeconds": 10,
                    "maxTokens": 1024,
                    "temperature": 0.0,
                    "maxResponseBytes": 1048576,
                },
                "generation": {
                    "maxCandidates": candidates,
                    "maxAttempts": 1,
                    "languages": ["en"],
                    "scenarios": [
                        "withdrawn-request",
                        "multiple-intents",
                        "missing-evidence",
                        "conflicting-information",
                        "changed-deadline",
                    ],
                    "scenarioFamilies": 4,
                    "maxInputCharacters": 16000,
                },
                "seed": 42,
            }
        ),
        encoding="utf-8",
    )


def _install_runner_inputs(
    monkeypatch: pytest.MonkeyPatch,
    batches: list[SourceBatch],
) -> EndpointModelIdentity:
    identity = _identity()
    monkeypatch.setattr(runner, "check_workspace_git_policy", lambda _path: None)
    monkeypatch.setattr(runner, "_source_batches", lambda *_args: batches)
    monkeypatch.setattr(sources, "source_catalog_digest", lambda: "b" * 64)
    monkeypatch.setattr(runner, "discover_models", lambda _config: [identity])
    monkeypatch.setattr(runner, "_select_model", lambda _config, _models: identity)
    return identity


def _accepted_outcome(
    identity: EndpointModelIdentity, parent: DataRecord, job: CandidateJob
) -> CandidateOutcome:
    assert parent.familyId is not None
    request = canonical_digest({"job": job.jobId, "kind": "request"})
    generation_provenance = GenerationProvenance(
        provider="openai-compatible",
        modelId=identity.modelId,
        modelIdentitySha256=identity.metadataSha256,
        promptSha256=canonical_digest({"job": job.jobId, "kind": "prompt"}),
        parametersSha256=canonical_digest({"job": job.jobId, "kind": "parameters"}),
        requestSha256=request,
        parentRecordIds=[parent.id],
    )
    systems = [message for message in parent.messages[:-1] if message.role == "system"]
    record = DataRecord(
        schemaVersion=1,
        id="generated-" + job.jobId,
        sourceId="generated-" + parent.sourceId,
        language=job.language,
        groupKeys=parent.groupKeys,
        messages=[
            *systems,
            ChatMessage(role="user", content="Generated input for " + job.jobId),
            ChatMessage(role="assistant", content=parent.messages[-1].content),
        ],
        tags=sorted(set(parent.tags + ["generated-test"])),
        origin="teacher",
        reviewed=False,
        familyId=parent.familyId,
        generation=generation_provenance,
    )
    return CandidateOutcome(
        jobId=job.jobId,
        status="accepted",
        reason="accepted",
        attempts=1,
        requestSha256=request,
        responseSha256=canonical_digest({"job": job.jobId, "kind": "response"}),
        record=record,
    )


def test_prepare_only_publishes_verified_source_corpus_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "curation.json"
    _write_config(config_path)
    _install_runner_inputs(monkeypatch, _ordinary_batches())

    first = runner.run_curation(config_path, tmp_path / "workspace", prepare_only=True)
    assert first.status == "prepared"
    assert first.sourceRecords == 5
    assert first.generatedAccepted == first.generatedQuarantined == 0
    assert len(first.datasets) == 1
    manifest = load_verified_artifact(Path(first.datasets[0].outputPath))
    assert isinstance(manifest.root, DatasetParent)
    assert set(manifest.root.details.assignments) == {
        "train-a",
        "train-b",
        "validation-a",
        "calibration-a",
        "test-a",
    }
    report = Path(first.reportPath).read_bytes()

    resumed = runner.run_curation(config_path, tmp_path / "workspace", prepare_only=True)
    assert resumed == first
    assert Path(resumed.reportPath).read_bytes() == report


def test_full_run_publishes_three_artifacts_without_generating_real_heldouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "curation.json"
    _write_config(config_path)
    batches = _ordinary_batches()
    _install_runner_inputs(monkeypatch, batches)
    seen: list[tuple[CandidateJob, DataRecord]] = []

    def accept(
        _config: object,
        *,
        identity: EndpointModelIdentity,
        parent: DataRecord,
        job: CandidateJob,
        cache_dir: Path,
    ) -> CandidateOutcome:
        del _config, cache_dir
        seen.append((job, parent))
        return _accepted_outcome(identity, parent, job)

    monkeypatch.setattr(generation, "generate_candidate", accept)
    result = runner.run_curation(config_path, tmp_path / "workspace")

    assert result.status == "completed"
    assert result.generatedAccepted == 4
    assert result.generatedQuarantined == 0
    assert len(result.datasets) == 3
    manifests = {
        Path(item.outputPath).name: load_verified_artifact(Path(item.outputPath))
        for item in result.datasets
    }
    assert set(manifests) == {"source-corpus", "augmented-corpus", "synthetic-regression"}
    assert all(manifest.root.kind == "dataset" for manifest in manifests.values())
    assert any(job.purpose == "training-augmentation" for job, _parent in seen)
    assert any(job.purpose == "synthetic-regression" for job, _parent in seen)
    assert all(
        parent.sourceId == "foliqant-scenarios"
        if job.purpose == "synthetic-regression"
        else job.split == "train" and parent.id.startswith("train-")
        for job, parent in seen
    )
    assert {parent.id for _job, parent in seen}.isdisjoint(
        {"validation-a", "calibration-a", "test-a"}
    )


def test_failed_generation_resumes_after_last_persisted_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "curation.json"
    _write_config(config_path)
    _install_runner_inputs(monkeypatch, _ordinary_batches())
    attempted: list[str] = []

    def interrupt_second(
        _config: object,
        *,
        identity: EndpointModelIdentity,
        parent: DataRecord,
        job: CandidateJob,
        cache_dir: Path,
    ) -> CandidateOutcome:
        del _config, cache_dir
        attempted.append(job.jobId)
        if len(attempted) == 2:
            raise ModelError("NETWORK_FAILED", "test endpoint interruption")
        return _accepted_outcome(identity, parent, job)

    monkeypatch.setattr(generation, "generate_candidate", interrupt_second)
    with pytest.raises(ModelError, match="test endpoint interruption"):
        runner.run_curation(config_path, tmp_path / "workspace")
    completed_job = attempted[0]

    def resume(
        _config: object,
        *,
        identity: EndpointModelIdentity,
        parent: DataRecord,
        job: CandidateJob,
        cache_dir: Path,
    ) -> CandidateOutcome:
        del _config, cache_dir
        attempted.append(job.jobId)
        return _accepted_outcome(identity, parent, job)

    monkeypatch.setattr(generation, "generate_candidate", resume)
    result = runner.run_curation(config_path, tmp_path / "workspace")
    assert result.status == "completed"
    assert attempted.count(completed_job) == 1
    assert result.generatedAccepted == 4


def test_dropped_cross_source_duplicate_preserves_restrictive_source_rights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "curation.json"
    _write_config(config_path, sources_=["source-a", "source-b", "source-c"])
    duplicate_prompt = "The same cross-source prompt"
    duplicate_answer = "The same cross-source answer"
    source_a = _batch(
        "source-a",
        [
            _row(
                "duplicate-train",
                "train",
                source_id="source-a",
                prompt=duplicate_prompt,
                answer=duplicate_answer,
            )
        ],
    )
    source_a = SourceBatch.model_validate(
        {
            **source_a.model_dump(mode="json"),
            "source": _rights("source-a", restriction="restrictive-source-a").model_dump(
                mode="json"
            ),
        }
    )
    source_b = _batch(
        "source-b",
        [
            _row(
                "duplicate-test",
                "test",
                source_id="source-b",
                prompt=duplicate_prompt,
                answer=duplicate_answer,
            )
        ],
    )
    source_c = _batch(
        "source-c",
        [_row(f"flex-{index}", "train", source_id="source-c") for index in range(5)],
    )
    _install_runner_inputs(monkeypatch, [source_a, source_b, source_c])

    result = runner.run_curation(config_path, tmp_path / "workspace", prepare_only=True)
    manifest = load_verified_artifact(Path(result.datasets[0].outputPath))
    assert isinstance(manifest.root, DatasetParent)
    assignments = manifest.root.details.assignments
    assert "duplicate-test" in assignments
    assert "duplicate-train" not in assignments
    source_rights = {right.sourceId: right for right in manifest.root.sourceRights}
    assert "source-a" not in source_rights
    retained = source_rights["source-b"]
    assert "source-a" in retained.license
    assert any("restrictive-source-a" in value for value in retained.restrictions)


def test_ineligible_labels_are_retained_but_not_used_for_augmentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batches = _ordinary_batches()
    for row in batches[0].records:
        if row.originalSplit == "train":
            row.record.tags.append("augmentation-ineligible:token-aligned")
    _install_runner_inputs(monkeypatch, batches)
    config_path = tmp_path / "config.json"
    _write_config(config_path, candidates=8)
    observed: list[CandidateJob] = []

    def generate(
        _config: object,
        *,
        identity: EndpointModelIdentity,
        parent: DataRecord,
        job: CandidateJob,
        cache_dir: Path,
    ) -> CandidateOutcome:
        observed.append(job)
        return _accepted_outcome(identity, parent, job)

    monkeypatch.setattr(generation, "generate_candidate", generate)
    result = runner.run_curation(config_path, tmp_path / "workspace")
    assert result.sourceRecords == 5
    assert observed and all(job.purpose == "synthetic-regression" for job in observed)
    coverage = json.loads((Path(result.runPath) / "coverage.json").read_text())["payload"]
    assert coverage["counts"]["augmentation-ineligible:token-aligned:excluded"] >= 1

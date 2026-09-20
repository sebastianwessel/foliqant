"""Separate curation repair passes preserve accepted work and isolate retries."""

from __future__ import annotations

from pathlib import Path

import pytest

from foliqant_model.contracts.inputs import ChatMessage, DataRecord
from foliqant_model.curation.contracts import (
    CandidateAttemptTrace,
    CandidateCallTrace,
    CandidateJob,
    CandidateOutcome,
    CurationConfig,
    CurationPlan,
)
from foliqant_model.curation.endpoint import EndpointModelIdentity
from foliqant_model.curation.repair import (
    RepairPass,
    _safe_parent,
    _verify_finished_coverage,
    initialize_repair_child,
    load_repair_pass,
    repair_jobs,
)
from foliqant_model.curation.storage import run_lock
from foliqant_model.errors import ModelError

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _job(job_id: str, parent: str) -> CandidateJob:
    return CandidateJob(
        jobId=job_id,
        parentRecordId=parent,
        familyId="family-one",
        split="train",
        language="en",
        purpose="training-augmentation",
        operation="paraphrase",
    )


def _outcome(job: CandidateJob, *, accepted: bool) -> CandidateOutcome:
    record = (
        DataRecord(
            schemaVersion=1,
            id="generated-" + job.jobId,
            sourceId="generated-source",
            language="en",
            groupKeys=["group-one"],
            messages=[
                ChatMessage(role="user", content="reworded request"),
                ChatMessage(role="assistant", content="answer"),
            ],
            origin="teacher",
            reviewed=False,
            familyId="family-one",
        )
        if accepted
        else None
    )
    return CandidateOutcome(
        jobId=job.jobId,
        status="accepted" if accepted else "quarantined",
        reason="accepted" if accepted else "checker-disagreed",
        attempts=1,
        requestSha256=DIGEST_A,
        responseSha256=DIGEST_B,
        record=record,
    )


def test_repair_jobs_keep_non_rejected_identity_and_remap_rejects(tmp_path: Path) -> None:
    accepted_job = _job(DIGEST_A, "accepted-parent")
    rejected_job = _job(DIGEST_B, "rejected-parent")
    accepted = _outcome(accepted_job, accepted=True)
    repair = RepairPass(
        parent=tmp_path / "parent",
        run_id=DIGEST_C,
        jobs=[accepted_job, rejected_job],
        outcomes={
            accepted_job.jobId: accepted,
            rejected_job.jobId: _outcome(rejected_job, accepted=False),
        },
        source_batches=[],
        source_plan=CurationPlan(
            configurationSha256=DIGEST_A,
            catalogSha256=DIGEST_B,
            records=[],
            frozenFamilies={},
            excludedCounts={},
        ),
        model=EndpointModelIdentity(
            modelId="test-model",
            modelType="llm",
            publisher=None,
            architecture=None,
            format=None,
            quantization=None,
            sizeBytes=None,
            maxContextLength=None,
            metadataSha256=DIGEST_A,
            immutableRevision=None,
            structuredOutput="unknown",
        ),
    )

    jobs, prior = repair_jobs(repair)

    assert jobs[0] == accepted_job
    assert jobs[1].jobId != rejected_job.jobId
    assert jobs[1].model_dump(exclude={"jobId"}) == rejected_job.model_dump(exclude={"jobId"})
    assert prior == {jobs[1].jobId: repair.outcomes[rejected_job.jobId]}
    assert repair_jobs(repair) == (jobs, prior)


def test_repair_parent_must_be_direct_and_cannot_be_symlink(tmp_path: Path) -> None:
    curation = tmp_path / "curation"
    curation.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (curation / "linked-run").symlink_to(real, target_is_directory=True)

    with pytest.raises(ModelError, match="direct workspace run"):
        _safe_parent(tmp_path, curation / "linked-run")

    nested = curation / "nested" / "run"
    nested.mkdir(parents=True)
    with pytest.raises(ModelError, match="direct workspace run"):
        _safe_parent(tmp_path, nested)


def test_repair_parent_must_not_be_active(tmp_path: Path) -> None:
    parent = tmp_path / "curation/parent"
    parent.mkdir(parents=True)
    config = CurationConfig.model_validate({"sources": [{"id": "source-one"}]})

    with run_lock(parent), pytest.raises(ModelError, match="Another process owns"):
        load_repair_pass(tmp_path, parent, config, recipe_sha256=DIGEST_A, native=False)


def test_finished_coverage_accepts_generic_and_native_failure_shapes() -> None:
    accepted_job = _job(DIGEST_A, "accepted-parent")
    rejected_job = _job(DIGEST_B, "rejected-parent")
    jobs = [accepted_job, rejected_job]
    outcomes = {
        accepted_job.jobId: _outcome(accepted_job, accepted=True),
        rejected_job.jobId: _outcome(rejected_job, accepted=False),
    }
    generic_counts = {
        "language:en:accepted": 1,
        "language:en:quarantined": 1,
        "operation:paraphrase:accepted": 1,
        "operation:paraphrase:quarantined": 1,
        "purpose:training-augmentation:accepted": 1,
        "purpose:training-augmentation:quarantined": 1,
        "reason:accepted:accepted": 1,
        "reason:checker-disagreed:quarantined": 1,
        "source:source-one:accepted": 1,
        "source:source-one:quarantined": 1,
    }

    _verify_finished_coverage(
        {"counts": generic_counts, "plannedCandidates": 2},
        {"status": "completed", "generatedAccepted": 1, "generatedQuarantined": 1},
        jobs,
        outcomes,
        native=False,
    )
    _verify_finished_coverage(
        {
            "status": "coverage-incomplete",
            "counts": {"accepted": 1, "quarantined": 1},
            "reasons": {"accepted": 1, "checker-disagreed": 1},
            "plannedCandidates": 2,
        },
        None,
        jobs,
        outcomes,
        native=True,
    )

    with pytest.raises(ModelError, match="coverage counts are inconsistent"):
        _verify_finished_coverage(
            {"counts": generic_counts | {"language:en:quarantined": 0}, "plannedCandidates": 2},
            {"status": "completed", "generatedAccepted": 1, "generatedQuarantined": 1},
            jobs,
            outcomes,
            native=False,
        )


def test_carried_acceptance_does_not_require_cache_for_absent_response(tmp_path: Path) -> None:
    job = _job(DIGEST_A, "accepted-parent")
    accepted = _outcome(job, accepted=True).model_copy(
        update={
            "attempts": 2,
            "attemptTrace": [
                CandidateAttemptTrace(
                    attempt=1,
                    status="quarantined",
                    reason="endpoint-response-invalid",
                    calls=[
                        CandidateCallTrace(
                            phase="generator",
                            status="rejected",
                            reason="endpoint-response-invalid",
                            callId=DIGEST_A,
                            requestSha256=DIGEST_B,
                            responseSha256=DIGEST_C,
                            finalAssistantResponsePreview=None,
                            previewTruncated=False,
                            responseSource="absent",
                        )
                    ],
                ),
                CandidateAttemptTrace(
                    attempt=2,
                    status="accepted",
                    reason="accepted",
                    calls=[],
                ),
            ],
        }
    )
    repair = RepairPass(
        parent=tmp_path / "parent",
        run_id=DIGEST_C,
        jobs=[job],
        outcomes={job.jobId: accepted},
        source_batches=[],
        source_plan=CurationPlan(
            configurationSha256=DIGEST_A,
            catalogSha256=DIGEST_B,
            records=[],
            frozenFamilies={},
            excludedCounts={},
        ),
        model=EndpointModelIdentity(
            modelId="test-model",
            modelType="llm",
            publisher=None,
            architecture=None,
            format=None,
            quantization=None,
            sizeBytes=None,
            maxContextLength=None,
            metadataSha256=DIGEST_A,
            immutableRevision=None,
            structuredOutput="unknown",
        ),
    )

    initialize_repair_child(tmp_path / "child", repair, recipe_sha256=DIGEST_B)

    assert (tmp_path / "child/outcomes" / f"{job.jobId}.json").is_file()
    assert not (tmp_path / "child/requests/calls" / f"{DIGEST_A}.json").exists()

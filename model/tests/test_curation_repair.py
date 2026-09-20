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
    CurationPlan,
)
from foliqant_model.curation.endpoint import EndpointModelIdentity
from foliqant_model.curation.repair import (
    RepairPass,
    _safe_parent,
    initialize_repair_child,
    repair_jobs,
)
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

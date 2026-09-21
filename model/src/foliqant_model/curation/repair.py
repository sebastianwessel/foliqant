"""Validation and immutable identity helpers for separate curation repair passes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..contracts.base import canonical_digest
from ..errors import ModelError
from .contracts import CandidateJob, CandidateOutcome, CurationConfig, CurationPlan, SourceBatch
from .endpoint import EndpointModelIdentity
from .storage import load_object, run_lock, store_object


@dataclass(frozen=True)
class RepairPass:
    """Verified parent state and deterministic identity for one child repair run."""

    parent: Path
    run_id: str
    jobs: list[CandidateJob]
    outcomes: dict[str, CandidateOutcome]
    source_batches: list[SourceBatch]
    source_plan: CurationPlan
    model: EndpointModelIdentity

    @property
    def namespace(self) -> str:
        """Return the cache-isolation namespace for repaired model requests."""

        return "repair-" + self.run_id


def _safe_parent(root: Path, requested: Path) -> Path:
    curation = root / "curation"
    candidate = requested.expanduser()
    if not candidate.is_absolute():
        candidate = curation / candidate
    try:
        root_resolved = curation.resolve(strict=True)
        parent = candidate.resolve(strict=True)
    except OSError as error:
        raise ModelError("INPUT_NOT_FOUND", "Repair parent run does not exist") from error
    if parent.parent != root_resolved:
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Repair parent must be a direct workspace run")
    for current in (candidate, *candidate.parents):
        if current.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Repair parent path cannot contain symlinks")
        if current == curation:
            break
    else:
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Repair parent path is outside the workspace")
    if not parent.is_dir():
        raise ModelError("INPUT_NOT_FOUND", "Repair parent run is not a directory")
    return parent


def _finished_report(
    parent: Path, coverage: dict[str, object], *, native: bool
) -> dict[str, object] | None:
    """Load terminal success evidence while allowing a native coverage-only failure."""

    if native and coverage.get("status") == "coverage-incomplete":
        return None
    report = load_object(parent / "completed-report.json")
    if not isinstance(report, dict) or report.get("status") != "completed":
        raise ModelError("INTEGRITY_FAILED", "Repair parent completion evidence is invalid")
    return report


def _verify_finished_coverage(
    coverage: object,
    report: dict[str, object] | None,
    jobs: list[CandidateJob],
    outcomes: dict[str, CandidateOutcome],
    *,
    native: bool,
) -> None:
    """Require a complete outcome ledger and coherent terminal coverage evidence."""

    if not isinstance(coverage, dict) or coverage.get("plannedCandidates") != len(jobs):
        raise ModelError("INTEGRITY_FAILED", "Repair parent coverage is invalid")
    if set(outcomes) != {job.jobId for job in jobs}:
        raise ModelError("INTEGRITY_FAILED", "Repair parent outcome ledger is incomplete")
    status_counts = Counter(outcome.status for outcome in outcomes.values())
    expected_statuses = {
        "accepted": status_counts["accepted"],
        "quarantined": status_counts["quarantined"],
    }
    counts = coverage.get("counts")
    if not isinstance(counts, dict) or any(
        not isinstance(key, str) or type(value) is not int or value < 0
        for key, value in counts.items()
    ):
        raise ModelError("INTEGRITY_FAILED", "Repair parent coverage is invalid")
    typed_counts = cast(dict[str, int], counts)
    if native:
        if coverage.get("status") not in {"completed", "coverage-incomplete"}:
            raise ModelError("INTEGRITY_FAILED", "Repair parent completion evidence is invalid")
        if typed_counts != expected_statuses:
            raise ModelError("INTEGRITY_FAILED", "Repair parent coverage counts are inconsistent")
        expected_reasons = dict(sorted(Counter(o.reason for o in outcomes.values()).items()))
        if coverage.get("reasons") != expected_reasons:
            raise ModelError("INTEGRITY_FAILED", "Repair parent coverage reasons are inconsistent")
    else:
        expected_dimensions: Counter[str] = Counter()
        for job in jobs:
            outcome = outcomes[job.jobId]
            for dimension, value in (
                ("purpose", job.purpose),
                ("language", job.language),
                ("operation", job.operation),
                ("reason", outcome.reason),
            ):
                expected_dimensions[f"{dimension}:{value}:{outcome.status}"] += 1
        observed_dimensions = {
            key: value
            for key, value in typed_counts.items()
            if key.startswith(("purpose:", "language:", "operation:", "reason:"))
        }
        if observed_dimensions != dict(sorted(expected_dimensions.items())):
            raise ModelError("INTEGRITY_FAILED", "Repair parent coverage counts are inconsistent")
        for status, expected in expected_statuses.items():
            observed_sources = sum(
                value
                for key, value in typed_counts.items()
                if key.startswith("source:") and key.endswith(f":{status}")
            )
            if observed_sources != expected:
                raise ModelError(
                    "INTEGRITY_FAILED", "Repair parent coverage counts are inconsistent"
                )
    if report is not None and (
        report.get("generatedAccepted") != expected_statuses["accepted"]
        or report.get("generatedQuarantined") != expected_statuses["quarantined"]
    ):
        raise ModelError("INTEGRITY_FAILED", "Repair parent completion counts are inconsistent")


def load_repair_pass(
    root: Path,
    requested: Path,
    config: CurationConfig,
    *,
    recipe_sha256: str,
    native: bool,
) -> RepairPass:
    """Verify a finished parent and derive the deterministic child repair identity."""

    parent = _safe_parent(root, requested)
    with run_lock(parent):
        expected_config = config.model_dump(mode="json")
        if load_object(parent / "configuration.json") != expected_config:
            raise ModelError("CONFIG_INVALID", "Repair configuration differs from its parent run")
        coverage = load_object(parent / "coverage.json")
        if not isinstance(coverage, dict):
            raise ModelError("INTEGRITY_FAILED", "Repair parent coverage is invalid")
        repair_record_path = parent / "repair.json"
        if repair_record_path.exists() or repair_record_path.is_symlink():
            repair_record = load_object(repair_record_path)
            if (
                not isinstance(repair_record, dict)
                or repair_record.get("generationRecipeSha256") != recipe_sha256
            ):
                raise ModelError("CONFIG_INVALID", "Repair generation recipe differs from parent")
        elif native and coverage.get("generationRecipeSha256") != recipe_sha256:
            raise ModelError("CONFIG_INVALID", "Repair generation recipe differs from parent")
        raw_jobs = load_object(parent / "jobs.json")
        if not isinstance(raw_jobs, list):
            raise ModelError("INTEGRITY_FAILED", "Repair parent job plan is invalid")
        jobs = [CandidateJob.model_validate(value) for value in raw_jobs]
        outcomes: dict[str, CandidateOutcome] = {}
        for job in jobs:
            outcome = CandidateOutcome.model_validate(
                load_object(parent / "outcomes" / (job.jobId + ".json"))
            )
            if outcome.jobId != job.jobId:
                raise ModelError("INTEGRITY_FAILED", "Repair parent outcome has the wrong job")
            outcomes[job.jobId] = outcome
        report = _finished_report(parent, coverage, native=native)
        _verify_finished_coverage(coverage, report, jobs, outcomes, native=native)
        if not any(outcome.status == "quarantined" for outcome in outcomes.values()):
            raise ModelError("ARGUMENT_INVALID", "Repair parent has no quarantined outcomes")
        source_plan = CurationPlan.model_validate(load_object(parent / "source-plan.json"))
        source_batches = [
            SourceBatch.model_validate(load_object(parent / "sources" / (source.id + ".json")))
            for source in config.sources
        ]
        model = EndpointModelIdentity.model_validate(load_object(parent / "model-identity.json"))
        parent_identity = canonical_digest(
            {
                "configuration": expected_config,
                "sourcePlan": source_plan.model_dump(mode="json"),
                "jobs": [job.model_dump(mode="json") for job in jobs],
                "outcomes": [outcomes[job.jobId].model_dump(mode="json") for job in jobs],
                "model": model.model_dump(mode="json"),
                "recipe": recipe_sha256,
                "native": native,
            }
        )
    run_id = canonical_digest({"repairParent": parent_identity, "version": 1})
    return RepairPass(
        parent=parent,
        run_id=run_id,
        jobs=jobs,
        outcomes=outcomes,
        source_batches=source_batches,
        source_plan=source_plan,
        model=model,
    )


def repair_jobs(repair: RepairPass) -> tuple[list[CandidateJob], dict[str, CandidateOutcome]]:
    """Carry accepted jobs unchanged and remap only quarantined jobs into the child run."""

    jobs: list[CandidateJob] = []
    prior: dict[str, CandidateOutcome] = {}
    for job in repair.jobs:
        outcome = repair.outcomes[job.jobId]
        if outcome.status == "accepted":
            jobs.append(job)
            continue
        payload = job.model_dump(mode="json", exclude={"jobId"})
        repaired = CandidateJob.model_validate(
            {
                **payload,
                "jobId": canonical_digest(
                    {"repairRun": repair.run_id, "parentJob": job.model_dump(mode="json")}
                ),
            }
        )
        jobs.append(repaired)
        prior[repaired.jobId] = outcome
    return jobs, prior


def verify_job_plan(repair: RepairPass, planned: list[CandidateJob]) -> None:
    """Verify that a repaired run retains the same ordered logical job plan."""

    parent_payloads = [job.model_dump(mode="json", exclude={"jobId"}) for job in repair.jobs]
    planned_payloads = [job.model_dump(mode="json", exclude={"jobId"}) for job in planned]
    if parent_payloads != planned_payloads:
        raise ModelError("INTEGRITY_FAILED", "Repair job plan differs from its parent run")
    if not any(
        (repair.parent / name).exists()
        for name in ("repair.json", "continuation.json", "projection-extension.json")
    ) and [job.jobId for job in repair.jobs] != [job.jobId for job in planned]:
        raise ModelError("CONFIG_INVALID", "Repair generation recipe differs from parent")


def initialize_repair_child(run: Path, repair: RepairPass, *, recipe_sha256: str) -> None:
    """Persist the verified parent link, source snapshots, and carried accepted outcomes."""

    store_object(
        run / "repair.json",
        {
            "schemaVersion": 1,
            "parentRun": str(repair.parent),
            "repairRunSha256": repair.run_id,
            "parentModelIdentitySha256": repair.model.metadataSha256,
            "parentSourcePlanSha256": canonical_digest(repair.source_plan.model_dump(mode="json")),
            "generationRecipeSha256": recipe_sha256,
        },
    )
    for batch in repair.source_batches:
        store_object(run / "sources" / (batch.source.id + ".json"), batch.model_dump(mode="json"))
    for job in repair.jobs:
        outcome = repair.outcomes[job.jobId]
        if outcome.status == "accepted":
            store_object(run / "outcomes" / (job.jobId + ".json"), outcome.model_dump(mode="json"))
            for attempt in outcome.attemptTrace:
                for call in attempt.calls:
                    if call.responseSource == "absent":
                        continue
                    cached = load_object(
                        repair.parent / "requests" / "calls" / (call.callId + ".json")
                    )
                    store_object(run / "requests" / "calls" / (call.callId + ".json"), cached)


def prior_response(repair: RepairPass, outcome: CandidateOutcome, *, phase: str) -> str | None:
    """Load the complete last retained response from the verified parent call cache."""

    from .generation import load_cached_final_response

    for attempt in reversed(outcome.attemptTrace):
        for call in reversed(attempt.calls):
            if call.phase != phase:
                continue
            if call.responseSource == "absent":
                return None
            return load_cached_final_response(repair.parent / "requests", call.callId)
    return None


def verify_parent_plan(repair: RepairPass, plan: CurationPlan) -> None:
    """Fail before inference if source bytes or frozen family assignments changed."""

    if plan != repair.source_plan:
        raise ModelError("INTEGRITY_FAILED", "Repair source plan differs from its parent run")


def verify_parent_model(repair: RepairPass, model: EndpointModelIdentity) -> None:
    """Fail before inference if endpoint model metadata differs from the parent run."""

    if model != repair.model:
        raise ModelError("CONFIG_INVALID", "Repair endpoint model differs from its parent run")


def quarantine_outcome(outcome: CandidateOutcome, reason: str) -> CandidateOutcome:
    """Change a generated outcome disposition while keeping its final trace consistent."""

    traces = list(outcome.attemptTrace)
    if traces:
        traces[-1] = traces[-1].model_copy(update={"status": "quarantined", "reason": reason})
    return CandidateOutcome.model_validate(
        {
            **outcome.model_dump(mode="json"),
            "status": "quarantined",
            "reason": reason,
            "record": None,
            "attemptTrace": [trace.model_dump(mode="json") for trace in traces],
        }
    )

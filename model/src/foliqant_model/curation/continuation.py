"""Explicit, hash-bound reuse of completed native outcomes after a recipe change."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contracts.base import canonical_digest
from ..errors import ModelError
from .contracts import CandidateJob, CandidateOutcome, CurationConfig
from .endpoint import EndpointModelIdentity
from .repair import _safe_parent
from .storage import load_object, run_lock, store_object


@dataclass(frozen=True)
class Continuation:
    """Verified parent snapshot; records retain their original generation provenance."""

    parent: Path
    run_id: str
    snapshot: dict[str, object]
    jobs: list[CandidateJob]
    outcomes: dict[str, CandidateOutcome]
    calls: dict[str, object]
    model: EndpointModelIdentity

    def recipe_for(self, job_id: str, current: str) -> str:
        """Select original provenance only for an explicitly carried outcome."""
        outcome = self.outcomes.get(job_id)
        if outcome is not None and outcome.record is not None:
            if outcome.record.generation is not None:
                return outcome.record.generation.promptSha256
        return current


def load_continuation(
    root: Path, requested: Path, config: CurationConfig, *, recipe: str
) -> Continuation:
    """Snapshot a locked native parent, rejecting corrupt or changed configuration."""
    parent = _safe_parent(root, requested)
    with run_lock(parent):
        configuration = load_object(parent / "configuration.json")
        if configuration != config.model_dump(mode="json"):
            raise ModelError("CONFIG_INVALID", "Continuation configuration differs from parent")
        raw_jobs = load_object(parent / "jobs.json")
        if not isinstance(raw_jobs, list):
            raise ModelError("INTEGRITY_FAILED", "Continuation parent jobs are invalid")
        jobs = [CandidateJob.model_validate(value) for value in raw_jobs]
        if len({job.jobId for job in jobs}) != len(jobs):
            raise ModelError("INTEGRITY_FAILED", "Continuation parent has duplicate jobs")
        outcomes: dict[str, CandidateOutcome] = {}
        calls: dict[str, object] = {}
        for job in jobs:
            path = parent / "outcomes" / (job.jobId + ".json")
            if not path.exists() and not path.is_symlink():
                continue
            outcome = CandidateOutcome.model_validate(load_object(path))
            if outcome.jobId != job.jobId:
                raise ModelError("INTEGRITY_FAILED", "Continuation outcome belongs to another job")
            outcomes[job.jobId] = outcome
            for attempt in outcome.attemptTrace:
                for call in attempt.calls:
                    path = parent / "requests" / "calls" / (call.callId + ".json")
                    if (
                        call.responseSource == "absent"
                        and not path.exists()
                        and not path.is_symlink()
                    ):
                        continue
                    payload = load_object(path)
                    if not isinstance(payload, dict) or (
                        canonical_digest(payload.get("callIdentity")) != call.callId
                    ):
                        raise ModelError("INTEGRITY_FAILED", "Continuation call identity changed")
                    calls[call.callId] = payload
        if not outcomes:
            raise ModelError("ARGUMENT_INVALID", "Continuation parent has no completed outcomes")
        model = EndpointModelIdentity.model_validate(load_object(parent / "model-identity.json"))
        snapshot: dict[str, object] = {
            "configuration": configuration,
            "source-plan": load_object(parent / "source-plan.json"),
            "native-seeds": load_object(parent / "native-seeds.json"),
            "native-families": load_object(parent / "native-families.json"),
            "sources": {
                source.id: load_object(parent / "sources" / (source.id + ".json"))
                for source in config.sources
            },
            "jobs": raw_jobs,
            "outcomes": {key: value.model_dump(mode="json") for key, value in outcomes.items()},
            "calls": calls,
            "model": model.model_dump(mode="json"),
        }
    run_id = canonical_digest({"parentSnapshot": snapshot, "recipe": recipe, "version": 1})
    return Continuation(parent, run_id, snapshot, jobs, outcomes, calls, model)


def continuation_jobs(parent: Continuation, planned: list[CandidateJob]) -> list[CandidateJob]:
    """Keep original IDs for finished jobs and use current IDs only for missing jobs."""
    if [job.model_dump(mode="json", exclude={"jobId"}) for job in parent.jobs] != [
        job.model_dump(mode="json", exclude={"jobId"}) for job in planned
    ]:
        raise ModelError("INTEGRITY_FAILED", "Continuation logical job plan differs from parent")
    return [
        old if old.jobId in parent.outcomes else new
        for old, new in zip(parent.jobs, planned, strict=True)
    ]


def verify_continuation_inputs(parent: Continuation, run: Path) -> None:
    """Require unchanged sources, seed semantics and frozen partitions before reuse."""
    for name in ("source-plan", "native-seeds", "native-families"):
        if load_object(run / (name + ".json")) != parent.snapshot[name]:
            raise ModelError("INTEGRITY_FAILED", "Continuation inputs differ from parent")


def initialize_continuation(run: Path, parent: Continuation, *, recipe: str) -> None:
    """Copy verified completed work, explicitly recording its parent and recipe boundary."""
    store_object(
        run / "continuation.json",
        {
            "schemaVersion": 1,
            "parentRun": str(parent.parent),
            "parentSnapshotSha256": canonical_digest(parent.snapshot),
            "generationRecipeSha256": recipe,
            "carriedOutcomes": {
                job_id: canonical_digest(outcome.model_dump(mode="json"))
                for job_id, outcome in parent.outcomes.items()
            },
        },
    )
    for job_id, outcome in parent.outcomes.items():
        store_object(run / "outcomes" / (job_id + ".json"), outcome.model_dump(mode="json"))
    for call_id, payload in parent.calls.items():
        store_object(run / "requests" / "calls" / (call_id + ".json"), payload)

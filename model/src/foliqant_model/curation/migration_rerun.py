"""Isolated, resumable generation for a frozen migration's remaining train tasks."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from ..contracts.base import canonical_digest
from ..errors import ModelError
from ..workspace import check_workspace_git_policy
from .contracts import CandidateJob, CandidateOutcome
from .decision_generation import (
    _recover_call,
    decision_generation_recipe_digest,
    generate_decision_candidate,
)
from .decision_runner import _validate_outcome
from .migration import load_migration, migration_recipe_digest, publish_migration
from .migration_contracts import (
    MigrationEntry,
    MigrationPending,
    MigrationPlan,
    MigrationProvenance,
    MigrationReview,
)
from .runner import _derived_sources
from .runtime import CurationControl, cached_request_count
from .storage import load_object, run_lock, store_object

_RERUN_VERSION = "migration-pending-only-rerun-v1"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _selection(
    plan: MigrationPlan, *, limit: int | None, job_ids: list[str] | None
) -> list[MigrationPending]:
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ModelError("ARGUMENT_INVALID", "Rerun limit must be a positive integer")
    pending = {item.seed.parent.id: item for item in plan.pending}
    review = {item.recordId for item in plan.review}
    if pending.keys() & review:
        raise ModelError("INTEGRITY_FAILED", "Review tasks cannot enter the migration rerun queue")
    if job_ids is not None:
        if not job_ids or any(not isinstance(value, str) for value in job_ids):
            raise ModelError("ARGUMENT_INVALID", "Select at least one pending migration task ID")
        if len(set(job_ids)) != len(job_ids) or not set(job_ids) <= pending.keys():
            raise ModelError("ARGUMENT_INVALID", "Rerun IDs must uniquely select pending tasks")
        ids = sorted(job_ids)
    else:
        ids = sorted(pending)
    selected = [pending[record_id] for record_id in ids[:limit]]
    if not selected:
        raise ModelError("ARGUMENT_INVALID", "Migration has no pending training tasks to rerun")
    declared = {source.id: source for source in plan.sources}
    for item in selected:
        family = item.seed.parent.familyId
        if family not in plan.families or plan.families[family].split != "train":
            raise ModelError("INTEGRITY_FAILED", "Rerun task is not in a frozen training family")
        source_ids = {
            item.seed.parent.sourceId,
            *(source.rightsSourceId for source in item.sources),
        }
        if not source_ids <= declared.keys():
            raise ModelError("INTEGRITY_FAILED", "Rerun task lacks source-rights provenance")
    return selected


def _job(item: MigrationPending, run_id: str, recipe: str) -> CandidateJob:
    parent = item.seed.parent
    fields = {
        "parentRecordId": parent.id,
        "familyId": parent.familyId,
        "split": "train",
        "language": parent.language,
        "purpose": "decision-training",
        "operation": "native-decision",
    }
    return CandidateJob.model_validate(
        {**fields, "jobId": canonical_digest({"rerun": run_id, "recipe": recipe, "job": fields})},
        strict=True,
    )


def _verify_calls(outcome: CandidateOutcome, cache: Path) -> None:
    for attempt in outcome.attemptTrace:
        for call in attempt.calls:
            path = cache / "calls" / (call.callId + ".json")
            if call.responseSource == "absent" and not path.exists() and not path.is_symlink():
                continue
            # Verifies the immutable envelope, original request/schema/model and
            # retained response against the trace; identical writes are read-only.
            _recover_call(cache, cache, call)


def _entry(item: MigrationPending, outcome: CandidateOutcome) -> MigrationEntry:
    assert outcome.record is not None
    return MigrationEntry(
        record=outcome.record,
        provenance=MigrationProvenance(
            recordId=outcome.record.id,
            operation="rerun",
            evidenceStatus="model-verified",
            parentRecordIds=[item.seed.parent.id],
            sources=item.sources,
            rule="native-rerun-semantic-and-evidence-validation-v1",
        ),
    )


def _child_plan(
    plan: MigrationPlan,
    selected: list[MigrationPending],
    jobs: list[CandidateJob],
    outcomes: list[CandidateOutcome],
) -> MigrationPlan:
    entries = list(plan.entries)
    review = list(plan.review)
    sources = {source.id: source for source in plan.sources}
    for item, job, outcome in zip(selected, jobs, outcomes, strict=True):
        if outcome.status == "quarantined":
            review.append(
                MigrationReview(
                    recordId=item.seed.parent.id, reason=outcome.reason, previousJobId=job.jobId
                )
            )
            continue
        assert outcome.record is not None
        if item.seed.mode == "rewrite":
            parent = item.seed.parent
            entries.append(
                MigrationEntry(
                    record=parent,
                    provenance=MigrationProvenance(
                        recordId=parent.id,
                        operation="rerun",
                        evidenceStatus="derived-reference",
                        parentRecordIds=[],
                        sources=item.sources,
                        rule="validated-rerun-seed-required-for-generation-ancestry-v1",
                    ),
                )
            )
            if outcome.record.sourceId not in sources:
                derived = _derived_sources([sources[parent.sourceId]], plan.model)[0]
                if derived.id != outcome.record.sourceId:
                    raise ModelError("INTEGRITY_FAILED", "Rerun derivative changed its source")
                sources[derived.id] = derived
        entries.append(_entry(item, outcome))
    completed_ids = {item.seed.parent.id for item in selected}
    payload = plan.model_dump(mode="json")
    payload.update(
        {
            "entries": [item.model_dump(mode="json") for item in entries],
            "pending": [
                item.model_dump(mode="json")
                for item in plan.pending
                if item.seed.parent.id not in completed_ids
            ],
            "review": [item.model_dump(mode="json") for item in review],
            "sources": [source.model_dump(mode="json") for source in sources.values()],
        }
    )
    try:
        return MigrationPlan.model_validate(payload, strict=True)
    except ValidationError as error:
        raise ModelError(
            "INTEGRITY_FAILED", "Rerun publication violates migration membership"
        ) from error


def rerun_migration(
    from_migration: Path,
    output: Path | None = None,
    *,
    limit: int | None = None,
    job_ids: list[str] | None = None,
    control: CurationControl | None = None,
) -> dict[str, object]:
    """Rerun only selected pending train tasks into an immutable resumable child.

    IDs name ``seed.parent.id`` entries in the frozen pending queue. Selection
    is sorted, then bounded by ``limit``. Completed quarantines move to review;
    transport failures stop immediately and leave completed outcomes resumable.
    The saved model identity is used without discovery or a model substitution.
    """

    runtime = control or CurationControl()
    with runtime.active():
        parent = _absolute(from_migration)
        plan = load_migration(parent)
        migration_recipe = migration_recipe_digest()
        if plan.recipeSha256 != migration_recipe:
            raise ModelError(
                "CONFIG_INVALID",
                "Migration task recipe changed; prepare a new migration before rerunning",
            )
        selected = _selection(plan, limit=limit, job_ids=job_ids)
        recipe = decision_generation_recipe_digest()
        identity = {
            "version": _RERUN_VERSION,
            "parentPlanSha256": canonical_digest(plan.model_dump(mode="json")),
            "migrationRecipeSha256": migration_recipe,
            "generationRecipeSha256": recipe,
            "configurationSha256": canonical_digest(plan.config.model_dump(mode="json")),
            "model": plan.model.model_dump(mode="json"),
            "selectedRecordIds": [item.seed.parent.id for item in selected],
        }
        run_id = canonical_digest(identity)
        run = _absolute(output) if output is not None else parent.parent / "reruns" / run_id
        if run == parent or parent in run.parents or run in parent.parents:
            raise ModelError(
                "ARGUMENT_INVALID", "Migration rerun output must be separate from its parent"
            )
        check_workspace_git_policy(run)
        jobs = [_job(item, run_id, recipe) for item in selected]
        with run_lock(run):
            runtime.checkpoint()
            store_object(
                run / "rerun-plan.json",
                {
                    "schemaVersion": 1,
                    "runId": run_id,
                    "parentMigration": str(parent),
                    **identity,
                    "jobs": [job.model_dump(mode="json") for job in jobs],
                },
            )
            outcomes: list[CandidateOutcome] = []
            accepted = 0
            quarantined = 0
            reused = 0
            for item, job in zip(selected, jobs, strict=True):
                runtime.checkpoint()
                runtime.report(
                    "generating",
                    run,
                    completed=len(outcomes),
                    total=len(jobs),
                    accepted=accepted,
                    quarantined=quarantined,
                    reused=reused,
                    cached=cached_request_count(run / "requests"),
                )
                path = run / "outcomes" / (job.jobId + ".json")
                if path.exists() or path.is_symlink():
                    try:
                        outcome = CandidateOutcome.model_validate(load_object(path), strict=True)
                    except ValidationError as error:
                        raise ModelError(
                            "INTEGRITY_FAILED", "Migration rerun outcome is invalid"
                        ) from error
                    reused += 1
                else:
                    with runtime.local_request():
                        outcome = generate_decision_candidate(
                            plan.config,
                            identity=plan.model,
                            seed=item.seed,
                            job=job,
                            cache_dir=run / "requests",
                            request_namespace="migration-rerun-" + run_id,
                        )
                _validate_outcome(item.seed, job, outcome, plan.model, recipe=recipe)
                _verify_calls(outcome, run / "requests")
                store_object(path, outcome.model_dump(mode="json"))
                outcomes.append(outcome)
                accepted += outcome.status == "accepted"
                quarantined += outcome.status == "quarantined"
                runtime.checkpoint()
            child = _child_plan(plan, selected, jobs, outcomes)
            runtime.report(
                "publishing",
                run,
                completed=len(outcomes),
                total=len(jobs),
                accepted=accepted,
                quarantined=quarantined,
                reused=reused,
                cached=cached_request_count(run / "requests"),
            )
            runtime.checkpoint()
            receipt: dict[str, object] = {
                "schemaVersion": 1,
                "outcomeSha256": {
                    outcome.jobId: canonical_digest(outcome.model_dump(mode="json"))
                    for outcome in outcomes
                },
                "rerun": {
                    "runId": run_id,
                    "parentMigration": str(parent),
                    "parentPlanSha256": identity["parentPlanSha256"],
                    "selectedRecordIds": identity["selectedRecordIds"],
                    "accepted": accepted,
                    "quarantined": quarantined,
                    "remainingPending": len(child.pending),
                },
            }
            store_object(run / "rerun-report.json", receipt)
            published = publish_migration(run, child)
            result: dict[str, object] = {**published, "command": "rerun-migrated-decisions"}
            runtime.report(
                "completed",
                run,
                completed=len(outcomes),
                total=len(jobs),
                accepted=accepted,
                quarantined=quarantined,
                reused=reused,
                cached=cached_request_count(run / "requests"),
            )
            return result

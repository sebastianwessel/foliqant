"""Immutable additions to a completed native run using offline-prepared projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..contracts.base import canonical_digest
from ..contracts.inputs import FrozenFamilyAssignment, ResolvedSourceDeclaration
from ..errors import ModelError
from .continuation import Continuation, load_continuation
from .contracts import CandidateJob, CurationConfig
from .decision_seeds import DecisionSeed
from .projection_contracts import ProjectionPlan
from .projection_preparation import build_projection_plan, load_projection_plan
from .storage import load_object, store_object


@dataclass(frozen=True)
class ProjectionExtension:
    """Verified completed parent and exact deterministic supplemental task plan."""

    parent: Continuation
    plan: ProjectionPlan
    run_id: str


def load_projection_extension(
    root: Path,
    requested: Path,
    config: CurationConfig,
    plan_path: Path,
    *,
    recipe: str,
) -> ProjectionExtension:
    """Reject active/incomplete or changed parents before any model discovery or call."""
    parent = load_continuation(root, requested, config, recipe=recipe)
    if len(parent.outcomes) != len(parent.jobs):
        raise ModelError("ARGUMENT_INVALID", "Projection extension requires a completed parent")
    coverage = load_object(parent.parent / "coverage.json")
    if (
        not isinstance(coverage, dict)
        or coverage.get("status") not in {"completed", "coverage-incomplete"}
        or coverage.get("plannedCandidates") != len(parent.jobs)
        or coverage.get("counts")
        != {
            "accepted": sum(o.status == "accepted" for o in parent.outcomes.values()),
            "quarantined": sum(o.status == "quarantined" for o in parent.outcomes.values()),
        }
    ):
        raise ModelError("INTEGRITY_FAILED", "Projection parent completion evidence is invalid")
    if (parent.parent / "projection-plan.json").exists():
        raise ModelError("ARGUMENT_INVALID", "Parent already contains scoped source projections")
    plan = load_projection_plan(plan_path)
    if Path(plan.parentRun).resolve() != parent.parent.resolve():
        raise ModelError("INTEGRITY_FAILED", "Projection plan belongs to a different parent run")
    expected = build_projection_plan(parent.parent, pilot=plan.selection == "pilot")
    if plan.model_dump(mode="json", exclude={"parentRun"}) != expected.model_dump(
        mode="json", exclude={"parentRun"}
    ):
        raise ModelError("INTEGRITY_FAILED", "Projection plan differs from verified source inputs")
    run_id = canonical_digest(
        {
            "version": 1,
            "parentSnapshot": parent.snapshot,
            "projectionPlan": plan.model_dump(mode="json", exclude={"parentRun"}),
            "recipe": recipe,
        }
    )
    return ProjectionExtension(parent, plan, run_id)


def load_inherited_projection(parent: Path) -> ProjectionPlan:
    """Reproduce inherited tasks from their original frozen inputs before reuse."""
    plan = load_projection_plan(parent / "projection-plan.json")
    marker_path = parent / "projection-extension.json"
    if marker_path.exists():
        marker = load_object(marker_path)
        if (
            not isinstance(marker, dict)
            or marker.get("projectionPlanSha256") != canonical_digest(plan.model_dump(mode="json"))
            or marker.get("parentRun") != plan.parentRun
        ):
            raise ModelError("INTEGRITY_FAILED", "Inherited projection ancestry differs")
    return plan


def append_projections(
    seeds: list[DecisionSeed],
    families: dict[str, FrozenFamilyAssignment],
    rights: list[ResolvedSourceDeclaration],
    plan: ProjectionPlan,
) -> tuple[list[DecisionSeed], dict[str, FrozenFamilyAssignment], list[ResolvedSourceDeclaration]]:
    """Add only new immutable seeds and retain every established family and right."""
    old_ids = {seed.parent.id for seed in seeds}
    if old_ids.intersection(seed.parent.id for seed in plan.seeds):
        raise ModelError("INTEGRITY_FAILED", "Projection plan repeats existing native records")
    merged_families = dict(families)
    for family, assignment in plan.frozenFamilies.items():
        if family in merged_families and merged_families[family] != assignment:
            raise ModelError("DATA_PARTITION_INVALID", "Projection changes a frozen family")
        merged_families[family] = assignment
    merged_rights = {source.id: source for source in rights}
    for source in plan.sources:
        if source.id in merged_rights and merged_rights[source.id] != source:
            raise ModelError("DATA_RIGHTS_DENIED", "Projection changes existing source rights")
        merged_rights[source.id] = source
    return (
        seeds + plan.seeds,
        dict(sorted(merged_families.items())),
        [merged_rights[key] for key in sorted(merged_rights)],
    )


def extension_jobs(
    extension: ProjectionExtension,
    new_jobs: list[CandidateJob],
    config: CurationConfig,
) -> list[CandidateJob]:
    """Reserve all old jobs before admitting the complete selected new training set."""
    expected_new = sum(
        extension.plan.frozenFamilies[cast(str, seed.parent.familyId)].split == "train"
        for seed in extension.plan.seeds
    )
    if (
        len(new_jobs) != expected_new
        or len(extension.parent.jobs) + expected_new > config.generation.maxCandidates
    ):
        raise ModelError(
            "CONFIG_INVALID", "Projection extension exceeds the total candidate budget"
        )
    jobs = extension.parent.jobs + new_jobs
    if len({job.jobId for job in jobs}) != len(jobs):
        raise ModelError("INTEGRITY_FAILED", "Projection extension has duplicate jobs")
    return jobs


def initialize_extension(run: Path, extension: ProjectionExtension, *, recipe: str) -> None:
    """Copy verified finished work with an explicit parent and projection identity."""
    parent = extension.parent
    store_object(
        run / "projection-extension.json",
        {
            "schemaVersion": 1,
            "parentRun": str(parent.parent),
            "parentSnapshotSha256": canonical_digest(parent.snapshot),
            "projectionPlanSha256": canonical_digest(extension.plan.model_dump(mode="json")),
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


def restore_job_order(parent: Path, planned: list[CandidateJob]) -> list[CandidateJob]:
    """Reconcile projected repair/continuation jobs without changing their logical order."""
    raw = load_object(parent / "jobs.json")
    if not isinstance(raw, list):
        raise ModelError("INTEGRITY_FAILED", "Projected parent job plan is invalid")
    old = [CandidateJob.model_validate(value) for value in raw]

    def key(job: CandidateJob) -> str:
        return canonical_digest(job.model_dump(mode="json", exclude={"jobId"}))

    by_key = {key(job): job for job in planned}
    if len(by_key) != len(planned) or {key(job) for job in old} != set(by_key):
        raise ModelError("INTEGRITY_FAILED", "Projected logical job membership changed")
    return [by_key[key(job)] for job in old]

"""Offline, explicit migration of native decision data and deterministic additions."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from foliqant.decisions import DecisionInput, DecisionOutput, validate_decision_output

from ..artifacts import load_verified_artifact, require_disjoint_output, sha256_file
from ..contracts.base import canonical_digest
from ..contracts.inputs import DataRecord
from ..errors import ModelError
from ..setup import check_workspace_git_policy
from .contracts import CandidateJob, CandidateOutcome, ImportedRecord
from .decision_runner import _seeds_and_families
from .decision_seeds import DecisionSeed, decision_seed_recipe_digest
from .endpoint import EndpointModelIdentity
from .migration_contracts import (
    MigrationEntry,
    MigrationPending,
    MigrationPlan,
    MigrationProvenance,
    MigrationReview,
    MigrationSource,
)
from .projection_preparation import _load_parent, _validate_parent, build_projection_plan
from .question_variants import QUESTION_VARIANT_RECIPE_VERSION, derive_question_variants
from .runner import _preserve_family_rights, _publish
from .source_projections import PROJECTION_VERSION
from .storage import (
    canonical_bytes,
    ensure_directory,
    load_object,
    run_lock,
    store_object,
    write_once,
)

MIGRATION_VERSION = "native-migration-v1"


def migration_recipe_digest() -> str:
    """Identify deterministic task transformations separately from model transport."""
    return canonical_digest(
        {
            "version": MIGRATION_VERSION,
            "seeds": decision_seed_recipe_digest(),
            "projections": PROJECTION_VERSION,
            "questions": QUESTION_VARIANT_RECIPE_VERSION,
        }
    )


def _inventory(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Migration input must be a regular directory")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Migration input cannot contain symlinks")
        if path.is_file() and path.name != "run.lock":
            files[str(path.relative_to(root))] = sha256_file(path)[1]
    return files


def _verify_parent(plan: MigrationPlan) -> None:
    if _inventory(Path(plan.parentRun)) != plan.parentFiles:
        raise ModelError("INTEGRITY_FAILED", "Migration parent changed")
    manifest = load_verified_artifact(Path(plan.parentRun) / "datasets/native-decisions")
    if manifest.root.artifactId != plan.parentArtifactId:
        raise ModelError("INTEGRITY_FAILED", "Migration parent artifact differs")


def _task_key(seed: DecisionSeed) -> str:
    source_keys = [tag for tag in seed.parent.tags if tag.startswith("original-source-record:")]
    if source_keys:
        if len(source_keys) != 1:
            raise ModelError("INTEGRITY_FAILED", "Native task has ambiguous source identity")
        return source_keys[0]
    return seed.parent.id


def _native_record_valid(record: DataRecord) -> None:
    try:
        task = DecisionInput.model_validate_json(record.messages[-2].content, strict=True)
        answer = DecisionOutput.model_validate_json(record.messages[-1].content, strict=True)
        if validate_decision_output(task, answer):
            raise ValueError("invalid task result")
    except (ValueError, IndexError) as error:
        raise ModelError(
            "DATA_RECORD_INVALID", "Migrated record fails native validation"
        ) from error


def _source_links(
    record: DataRecord,
    old_seed: DecisionSeed,
    raw: dict[str, ImportedRecord],
    hashes: dict[str, str],
) -> list[MigrationSource]:
    keys = [
        tag.split(":", 1)[1]
        for tag in old_seed.parent.tags
        if tag.startswith("original-source-record:")
    ]
    if keys:
        row = raw[keys[0]]
        return [
            MigrationSource(
                datasetId=row.record.sourceId,
                recordId=row.record.id,
                originalId=row.originalId,
                snapshotSha256=hashes[f"sources/{row.record.sourceId}.json"],
                rightsSourceId=record.sourceId,
            )
        ]
    return [
        MigrationSource(
            datasetId="foliqant-decisions",
            recordId=old_seed.parent.id,
            originalId=old_seed.parent.id,
            snapshotSha256=hashes["native-seeds.json"],
            rightsSourceId=record.sourceId,
        )
    ]


def _deduplicate(
    entries: list[MigrationEntry],
) -> tuple[list[MigrationEntry], list[MigrationReview]]:
    """Prefer existing rows and reject cross-family or conflicting derived tasks."""
    kept: list[MigrationEntry] = []
    review: list[MigrationReview] = []
    tasks: dict[str, MigrationEntry] = {}
    for entry in entries:
        record = entry.record
        prompt = canonical_digest(
            [message.model_dump(mode="json") for message in record.messages[:-1]]
        )
        previous = tasks.get(prompt)
        if previous is not None:
            if previous.record.familyId != record.familyId:
                raise ModelError("LEAKAGE_DETECTED", "Derived task crosses frozen families")
            if previous.record.messages[-1] != record.messages[-1]:
                raise ModelError("DATA_RECORD_INVALID", "Derived task has conflicting targets")
            review.append(MigrationReview(recordId=record.id, reason="duplicate-derived-task"))
            continue
        tasks[prompt] = entry
        kept.append(entry)
    return kept, review


def _build_plan(parent: Path) -> MigrationPlan:
    from .migration_transform import transform_record

    before = _inventory(parent)
    completed = load_object(parent / "completed-report.json")
    if not isinstance(completed, dict) or completed.get("status") != "completed":
        raise ModelError("ARGUMENT_INVALID", "Migration requires a completed native run")
    snapshot = _load_parent(parent)
    _validate_parent(snapshot)
    manifest = load_verified_artifact(parent / "datasets/native-decisions")
    if manifest.root.kind != "dataset":
        raise ModelError("INTEGRITY_FAILED", "Migration requires a dataset artifact")
    if not any(
        item.get("artifactId") == manifest.root.artifactId
        for item in cast(list[dict[str, object]], completed.get("datasets", []))
    ):
        raise ModelError("INTEGRITY_FAILED", "Completed run does not identify its dataset")
    current, current_families, _, _ = _seeds_and_families(
        snapshot.config,
        snapshot.plan,
        _preserve_family_rights([batch.source for batch in snapshot.batches], snapshot.plan),
    )
    current_by_key = {_task_key(seed): seed for seed in current}
    old_by_id = {seed.parent.id: seed for seed in snapshot.seeds}
    raw = {row.record.id: row for row in snapshot.plan.records}
    families = dict(snapshot.families)
    for family, assignment in current_families.items():
        if family in families and families[family] != assignment:
            raise ModelError("DATA_PARTITION_INVALID", "Migration would change a frozen split")
    rights = {source.id: source for source in manifest.root.details.datasetConfig.sources}
    entries: list[MigrationEntry] = []
    review: list[MigrationReview] = []
    pending: dict[str, MigrationPending] = {}
    for split in ("train", "validation", "calibration", "test"):
        path = (
            parent
            / "datasets/native-decisions"
            / getattr(manifest.root.details.recordFiles, split).path
        )
        for line in path.read_text(encoding="utf-8").splitlines():
            record = DataRecord.model_validate_json(line, strict=True)
            parent_id = record.generation.parentRecordIds[0] if record.generation else record.id
            if parent_id not in old_by_id:
                raise ModelError("INTEGRITY_FAILED", "Published row has no original seed")
            old_seed = old_by_id[parent_id]
            new_seed = current_by_key.get(_task_key(old_seed))
            links = _source_links(record, old_seed, raw, before)
            if new_seed is None:
                review.append(
                    MigrationReview(recordId=record.id, reason="current-task-unavailable")
                )
                continue
            transformed = transform_record(record, old_seed, new_seed)
            if transformed.record is None:
                if split == "train":
                    pending[new_seed.parent.id] = MigrationPending(
                        seed=new_seed,
                        reason=transformed.reason,
                        sources=links,
                    )
                else:
                    review.append(MigrationReview(recordId=record.id, reason=transformed.reason))
                continue
            result = transformed.record
            _native_record_valid(result)
            status: Literal["source-annotation", "historical-accepted", "authored-reference"] = (
                "source-annotation"
                if transformed.operation == "reprojected"
                else (
                    "historical-accepted" if record.generation is not None else "authored-reference"
                )
            )
            entries.append(
                MigrationEntry(
                    record=result,
                    provenance=MigrationProvenance(
                        recordId=result.id,
                        operation=transformed.operation,
                        evidenceStatus=status,
                        parentRecordIds=[record.id],
                        sources=links,
                        rule=transformed.reason,
                    ),
                )
            )

    raw_jobs = load_object(parent / "jobs.json")
    if not isinstance(raw_jobs, list):
        raise ModelError("INTEGRITY_FAILED", "Parent jobs must be an array")
    jobs = [CandidateJob.model_validate(value) for value in raw_jobs]
    counts: Counter[str] = Counter()
    for job in jobs:
        outcome = CandidateOutcome.model_validate(
            load_object(parent / "outcomes" / (job.jobId + ".json"))
        )
        if outcome.jobId != job.jobId:
            raise ModelError("INTEGRITY_FAILED", "Parent outcome differs from its job")
        counts[outcome.status] += 1
        if outcome.status != "quarantined":
            continue
        old_seed = old_by_id[job.parentRecordId]
        current_seed = current_by_key.get(_task_key(old_seed))
        # A former truth predicate is a different task from classifying an NLI relation.
        changed_relation = old_seed.scenario == "projection:wanli" and (
            current_seed is not None and old_seed.input.questions != current_seed.input.questions
        )
        if current_seed is None or (
            outcome.reason == "solver-semantic-mismatch" and not changed_relation
        ):
            review.append(
                MigrationReview(
                    recordId=old_seed.parent.id, reason=outcome.reason, previousJobId=job.jobId
                )
            )
            continue
        pending[current_seed.parent.id] = MigrationPending(
            seed=current_seed,
            reason="changed-relation-task" if changed_relation else outcome.reason,
            previousJobId=job.jobId,
            sources=_source_links(current_seed.parent, old_seed, raw, before),
        )
    if counts["accepted"] != completed.get("generatedAccepted") or counts[
        "quarantined"
    ] != completed.get("generatedQuarantined"):
        raise ModelError("INTEGRITY_FAILED", "Completed parent outcome counts differ")

    projection = build_projection_plan(parent)
    for source in projection.sources:
        if source.id in rights and rights[source.id] != source:
            raise ModelError("DATA_RIGHTS_DENIED", "Projection source rights conflict")
        rights[source.id] = source
    for family, assignment in projection.frozenFamilies.items():
        if family in families and families[family] != assignment:
            raise ModelError("DATA_PARTITION_INVALID", "Projection changes a frozen split")
        families[family] = assignment
    for seed in projection.seeds:
        _native_record_valid(seed.parent)
        entries.append(
            MigrationEntry(
                record=seed.parent,
                provenance=MigrationProvenance(
                    recordId=seed.parent.id,
                    operation="source-projection",
                    evidenceStatus="deterministic-computation"
                    if seed.scenario == "projection:tatqa"
                    else "source-annotation",
                    parentRecordIds=[],
                    sources=_source_links(seed.parent, seed, raw, before),
                    rule=PROJECTION_VERSION,
                ),
            )
        )
    originals = list(entries)
    for entry in originals:
        for variant in derive_question_variants(entry.record):
            _native_record_valid(variant.record)
            entries.append(
                MigrationEntry(
                    record=variant.record,
                    provenance=MigrationProvenance(
                        recordId=variant.record.id,
                        operation="question-variant",
                        evidenceStatus="derived-reference",
                        parentRecordIds=[variant.parentRecordId],
                        sources=entry.provenance.sources,
                        rule=QUESTION_VARIANT_RECIPE_VERSION + ":" + variant.rule,
                    ),
                )
            )
    entries, duplicates = _deduplicate(entries)
    review.extend(duplicates)
    published = {entry.record.id for entry in entries}
    pending = {key: item for key, item in pending.items() if key not in published}
    if before != _inventory(parent):
        raise ModelError("INTEGRITY_FAILED", "Migration parent changed during preparation")
    return MigrationPlan(
        recipeSha256=migration_recipe_digest(),
        parentRun=str(parent),
        parentArtifactId=manifest.root.artifactId,
        parentFiles=before,
        config=snapshot.config,
        model=EndpointModelIdentity.model_validate(load_object(parent / "model-identity.json")),
        sources=sorted(rights.values(), key=lambda source: source.id),
        families=dict(sorted(families.items())),
        entries=sorted(entries, key=lambda entry: entry.record.id),
        pending=[pending[key] for key in sorted(pending)],
        review=review,
        projectionCounts={
            **projection.report.selectedSourceCounts,
            **{
                "excluded:" + key: value for key, value in projection.report.exclusionCounts.items()
            },
        },
    )


def publish_migration(path: Path, plan: MigrationPlan) -> dict[str, object]:
    """Publish one immutable dataset and its provenance; the caller owns the run lock."""
    _verify_parent(plan)
    ensure_directory(path)
    store_object(path / "migration-plan.json", plan.model_dump(mode="json"))
    for entry in plan.entries:
        _native_record_valid(entry.record)
    represented = {cast(str, entry.record.familyId) for entry in plan.entries}
    artifact = _publish(
        path,
        "native-decisions",
        [entry.record for entry in plan.entries],
        plan.sources,
        {key: plan.families[key] for key in sorted(represented)},
        plan.config.seed,
    )
    assert artifact.root.kind == "dataset"
    write_once(
        path / "provenance.jsonl",
        b"".join(
            canonical_bytes(entry.provenance.model_dump(mode="json")) for entry in plan.entries
        ),
    )
    store_object(
        path / "source-rights.json", [source.model_dump(mode="json") for source in plan.sources]
    )
    # Keep complete original annotations for every referenced imported record.
    raw_plan = load_object(Path(plan.parentRun) / "source-plan.json")
    if not isinstance(raw_plan, dict) or not isinstance(raw_plan.get("records"), list):
        raise ModelError("INTEGRITY_FAILED", "Parent source plan is invalid")
    source_ids = {link.recordId for entry in plan.entries for link in entry.provenance.sources}
    source_ids.update(link.recordId for item in plan.pending for link in item.sources)
    annotations = [row for row in raw_plan["records"] if row["record"]["id"] in source_ids]
    write_once(
        path / "source-annotations.jsonl", b"".join(canonical_bytes(row) for row in annotations)
    )
    store_object(path / "review.json", [item.model_dump(mode="json") for item in plan.review])
    store_object(path / "pending.json", [item.model_dump(mode="json") for item in plan.pending])
    report = {
        "schemaVersion": 1,
        "status": "completed",
        "recipeSha256": plan.recipeSha256,
        "parentArtifactId": plan.parentArtifactId,
        "artifactId": artifact.root.artifactId,
        "records": len(plan.entries),
        "pendingTasks": len(plan.pending),
        "reviewItems": len(plan.review),
        "operations": dict(
            sorted(Counter(entry.provenance.operation for entry in plan.entries).items())
        ),
        "evidenceStatus": dict(
            sorted(Counter(entry.provenance.evidenceStatus for entry in plan.entries).items())
        ),
        "partitions": artifact.root.details.partitions.model_dump(mode="json"),
        "sourceDatasets": dict(
            sorted(
                Counter(
                    link.datasetId for entry in plan.entries for link in entry.provenance.sources
                ).items()
            )
        ),
        "pendingReasons": dict(sorted(Counter(item.reason for item in plan.pending).items())),
        "reviewReasons": dict(sorted(Counter(item.reason for item in plan.review).items())),
        "projectionCounts": plan.projectionCounts,
    }
    store_object(path / "migration-report.json", report)
    inventory = {
        name: digest
        for name, digest in _inventory(path).items()
        if name != "migration-complete.json"
    }
    store_object(
        path / "migration-complete.json",
        {
            "schemaVersion": 1,
            "planSha256": canonical_digest(plan.model_dump(mode="json")),
            "artifactId": artifact.root.artifactId,
            "files": inventory,
        },
    )
    _verify_parent(plan)
    return {
        "command": "migrate-decisions",
        "migrationPath": str(path),
        "datasetPath": str(path / "datasets/native-decisions"),
        "artifactId": artifact.root.artifactId,
        "reportPath": str(path / "migration-report.json"),
        "records": len(plan.entries),
        "pendingTasks": len(plan.pending),
        "reviewItems": len(plan.review),
    }


def load_migration(path: Path) -> MigrationPlan:
    """Verify a completed migration, original ancestry and all exported row files."""
    path = path.expanduser().absolute()
    try:
        plan = MigrationPlan.model_validate(load_object(path / "migration-plan.json"), strict=True)
        completed = load_object(path / "migration-complete.json")
        if not isinstance(completed, dict) or not isinstance(completed.get("files"), dict):
            raise ValueError("invalid completion receipt")
        if completed["planSha256"] != canonical_digest(plan.model_dump(mode="json")):
            raise ValueError("plan changed")
        actual = _inventory(path)
        actual.pop("migration-complete.json", None)
        if actual != completed["files"]:
            raise ValueError("migration files changed")
        artifact = load_verified_artifact(path / "datasets/native-decisions")
        if artifact.root.artifactId != completed["artifactId"]:
            raise ValueError("artifact changed")
    except (ValidationError, ValueError, KeyError, TypeError) as error:
        raise ModelError("INTEGRITY_FAILED", "Migration snapshot is invalid") from error
    _verify_parent(plan)
    return plan


def migrate_decisions(from_run: Path, output: Path | None = None) -> dict[str, object]:
    """Migrate a completed native run offline, adding sourced deterministic tasks."""
    parent = from_run.expanduser().absolute()
    with run_lock(parent):
        plan = _build_plan(parent)
    identity = canonical_digest(plan.model_dump(mode="json"))
    destination = (
        output.expanduser().absolute()
        if output
        else parent.parent.parent / "migrations" / identity[:16]
    )
    require_disjoint_output(destination, [parent])
    check_workspace_git_policy(destination)
    ensure_directory(destination)
    with run_lock(destination):
        if (destination / "migration-complete.json").exists():
            existing = load_migration(destination)
            if existing != plan:
                raise ModelError("INTEGRITY_FAILED", "Migration output belongs to different inputs")
        return publish_migration(destination, plan)

"""Offline preparation of new native projections from an immutable parent run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from ..artifacts import require_disjoint_output, sha256_file
from ..contracts.base import canonical_digest
from ..contracts.cli import ProjectionPreparationResult
from ..contracts.inputs import FrozenFamilyAssignment, ResolvedSourceDeclaration
from ..errors import ModelError
from .contracts import CurationConfig, CurationPlan, ImportedRecord, SourceBatch
from .decision_contracts import semantic_signature
from .decision_seeds import DecisionSeed
from .planning import freeze_sources
from .projection_contracts import ProjectionPlan, ProjectionReport, ProjectionSourceCounts
from .runner import _preserve_family_rights
from .source_projections import (
    PROJECTION_VERSION,
    project_auxiliary,
    projection_priority,
)
from .sources import _load_catalog, source_catalog_digest
from .storage import load_object, store_object

_PLAN_NAME = "projection-plan.json"
_REPORT_NAME = "projection-report.json"
_DERIVATIVE_RESTRICTION = "Native projection is diagnostic, not human adjudication."
_PILOT_SOURCE_QUOTAS = {
    "multidogo-finance": 8,
    "tatqa": 8,
    "typed-decisions": 16,
}
_PILOT_TYPED_WORKFLOWS = (
    "workflow:agent_trace_observability",
    "workflow:customer_service",
    "workflow:invoice_processing",
    "workflow:security_incidents",
)


@dataclass(frozen=True)
class _ParentSnapshot:
    config: CurationConfig
    plan: CurationPlan
    seeds: list[DecisionSeed]
    families: dict[str, FrozenFamilyAssignment]
    batches: list[SourceBatch]
    file_hashes: dict[str, str]
    source_hashes: dict[str, str]


@dataclass(frozen=True)
class _EligibleProjection:
    row: ImportedRecord
    seed: DecisionSeed
    exactTaskSha256: str
    answerBearingTaskSha256: str
    targetSha256: str


def _validated[T](model: type[T], value: object, label: str) -> T:
    try:
        return model.model_validate(value, strict=True)  # type: ignore[attr-defined, no-any-return]
    except (ValidationError, ValueError, TypeError) as error:
        raise ModelError("INTEGRITY_FAILED", f"Parent {label} is invalid") from error


def _file_digest(path: Path) -> str:
    return sha256_file(path)[1]


def _expected_parent_files(parent: Path, source_ids: list[str]) -> dict[str, Path]:
    result = {
        "configuration.json": parent / "configuration.json",
        "native-families.json": parent / "native-families.json",
        "native-seeds.json": parent / "native-seeds.json",
        "source-plan.json": parent / "source-plan.json",
    }
    result.update(
        {
            f"sources/{source_id}.json": parent / "sources" / f"{source_id}.json"
            for source_id in source_ids
        }
    )
    return dict(sorted(result.items()))


def _hash_parent_files(parent: Path, source_ids: list[str]) -> dict[str, str]:
    return {
        name: _file_digest(path)
        for name, path in _expected_parent_files(parent, source_ids).items()
    }


def _load_parent(parent: Path) -> _ParentSnapshot:
    config_path = parent / "configuration.json"
    config_before = _file_digest(config_path)
    config = _validated(
        CurationConfig,
        load_object(config_path),
        "configuration snapshot",
    )
    if config_before != _file_digest(config_path):
        raise ModelError("INTEGRITY_FAILED", "Parent configuration changed while reading")
    if config.decisionData is None:
        raise ModelError("CONFIG_INVALID", "Parent run has no native decision-data settings")
    source_ids = [selection.id for selection in config.sources]
    file_hashes = _hash_parent_files(parent, source_ids)
    repeated_config = _validated(
        CurationConfig,
        load_object(config_path),
        "configuration snapshot",
    )
    if repeated_config != config:
        raise ModelError("INTEGRITY_FAILED", "Parent configuration changed while reading")
    plan = _validated(
        CurationPlan,
        load_object(parent / "source-plan.json"),
        "source plan",
    )
    seed_values = load_object(parent / "native-seeds.json")
    if not isinstance(seed_values, list):
        raise ModelError("INTEGRITY_FAILED", "Parent native seeds are invalid")
    seeds = [_validated(DecisionSeed, value, "native seed") for value in seed_values]
    family_values = load_object(parent / "native-families.json")
    if not isinstance(family_values, dict) or not all(
        isinstance(key, str) for key in family_values
    ):
        raise ModelError("INTEGRITY_FAILED", "Parent native families are invalid")
    families = {
        key: _validated(FrozenFamilyAssignment, value, "native family")
        for key, value in sorted(cast(dict[str, object], family_values).items())
    }
    batches = [
        _validated(
            SourceBatch,
            load_object(parent / "sources" / f"{source_id}.json"),
            "source snapshot",
        )
        for source_id in source_ids
    ]
    if file_hashes != _hash_parent_files(parent, source_ids):
        raise ModelError("INTEGRITY_FAILED", "Parent inputs changed during projection planning")
    source_hashes = {
        source_id: file_hashes[f"sources/{source_id}.json"] for source_id in sorted(source_ids)
    }
    return _ParentSnapshot(
        config=config,
        plan=plan,
        seeds=seeds,
        families=families,
        batches=batches,
        file_hashes=file_hashes,
        source_hashes=source_hashes,
    )


def _validate_parent(snapshot: _ParentSnapshot) -> None:
    config = snapshot.config
    plan = snapshot.plan
    config_sha256 = canonical_digest(config.model_dump(mode="json"))
    catalog_sha256 = source_catalog_digest()
    if plan.configurationSha256 != config_sha256:
        raise ModelError("INTEGRITY_FAILED", "Parent source plan configuration does not match")
    if plan.catalogSha256 != catalog_sha256:
        raise ModelError("INTEGRITY_FAILED", "Parent source plan catalog does not match")
    selected_ids = [selection.id for selection in config.sources]
    batch_ids = [batch.source.id for batch in snapshot.batches]
    if batch_ids != selected_ids or len(batch_ids) != len(set(batch_ids)):
        raise ModelError("INTEGRITY_FAILED", "Parent source snapshots do not match configuration")
    catalog = _load_catalog()
    for batch in snapshot.batches:
        entry = next(
            (value for key, value in catalog.sources.items() if key == batch.source.id), None
        )
        if (
            entry is None
            or batch.source != entry.source
            or batch.revision != entry.revision
            or batch.assets != entry.assets
        ):
            raise ModelError("INTEGRITY_FAILED", "Parent source snapshot does not match catalog")
    rebuilt = freeze_sources(
        snapshot.batches,
        seed=config.seed,
        configuration_digest=config_sha256,
        catalog_digest=catalog_sha256,
    )
    if rebuilt != plan:
        raise ModelError("INTEGRITY_FAILED", "Parent frozen source plan cannot be reproduced")
    seed_ids: set[str] = set()
    group_owners: dict[str, str] = {}
    for seed in snapshot.seeds:
        parent = seed.parent
        if parent.id in seed_ids:
            raise ModelError("INTEGRITY_FAILED", "Parent native seed IDs are not unique")
        seed_ids.add(parent.id)
        family = parent.familyId
        if family is None or family not in snapshot.families:
            raise ModelError("INTEGRITY_FAILED", "Parent native seed lacks its frozen family")
        for key in parent.groupKeys:
            owner = group_owners.setdefault(key, family)
            if owner != family:
                raise ModelError("INTEGRITY_FAILED", "Parent native groups cross frozen families")


def _task_identity(seed: DecisionSeed) -> tuple[str, str, str]:
    task = seed.input.model_dump(mode="json")
    exact = canonical_digest(task)
    allowed = {
        source_id for question in seed.input.questions for source_id in question.allowedSourceIds
    }
    answer_bearing = canonical_digest(
        {
            **task,
            "state": {
                **task["state"],
                "sources": [
                    source for source in task["state"]["sources"] if source["id"] in allowed
                ],
            },
        }
    )
    target = canonical_digest(semantic_signature(seed.oracle))
    return exact, answer_bearing, target


def _is_multi_intent(seed: DecisionSeed) -> bool:
    return any(
        result.type == "multiselect"
        and result.answer is not None
        and len(result.answer.optionIds) > 1
        for result in seed.oracle.results
    )


def _increment_exclusion(
    source_exclusions: dict[str, Counter[str]], source_id: str, reason: str
) -> None:
    if not reason:
        raise ModelError("INTERNAL_ERROR", "Projection exclusion reason is empty")
    source_exclusions[source_id][reason] += 1


def _deduplicate_eligible(
    candidates: list[_EligibleProjection],
    existing_tasks: dict[str, tuple[str, str]],
    frozen_families: dict[str, FrozenFamilyAssignment],
    source_exclusions: dict[str, Counter[str]],
) -> list[_EligibleProjection]:
    """Resolve whole answer-bearing groups before any selection cap is applied."""

    groups: dict[str, list[_EligibleProjection]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.answerBearingTaskSha256, []).append(candidate)
    retained: list[_EligibleProjection] = []
    for answer_bearing in sorted(groups):
        group = groups[answer_bearing]
        prior = existing_tasks.get(answer_bearing)
        if prior is not None:
            for candidate in group:
                reason = (
                    "existing-task-conflict"
                    if candidate.targetSha256 != prior[1]
                    else "exact-duplicate"
                    if candidate.exactTaskSha256 == prior[0]
                    else "unreachable-source-only-duplicate"
                )
                _increment_exclusion(source_exclusions, candidate.row.record.sourceId, reason)
            continue
        targets = {candidate.targetSha256 for candidate in group}
        if len(targets) != 1:
            for candidate in group:
                _increment_exclusion(
                    source_exclusions,
                    candidate.row.record.sourceId,
                    "conflicting-target",
                )
            continue
        families = {cast(str, candidate.row.record.familyId) for candidate in group}
        splits = {frozen_families[family].split for family in families}
        if len(splits) != 1:
            for candidate in group:
                _increment_exclusion(
                    source_exclusions,
                    candidate.row.record.sourceId,
                    "cross-split-alias",
                )
            continue
        if len(families) != 1:
            for candidate in group:
                _increment_exclusion(
                    source_exclusions,
                    candidate.row.record.sourceId,
                    "cross-family-alias",
                )
            continue
        representative = min(group, key=lambda candidate: candidate.row.record.id)
        retained.append(representative)
        for candidate in group:
            if candidate is representative:
                continue
            reason = (
                "exact-duplicate"
                if candidate.exactTaskSha256 == representative.exactTaskSha256
                else "unreachable-source-only-duplicate"
            )
            _increment_exclusion(source_exclusions, candidate.row.record.sourceId, reason)
    return retained


def _projection_rights(
    batches: list[SourceBatch], plan: CurationPlan, selected_source_ids: set[str]
) -> list[ResolvedSourceDeclaration]:
    rights: list[ResolvedSourceDeclaration] = []
    preserved = _preserve_family_rights([batch.source for batch in batches], plan)
    for source in preserved:
        if source.id not in selected_source_ids:
            continue
        rights.append(
            source.model_copy(
                update={
                    "id": "native-" + source.id,
                    "restrictions": sorted(set(source.restrictions) | {_DERIVATIVE_RESTRICTION}),
                }
            )
        )
    return sorted(rights, key=lambda source: source.id)


def _plan_identity(plan: ProjectionPlan) -> str:
    payload = plan.model_dump(mode="json")
    payload.pop("parentRun")
    return canonical_digest(payload)


def _typed_workflow(seed: DecisionSeed) -> str | None:
    values = [tag for tag in seed.parent.tags if tag in _PILOT_TYPED_WORKFLOWS]
    if len(values) > 1:
        raise ModelError("INTEGRITY_FAILED", "Typed projection has multiple workflow tags")
    return values[0] if values else None


def build_projection_plan(parent: Path, *, pilot: bool = False) -> ProjectionPlan:
    """Purely derive a projection plan from saved immutable parent inputs."""

    parent = parent.expanduser().absolute()
    snapshot = _load_parent(parent)
    _validate_parent(snapshot)
    config = snapshot.config
    settings = config.decisionData
    assert settings is not None

    raw_source_counts = Counter(row.record.sourceId for row in snapshot.plan.records)
    eligible_source_counts: Counter[str] = Counter()
    eligible_multi_intent_source_counts: Counter[str] = Counter()
    selected_source_counts: Counter[str] = Counter()
    selected_multi_intent_source_counts: Counter[str] = Counter()
    eligible_languages: Counter[str] = Counter()
    selected_languages: Counter[str] = Counter()
    eligible_splits: Counter[str] = Counter()
    selected_splits: Counter[str] = Counter()
    selected_families: Counter[str] = Counter()
    question_types: Counter[str] = Counter()
    selected_question_types: Counter[str] = Counter()
    source_exclusions = {source_id: Counter[str]() for source_id in sorted(raw_source_counts)}

    existing_tasks: dict[str, tuple[str, str]] = {}
    group_owners: dict[str, str] = {}
    for seed in snapshot.seeds:
        exact, answer_bearing, target = _task_identity(seed)
        previous = existing_tasks.get(answer_bearing)
        if previous is not None:
            detail = "conflicting targets" if previous[1] != target else "duplicate content"
            raise ModelError("INTEGRITY_FAILED", f"Parent native seeds contain {detail}")
        existing_tasks[answer_bearing] = (exact, target)
        family = cast(str, seed.parent.familyId)
        for key in seed.parent.groupKeys:
            owner = group_owners.setdefault(key, family)
            if owner != family:
                raise ModelError("INTEGRITY_FAILED", "Parent native content groups cross families")

    tatqa_representatives: dict[str, str] = {}
    for row in snapshot.plan.records:
        tatqa_family = row.record.familyId
        if row.record.sourceId == "tatqa" and tatqa_family is not None:
            current = tatqa_representatives.get(tatqa_family)
            if current is None or row.record.id < current:
                tatqa_representatives[tatqa_family] = row.record.id

    ordered_rows = sorted(
        snapshot.plan.records,
        key=lambda row: (
            projection_priority(row)[0],
            canonical_digest({"seed": config.seed, "recordId": row.record.id}),
            projection_priority(row)[1],
        ),
    )
    eligible: list[_EligibleProjection] = []
    for row in ordered_rows:
        source_id = row.record.sourceId
        result = project_auxiliary(row)
        if result.seed is None:
            _increment_exclusion(source_exclusions, source_id, result.reason)
            continue
        projected = result.seed
        row_family = row.record.familyId
        if (
            row_family is None
            or row_family not in snapshot.plan.frozenFamilies
            or projected.parent.familyId != row_family
            or projected.parent.groupKeys != row.record.groupKeys
            or projected.parent.sourceId != "native-" + source_id
            or projected.mode != "annotate"
        ):
            raise ModelError("INTEGRITY_FAILED", "Source projection changed frozen lineage")
        eligible_source_counts[source_id] += 1
        if _is_multi_intent(projected):
            eligible_multi_intent_source_counts[source_id] += 1
        eligible_languages[projected.parent.language] += 1
        eligible_splits[snapshot.plan.frozenFamilies[row_family].split] += 1
        question_types.update(question.type for question in projected.input.questions)
        exact, answer_bearing, target = _task_identity(projected)
        eligible.append(
            _EligibleProjection(
                row=row,
                seed=projected,
                exactTaskSha256=exact,
                answerBearingTaskSha256=answer_bearing,
                targetSha256=target,
            )
        )

    selected: list[_EligibleProjection] = []
    pilot_workflows: Counter[str] = Counter()
    deduplicated = _deduplicate_eligible(
        eligible,
        existing_tasks,
        snapshot.plan.frozenFamilies,
        source_exclusions,
    )
    for candidate in sorted(
        deduplicated,
        key=lambda item: (
            projection_priority(item.row)[0],
            canonical_digest({"seed": config.seed, "recordId": item.row.record.id}),
            projection_priority(item.row)[1],
        ),
    ):
        row = candidate.row
        projected = candidate.seed
        source_id = row.record.sourceId
        family = cast(str, row.record.familyId)
        if source_id == "tatqa" and tatqa_representatives[family] != row.record.id:
            _increment_exclusion(source_exclusions, source_id, "tatqa-family-sibling")
            continue
        if projected.parent.language not in config.generation.languages:
            _increment_exclusion(source_exclusions, source_id, "language-excluded")
            continue
        split = snapshot.plan.frozenFamilies[family].split
        if pilot and split != "train":
            _increment_exclusion(source_exclusions, source_id, "pilot-held-out")
            continue
        if pilot and source_id not in _PILOT_SOURCE_QUOTAS:
            _increment_exclusion(source_exclusions, source_id, "pilot-source-excluded")
            continue
        workflow: str | None = None
        if pilot and source_id == "typed-decisions":
            workflow = _typed_workflow(projected)
            if workflow is None:
                _increment_exclusion(source_exclusions, source_id, "pilot-workflow-excluded")
                continue
            if pilot_workflows[workflow] >= 4:
                _increment_exclusion(source_exclusions, source_id, "pilot-workflow-quota")
                continue
        if pilot and selected_source_counts[source_id] >= _PILOT_SOURCE_QUOTAS[source_id]:
            _increment_exclusion(source_exclusions, source_id, "pilot-source-quota")
            continue
        if selected_source_counts[source_id] >= settings.sourceExamplesPerSource:
            _increment_exclusion(source_exclusions, source_id, "over-limit")
            continue
        for key in projected.parent.groupKeys:
            owner = group_owners.setdefault(key, family)
            if owner != family:
                raise ModelError(
                    "DATA_PARTITION_INVALID", "Projected content group crosses families"
                )
        selected.append(candidate)
        selected_source_counts[source_id] += 1
        if _is_multi_intent(projected):
            selected_multi_intent_source_counts[source_id] += 1
        if workflow is not None:
            pilot_workflows[workflow] += 1
        selected_languages[projected.parent.language] += 1
        selected_splits[snapshot.plan.frozenFamilies[family].split] += 1
        selected_families[family] += 1
        selected_question_types.update(question.type for question in projected.input.questions)

    if pilot:
        missing = {
            source_id: quota - selected_source_counts[source_id]
            for source_id, quota in _PILOT_SOURCE_QUOTAS.items()
            if selected_source_counts[source_id] != quota
        }
        missing.update(
            {
                workflow: 4 - pilot_workflows[workflow]
                for workflow in _PILOT_TYPED_WORKFLOWS
                if pilot_workflows[workflow] != 4
            }
        )
        if missing:
            raise ModelError("OUTPUT_INVALID", "Parent run cannot satisfy the projection pilot")

    source_counts = {
        source_id: ProjectionSourceCounts(
            rawRecords=raw_source_counts[source_id],
            eligibleRecords=eligible_source_counts[source_id],
            eligibleMultiIntentRecords=eligible_multi_intent_source_counts[source_id],
            selectedRecords=selected_source_counts[source_id],
            selectedMultiIntentRecords=selected_multi_intent_source_counts[source_id],
            selectedFamilies=len(
                {
                    candidate.seed.parent.familyId
                    for candidate in selected
                    if candidate.row.record.sourceId == source_id
                }
            ),
            exclusionCounts=dict(sorted(source_exclusions[source_id].items())),
        )
        for source_id in sorted(raw_source_counts)
    }
    exclusions: Counter[str] = Counter()
    for counts in source_exclusions.values():
        exclusions.update(counts)
    report = ProjectionReport(
        rawRecords=len(snapshot.plan.records),
        eligibleRecords=len(eligible),
        selectedRecords=len(selected),
        selectedFamilies=len(selected_families),
        sourceCounts=source_counts,
        selectedSourceCounts=dict(sorted(selected_source_counts.items())),
        eligibleLanguageCounts=dict(sorted(eligible_languages.items())),
        selectedLanguageCounts=dict(sorted(selected_languages.items())),
        eligibleSplitCounts=dict(sorted(eligible_splits.items())),
        selectedSplitCounts=dict(sorted(selected_splits.items())),
        familyCounts=dict(sorted(selected_families.items())),
        questionTypeCounts=dict(sorted(question_types.items())),
        selectedQuestionTypeCounts=dict(sorted(selected_question_types.items())),
        exclusionCounts=dict(sorted(exclusions.items())),
    )
    selected_source_ids = {candidate.row.record.sourceId for candidate in selected}
    return ProjectionPlan(
        projectionVersion=PROJECTION_VERSION,
        selection="pilot" if pilot else "full",
        parentInputsSha256=canonical_digest(snapshot.file_hashes),
        sourcePlanSha256=canonical_digest(snapshot.plan.model_dump(mode="json")),
        configSha256=canonical_digest(config.model_dump(mode="json")),
        parentRun=str(parent),
        sourceSnapshots=snapshot.source_hashes,
        seeds=sorted((candidate.seed for candidate in selected), key=lambda seed: seed.parent.id),
        frozenFamilies={
            family: snapshot.plan.frozenFamilies[family] for family in sorted(selected_families)
        },
        sources=_projection_rights(snapshot.batches, snapshot.plan, selected_source_ids),
        report=report,
    )


def _verify_parent_unchanged(plan: ProjectionPlan) -> None:
    parent = Path(plan.parentRun)
    config = _validated(
        CurationConfig,
        load_object(parent / "configuration.json"),
        "configuration snapshot",
    )
    source_ids = [selection.id for selection in config.sources]
    hashes = _hash_parent_files(parent, source_ids)
    if canonical_digest(hashes) != plan.parentInputsSha256:
        raise ModelError("INTEGRITY_FAILED", "Parent inputs changed before publication")
    observed_sources = {
        source_id: hashes[f"sources/{source_id}.json"] for source_id in sorted(source_ids)
    }
    if observed_sources != plan.sourceSnapshots:
        raise ModelError("INTEGRITY_FAILED", "Parent source snapshots changed before publication")


def load_projection_plan(path: Path) -> ProjectionPlan:
    """Load one plan envelope and reproduce it from its immutable parent snapshot."""

    selected = path.expanduser().absolute()
    plan_path = selected / _PLAN_NAME if selected.is_dir() else selected
    plan = _validated(ProjectionPlan, load_object(plan_path), "projection plan")
    report = _validated(
        ProjectionReport,
        load_object(plan_path.parent / _REPORT_NAME),
        "projection report",
    )
    if report != plan.report:
        raise ModelError("INTEGRITY_FAILED", "Projection report does not match its plan")
    rebuilt = build_projection_plan(Path(plan.parentRun), pilot=plan.selection == "pilot")
    if _plan_identity(rebuilt) != _plan_identity(plan):
        raise ModelError("INTEGRITY_FAILED", "Projection plan cannot be reproduced")
    return plan


def prepare_source_projections(
    from_run: Path, output: Path | None = None, *, pilot: bool = False
) -> ProjectionPreparationResult:
    """Build and immutably publish an offline projection plan without model calls."""

    parent = from_run.expanduser().absolute()
    plan = build_projection_plan(parent, pilot=pilot)
    plan_id = _plan_identity(plan)
    destination = (
        output.expanduser().absolute()
        if output is not None
        else parent.parent.parent / "projections" / plan_id
    )
    require_disjoint_output(destination, [parent])
    _verify_parent_unchanged(plan)
    report_path = destination / _REPORT_NAME
    plan_path = destination / _PLAN_NAME
    store_object(report_path, plan.report.model_dump(mode="json"))
    _verify_parent_unchanged(plan)
    store_object(plan_path, plan.model_dump(mode="json"))
    verified = load_projection_plan(plan_path)
    return ProjectionPreparationResult(
        command="prepare-source-projections",
        planPath=str(plan_path),
        reportPath=str(report_path),
        rawRecords=verified.report.rawRecords,
        eligibleRecords=verified.report.eligibleRecords,
        selectedRecords=verified.report.selectedRecords,
        selectedFamilies=verified.report.selectedFamilies,
    )

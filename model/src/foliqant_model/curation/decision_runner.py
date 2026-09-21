"""Serial, resumable native decision-data generation using existing artifact boundaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from foliqant.decisions import (
    DecisionInput,
    DecisionOutput,
    semantic_signature,
    validate_decision_output,
)

from ..artifacts import write_private_json
from ..contracts.base import canonical_digest
from ..contracts.cli import CurateResult, ErrorLocation
from ..contracts.inputs import DataRecord, FrozenFamilyAssignment, ResolvedSourceDeclaration
from ..errors import ModelError
from ..workspace import model_workspace
from .continuation import (
    Continuation,
    continuation_jobs,
    initialize_continuation,
    load_continuation,
    verify_continuation_inputs,
)
from .contracts import (
    CandidateJob,
    CandidateOutcome,
    CurationConfig,
    CurationPlan,
    ImportedRecord,
    SourceBatch,
)
from .decision_generation import (
    canonical_decision_output,
    decision_generation_recipe_digest,
    generate_decision_candidate,
    validate_decision_rewrite,
)
from .decision_seeds import (
    AUTHORED_CASES_PER_SCENARIO,
    DecisionSeed,
    build_authored_seeds,
    project_source,
)
from .endpoint import EndpointModelIdentity, _select_model, discover_models
from .planning import freeze_sources
from .projection_extension import (
    append_projections,
    extension_jobs,
    initialize_extension,
    load_inherited_projection,
    load_projection_extension,
    restore_job_order,
)
from .runner import _created, _derived_sources, _preserve_family_rights, _publish, _source_batches
from .runtime import CurationControl, cached_request_count
from .sources import source_catalog_digest
from .storage import ensure_directory, load_object, run_lock, store_object

_RUN_VERSION = "native-decisions-v5"

# A bounded pilot exercises every answer type before the remaining coverage cells.
_PILOT_SCENARIOS = (
    "choice-answerable",
    "multiselect",
    "predicate-unknown",
    "ordinal-conflict",
    "requests-same",
    "requests-partial",
    "conditional-unresolved",
    "adequacy-omitted-request",
)


def _cell(seed: DecisionSeed) -> str:
    return seed.scenario + ":" + seed.parent.language


def build_decision_jobs(
    config: CurationConfig,
    seeds: list[DecisionSeed],
    families: dict[str, FrozenFamilyAssignment],
) -> list[CandidateJob]:
    """Round-robin coverage cells, with no held-out state submitted to inference."""
    cells: dict[str, list[DecisionSeed]] = defaultdict(list)
    for seed in seeds:
        family = seed.parent.familyId
        if family is None or family not in families:
            raise ModelError("DATA_PARTITION_INVALID", "Native seed lacks a frozen family")
        if families[family].split == "train":
            cells[_cell(seed)].append(seed)
    for rows in cells.values():
        rows.sort(key=lambda row: canonical_digest({"seed": config.seed, "id": row.parent.id}))
    result: list[CandidateJob] = []
    recipe = decision_generation_recipe_digest()
    configuration = canonical_digest(config.model_dump(mode="json"))
    priority = {scenario: index for index, scenario in enumerate(_PILOT_SCENARIOS)}
    ordered_cells = sorted(
        cells, key=lambda cell: (priority.get(cell.rsplit(":", 1)[0], len(priority)), cell)
    )
    for group in zip_longest(*(cells[key] for key in ordered_cells)):
        for seed in group:
            if seed is None:
                continue
            family = cast(str, seed.parent.familyId)
            payload = {
                "parentRecordId": seed.parent.id,
                "familyId": family,
                "split": "train",
                "language": seed.parent.language,
                "purpose": "decision-training",
                "operation": "native-decision",
            }
            result.append(
                CandidateJob.model_validate(
                    {
                        **payload,
                        "jobId": canonical_digest(
                            {"job": payload, "configuration": configuration, "recipe": recipe}
                        ),
                    }
                )
            )
            if len(result) >= config.generation.maxCandidates:
                return result
    return result


def _authored_source() -> ResolvedSourceDeclaration:
    return ResolvedSourceDeclaration(
        id="foliqant-decisions",
        license="Project-authored research scenarios",
        licenseEvidence="Foliqant versioned native decision seed builder",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="unknown",
        attribution="Foliqant",
        restrictions=["Synthetic research material; no financial or commercial qualification."],
    )


def _task_identities(seed: DecisionSeed) -> tuple[str, str]:
    """Return exact and question-reachable task identities without semantic guessing."""
    task = seed.input.model_dump(mode="json")
    exact = canonical_digest(task)
    allowed = {
        source_id for question in seed.input.questions for source_id in question.allowedSourceIds
    }
    projected = {
        **task,
        "state": {
            **task["state"],
            "sources": [source for source in task["state"]["sources"] if source["id"] in allowed],
        },
    }
    return exact, canonical_digest(projected)


def _seeds_and_families(
    config: CurationConfig, plan: CurationPlan, sources: list[ResolvedSourceDeclaration]
) -> tuple[
    list[DecisionSeed],
    dict[str, FrozenFamilyAssignment],
    list[ResolvedSourceDeclaration],
    dict[str, int],
]:
    settings = config.decisionData
    assert settings is not None
    seeds = build_authored_seeds(
        settings, seed=config.seed, languages=list(config.generation.languages)
    )
    seen_tasks: dict[str, tuple[str, str]] = {}
    for seed in seeds:
        exact, answer_bearing = _task_identities(seed)
        answer = canonical_digest(semantic_signature(seed.oracle))
        previous = seen_tasks.get(answer_bearing)
        if previous is not None:
            detail = "conflicting targets" if previous[1] != answer else "duplicate content"
            raise ModelError("DATA_RECORD_INVALID", f"Authored native examples contain {detail}")
        seen_tasks[answer_bearing] = (exact, answer)
    source = _authored_source()
    authored = freeze_sources(
        [
            SourceBatch(
                source=source,
                revision=_RUN_VERSION,
                assets=[],
                records=[
                    ImportedRecord(
                        record=seed.parent,
                        originalSplit="unspecified",
                        originalId=seed.parent.id,
                        task="decision",
                    )
                    for seed in seeds
                ],
                totalAvailable=len(seeds),
            )
        ],
        seed=config.seed,
        configuration_digest=plan.configurationSha256,
        catalog_digest=plan.catalogSha256,
    )
    by_id = {seed.parent.id: seed for seed in seeds}
    seeds = [
        by_id[row.record.id].model_copy(update={"parent": row.record}) for row in authored.records
    ]
    families = dict(authored.frozenFamilies)
    counts: Counter[str] = Counter()
    for row in sorted(
        plan.records,
        key=lambda item: canonical_digest({"seed": config.seed, "sourceId": item.record.id}),
    ):
        source_id = row.record.sourceId
        projected = project_source(row)
        if projected is None:
            counts[source_id + ":unsupported"] += 1
            continue
        counts[source_id + ":projected"] += 1
        if projected.parent.language not in config.generation.languages:
            counts[source_id + ":language-excluded"] += 1
            continue
        family = cast(str, row.record.familyId)
        if (
            projected.parent.familyId != family
            or projected.parent.groupKeys != row.record.groupKeys
        ):
            raise ModelError("INTEGRITY_FAILED", "Native source projection changed its family")
        exact, answer_bearing = _task_identities(projected)
        answer = canonical_digest(semantic_signature(projected.oracle))
        previous = seen_tasks.get(answer_bearing)
        if previous is not None:
            if previous[1] != answer:
                raise ModelError(
                    "DATA_RECORD_INVALID",
                    "Equivalent projected native tasks have conflicting targets",
                )
            reason = (
                "exact-duplicate" if previous[0] == exact else "unreachable-source-only-duplicate"
            )
            counts[source_id + ":" + reason] += 1
            continue
        if counts[source_id + ":selected"] >= settings.sourceExamplesPerSource:
            counts[source_id + ":over-limit"] += 1
            continue
        seen_tasks[answer_bearing] = (exact, answer)
        families[family] = plan.frozenFamilies[family]
        seeds.append(projected)
        counts[source_id + ":selected"] += 1
    rights = [source]
    used_sources = {seed.parent.sourceId for seed in seeds}
    for declaration in sources:
        if "native-" + declaration.id in used_sources:
            rights.append(
                declaration.model_copy(
                    update={
                        "id": "native-" + declaration.id,
                        "restrictions": sorted(
                            set(declaration.restrictions)
                            | {"Native projection is diagnostic, not human adjudication."}
                        ),
                    }
                )
            )
    return (
        sorted(seeds, key=lambda seed: seed.parent.id),
        dict(sorted(families.items())),
        rights,
        dict(sorted(counts.items())),
    )


def _validate_record(seed: DecisionSeed, record: DataRecord) -> None:
    try:
        task = DecisionInput.model_validate_json(record.messages[-2].content)
        original = DecisionInput.model_validate_json(seed.parent.messages[-2].content)
        output = DecisionOutput.model_validate_json(record.messages[-1].content)
        expected = DecisionOutput.model_validate_json(seed.parent.messages[-1].content)
        problems = validate_decision_output(task, output)
    except (ValueError, ValidationError, IndexError) as error:
        raise ModelError(
            "INTEGRITY_FAILED", "Native record does not match its typed contract"
        ) from error
    if task.questions != original.questions or (seed.mode == "annotate" and task != original):
        raise ModelError("INTEGRITY_FAILED", "Native candidate changed its question contract")
    if problems or semantic_signature(output) != semantic_signature(expected):
        raise ModelError("INTEGRITY_FAILED", "Native candidate changed its reference semantics")
    if seed.mode == "rewrite" and record.generation is not None:
        if validate_decision_rewrite(original, task, rewrite_source_ids=seed.rewriteSourceIds):
            raise ModelError("INTEGRITY_FAILED", "Native derivative failed rewrite validation")
    try:
        canonical_output = canonical_decision_output(seed, task)
    except ModelError as error:
        raise ModelError(
            "INTEGRITY_FAILED", "Native reference support cannot be remapped"
        ) from error
    if output != canonical_output:
        raise ModelError("INTEGRITY_FAILED", "Native output changed its canonical reference prose")


def _validate_seed_partitions(
    seeds: list[DecisionSeed], families: dict[str, FrozenFamilyAssignment]
) -> dict[str, object]:
    """Reject conflicting tasks or split leakage and report actual input diversity."""
    inputs: dict[str, tuple[str, str]] = {}
    groups: dict[str, str] = {}
    split_inputs: dict[str, set[str]] = defaultdict(set)
    for seed in seeds:
        family = seed.parent.familyId
        if family is None or family not in families:
            raise ModelError("DATA_PARTITION_INVALID", "Native seed lacks a frozen family")
        _, task = _task_identities(seed)
        answer = canonical_digest(semantic_signature(seed.oracle))
        previous = inputs.get(task)
        if previous is None:
            inputs[task] = (family, answer)
        elif previous[0] != family:
            raise ModelError("DATA_PARTITION_INVALID", "Equivalent native inputs cross families")
        elif previous[1] != answer:
            raise ModelError(
                "DATA_RECORD_INVALID", "Equivalent native inputs have conflicting targets"
            )
        else:
            raise ModelError("DATA_RECORD_INVALID", "Native seeds repeat an answer-bearing task")
        for key in seed.parent.groupKeys:
            previous_family = groups.setdefault(key, family)
            if previous_family != family:
                raise ModelError("DATA_PARTITION_INVALID", "Native content group crosses families")
        split_inputs[families[family].split].add(task)
    return {
        "authoredExamples": sum(seed.parent.sourceId == "foliqant-decisions" for seed in seeds),
        "authoredExamplesByScenario": dict(
            sorted(
                Counter(
                    seed.scenario for seed in seeds if seed.parent.sourceId == "foliqant-decisions"
                ).items()
            )
        ),
        "uniqueInputs": len(inputs),
        "contentFamilies": len({seed.parent.familyId for seed in seeds}),
        "uniqueInputsBySplit": {key: len(value) for key, value in sorted(split_inputs.items())},
        "templateVariants": len(
            {
                key
                for seed in seeds
                for key in seed.parent.groupKeys
                if key.startswith("foliqant-decisions:template:")
            }
        ),
    }


def run_decision_curation(
    config: CurationConfig,
    workspace: Path | None = None,
    *,
    prepare_only: bool = False,
    offline: bool = False,
    repair_from: Path | None = None,
    continue_from: Path | None = None,
    extend_projections_from: Path | None = None,
    projection_plan: Path | None = None,
    control: CurationControl | None = None,
) -> CurateResult:
    """Publish diagnostic native data; fail the coverage gate without discarding progress."""
    selected_control = control or CurationControl()
    with selected_control.active():
        return _run_decision_curation(
            config,
            workspace,
            prepare_only=prepare_only,
            offline=offline,
            repair_from=repair_from,
            continue_from=continue_from,
            extend_projections_from=extend_projections_from,
            projection_plan=projection_plan,
            control=selected_control,
        )


def _run_decision_curation(
    config: CurationConfig,
    workspace: Path | None,
    *,
    prepare_only: bool,
    offline: bool,
    repair_from: Path | None,
    continue_from: Path | None,
    extend_projections_from: Path | None,
    projection_plan: Path | None,
    control: CurationControl,
) -> CurateResult:
    settings = config.decisionData
    if settings is None:
        raise ModelError("CONFIG_INVALID", "Native curation requires decisionData settings")
    configuration = canonical_digest(config.model_dump(mode="json"))
    catalog = source_catalog_digest()
    run_id = canonical_digest(
        {
            "configuration": configuration,
            "catalog": catalog,
            "version": _RUN_VERSION,
            "generation": decision_generation_recipe_digest(),
        }
    )
    root = model_workspace(workspace)
    if continue_from is not None and repair_from is not None:
        raise ModelError("ARGUMENT_INVALID", "Choose continuation or rejection repair, not both")
    if (extend_projections_from is None) != (projection_plan is None):
        raise ModelError("ARGUMENT_INVALID", "Projection extension requires both parent and plan")
    if extend_projections_from is not None and (
        continue_from is not None or repair_from is not None
    ):
        raise ModelError("ARGUMENT_INVALID", "Choose projection extension, continuation, or repair")
    extension = (
        load_projection_extension(
            root,
            extend_projections_from,
            config,
            cast(Path, projection_plan),
            recipe=decision_generation_recipe_digest(),
        )
        if extend_projections_from is not None
        else None
    )
    continuation = (
        load_continuation(root, continue_from, config, recipe=decision_generation_recipe_digest())
        if continue_from is not None
        else None
    )
    if repair_from is not None and prepare_only:
        raise ModelError("ARGUMENT_INVALID", "A repair pass cannot be prepare-only")
    repair = None
    if repair_from is not None:
        from .repair import load_repair_pass

        repair = load_repair_pass(
            root,
            repair_from,
            config,
            recipe_sha256=decision_generation_recipe_digest(),
            native=True,
        )
        if all(
            outcome.status == "accepted" or outcome.reason == "solver-semantic-mismatch"
            for outcome in repair.outcomes.values()
        ):
            raise ModelError(
                "ARGUMENT_INVALID",
                "No automatically repairable native rejections; semantic disagreements require "
                "reference review or adjudication before a new recipe",
            )
    run = (
        root
        / "curation"
        / (config.name + ("-repair-" + repair.run_id[:12] if repair else "-" + run_id[:12]))
    )
    if continuation is not None:
        run = root / "curation" / (config.name + "-continue-" + continuation.run_id[:12])
    if extension is not None:
        run = root / "curation" / (config.name + "-projections-" + extension.run_id[:12])
    inherited_parent = repair.parent if repair else continuation.parent if continuation else None
    supplement = extension.plan if extension is not None else None
    if inherited_parent is not None and (inherited_parent / "projection-plan.json").exists():
        supplement = load_inherited_projection(inherited_parent)
    ensure_directory(run)
    control.report("preparing", run)
    with run_lock(run):
        store_object(run / "configuration.json", config.model_dump(mode="json"))
        if repair is not None:
            from .repair import initialize_repair_child

            initialize_repair_child(run, repair, recipe_sha256=decision_generation_recipe_digest())
            batches = repair.source_batches
        elif continuation is not None or extension is not None:
            carried_parent = (
                continuation
                if continuation is not None
                else cast(Continuation, extension.parent if extension else None)
            )
            batches = [
                SourceBatch.model_validate(value)
                for value in cast(dict[str, object], carried_parent.snapshot["sources"]).values()
            ]
            for batch in batches:
                store_object(
                    run / "sources" / (batch.source.id + ".json"), batch.model_dump(mode="json")
                )
        else:
            batches = _source_batches(
                config, run, root / "curation-downloads" / catalog[:16], offline
            )
        control.checkpoint()
        plan = freeze_sources(
            batches, seed=config.seed, configuration_digest=configuration, catalog_digest=catalog
        )
        if repair is not None:
            from .repair import verify_parent_plan

            verify_parent_plan(repair, plan)
        store_object(run / "source-plan.json", plan.model_dump(mode="json"))
        sources = _preserve_family_rights([batch.source for batch in batches], plan)
        base = _publish(
            run,
            "source-corpus",
            [row.record for row in plan.records],
            sources,
            plan.frozenFamilies,
            config.seed,
        )
        artifacts = [_created(base, run / "datasets/source-corpus")]
        seeds, families, rights, exclusions = _seeds_and_families(config, plan, sources)
        if extension is not None:
            if extension.parent.snapshot["native-seeds"] != [
                seed.model_dump(mode="json") for seed in seeds
            ] or extension.parent.snapshot["native-families"] != {
                key: value.model_dump(mode="json") for key, value in sorted(families.items())
            }:
                raise ModelError("INTEGRITY_FAILED", "Projection extension changed existing seeds")
        if supplement is not None:
            if supplement.sourcePlanSha256 != canonical_digest(plan.model_dump(mode="json")):
                raise ModelError("INTEGRITY_FAILED", "Projection source plan changed")
            if supplement.configSha256 != configuration:
                raise ModelError("CONFIG_INVALID", "Projection configuration changed")
            seeds, families, rights = append_projections(seeds, families, rights, supplement)
            store_object(run / "projection-plan.json", supplement.model_dump(mode="json"))
            store_object(run / "projection-report.json", supplement.report.model_dump(mode="json"))
        for seed in seeds:
            _validate_record(seed, seed.parent)
        diversity = _validate_seed_partitions(seeds, families)
        diversity["requestedExamplesPerScenario"] = settings.examplesPerScenario
        diversity["availableExamplesPerScenario"] = AUTHORED_CASES_PER_SCENARIO
        store_object(run / "native-seeds.json", [seed.model_dump(mode="json") for seed in seeds])
        store_object(
            run / "native-families.json",
            {key: value.model_dump(mode="json") for key, value in sorted(families.items())},
        )
        if repair is not None:
            if load_object(repair.parent / "native-seeds.json") != [
                seed.model_dump(mode="json") for seed in seeds
            ] or load_object(repair.parent / "native-families.json") != {
                key: value.model_dump(mode="json") for key, value in sorted(families.items())
            }:
                raise ModelError("INTEGRITY_FAILED", "Repair native seed plan differs from parent")
        seed_manifest = _publish(
            run, "native-seeds", [seed.parent for seed in seeds], rights, families, config.seed
        )
        artifacts.append(_created(seed_manifest, run / "datasets/native-seeds"))
        control.checkpoint()
        jobs = build_decision_jobs(config, seeds, families)
        if extension is not None:
            jobs = extension_jobs(
                extension, build_decision_jobs(config, extension.plan.seeds, families), config
            )
        elif supplement is not None and inherited_parent is not None:
            jobs = restore_job_order(inherited_parent, jobs)
        prior_rejections: dict[str, CandidateOutcome] = {}
        if repair is not None:
            from .repair import repair_jobs, verify_job_plan

            verify_job_plan(repair, jobs)
            jobs, prior_rejections = repair_jobs(repair)
        by_id = {seed.parent.id: seed for seed in seeds}
        if extension is not None:
            for job in extension.parent.jobs:
                _validate_outcome(
                    by_id[job.parentRecordId],
                    job,
                    extension.parent.outcomes[job.jobId],
                    extension.parent.model,
                    recipe=extension.parent.recipe_for(
                        job.jobId, decision_generation_recipe_digest()
                    ),
                )
            initialize_extension(run, extension, recipe=decision_generation_recipe_digest())
        if continuation is not None:
            verify_continuation_inputs(continuation, run)
            jobs = continuation_jobs(continuation, jobs)
            for job in jobs:
                if job.jobId in continuation.outcomes:
                    _validate_outcome(
                        by_id[job.parentRecordId],
                        job,
                        continuation.outcomes[job.jobId],
                        continuation.model,
                        recipe=continuation.recipe_for(
                            job.jobId, decision_generation_recipe_digest()
                        ),
                    )
            initialize_continuation(run, continuation, recipe=decision_generation_recipe_digest())
        store_object(run / "jobs.json", [job.model_dump(mode="json") for job in jobs])
        all_cells = sorted(
            {
                _cell(seed)
                for seed in seeds
                if families[cast(str, seed.parent.familyId)].split == "train"
            }
        )
        planned = Counter(_cell(by_id[job.parentRecordId]) for job in jobs)
        store_object(
            run / "planned-coverage.json",
            {
                "cells": {cell: planned[cell] for cell in all_cells},
                "sourceProjection": exclusions,
                **(
                    {"scopedSourceProjection": supplement.report.model_dump(mode="json")}
                    if supplement is not None
                    else {}
                ),
                "requestedMaximum": config.generation.maxCandidates,
                "plannedCandidates": len(jobs),
                "minimumAcceptedPerCell": settings.minimumAcceptedPerCell,
                "diversity": diversity,
                "maximumModelCalls": sum(
                    (2 if by_id[job.parentRecordId].mode == "rewrite" else 1)
                    * config.generation.maxAttempts
                    for job in jobs
                ),
            },
        )
        warnings = [
            "Native and source corpora are diagnostic; no human-gold calibration is generated.",
            "Source corpus is auxiliary; native-decisions uses the native training contract.",
        ]
        if prepare_only:
            result = CurateResult(
                command="curate",
                status="prepared",
                runPath=str(run),
                configurationSha256=configuration,
                sourceRecords=len(plan.records),
                generatedAccepted=0,
                generatedQuarantined=0,
                datasets=artifacts,
                reportPath=str(run / "prepared-report.json"),
                warnings=warnings,
            )
            store_object(run / "prepared-report.json", result.model_dump(mode="json"))
            control.report("completed", run)
            return result
        control.report("discovering-model", run)
        identity = _select_model(config.endpoint, discover_models(config.endpoint))
        if continuation is not None and identity != continuation.model:
            raise ModelError("CONFIG_INVALID", "Continuation endpoint model differs from parent")
        if extension is not None and identity != extension.parent.model:
            raise ModelError("CONFIG_INVALID", "Projection endpoint model differs from parent")
        control.checkpoint()
        if repair is not None:
            from .repair import verify_parent_model

            verify_parent_model(repair, identity)
        store_object(run / "model-identity.json", identity.model_dump(mode="json"))
        records = {
            seed.parent.id: seed.parent
            for seed in seeds
            if families[cast(str, seed.parent.familyId)].split != "train"
        }
        heldout_count = len(records)
        known = {
            canonical_digest([item.model_dump(mode="json") for item in seed.parent.messages])
            for seed in seeds
        }
        carried_outcomes = (
            repair.outcomes
            if repair
            else continuation.outcomes
            if continuation
            else extension.parent.outcomes
            if extension
            else {}
        )
        carried_contents = (
            {
                canonical_digest(
                    [message.model_dump(mode="json") for message in outcome.record.messages]
                )
                for outcome in carried_outcomes.values()
                if outcome.status == "accepted" and outcome.record is not None
            }
            if carried_outcomes
            else set()
        )
        counts: Counter[str] = Counter()
        accepted: Counter[str] = Counter()
        quarantined: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        dimensions: Counter[str] = Counter()
        accepted_kinds: Counter[str] = Counter()
        lineage_parents: set[str] = set()
        reused = 0
        cached = cached_request_count(run / "requests")
        for job in jobs:
            control.checkpoint()
            seed = by_id[job.parentRecordId]
            path = run / "outcomes" / (job.jobId + ".json")
            if path.exists() or path.is_symlink():
                outcome = CandidateOutcome.model_validate(load_object(path))
                reused += 1
            else:
                control.report(
                    "generating",
                    run,
                    completed=sum(counts.values()),
                    total=len(jobs),
                    accepted=counts["accepted"],
                    quarantined=counts["quarantined"],
                    reused=reused,
                    cached=cached,
                )
                with control.local_request():
                    if repair is None:
                        outcome = generate_decision_candidate(
                            config,
                            identity=identity,
                            seed=seed,
                            job=job,
                            cache_dir=run / "requests",
                        )
                    else:
                        from .repair import prior_response

                        if prior_rejections[job.jobId].reason == "solver-semantic-mismatch":
                            reused += 1
                        outcome = generate_decision_candidate(
                            config,
                            identity=identity,
                            seed=seed,
                            job=job,
                            cache_dir=run / "requests",
                            prior_rejection=prior_rejections[job.jobId],
                            prior_response=prior_response(
                                repair,
                                prior_rejections[job.jobId],
                                phase="rewrite"
                                if prior_rejections[job.jobId].reason.startswith("rewrite-")
                                else "solver",
                            ),
                            prior_cache_dir=repair.parent / "requests",
                            request_namespace=repair.namespace,
                        )
                if outcome.record is not None and seed.mode == "rewrite":
                    content = canonical_digest(
                        [item.model_dump(mode="json") for item in outcome.record.messages]
                    )
                    if content in known or content in carried_contents:
                        from .repair import quarantine_outcome

                        outcome = quarantine_outcome(outcome, "duplicate-conversation")
                _validate_outcome(seed, job, outcome, identity)
                store_object(path, outcome.model_dump(mode="json"))
            carried_record = (
                carried_outcomes[job.jobId].record if job.jobId in carried_outcomes else None
            )
            _validate_outcome(
                seed,
                job,
                outcome,
                identity,
                recipe=carried_record.generation.promptSha256
                if carried_record is not None and carried_record.generation is not None
                else None,
            )
            counts[outcome.status] += 1
            reasons[outcome.reason] += 1
            if outcome.status == "quarantined":
                quarantined[_cell(seed)] += 1
            if outcome.record is not None:
                record = outcome.record
                content = canonical_digest(
                    [item.model_dump(mode="json") for item in record.messages]
                )
                carried = (
                    repair is not None
                    and job.jobId in repair.outcomes
                    and repair.outcomes[job.jobId].status == "accepted"
                )
                if seed.mode == "rewrite" and content in known and not carried:
                    raise ModelError(
                        "INTEGRITY_FAILED", "Cached native outcome duplicates another record"
                    )
                known.add(content)
                if seed.mode == "rewrite":
                    records[seed.parent.id] = seed.parent
                    lineage_parents.add(seed.parent.id)
                    accepted_kinds["generatedDerivative"] += 1
                else:
                    accepted_kinds["verifiedProjection"] += 1
                records[record.id] = record
                accepted[_cell(seed)] += 1
                dimensions["scenario:" + seed.scenario] += 1
                dimensions["language:" + job.language] += 1
                dimensions["split:" + job.split] += 1
                answer = DecisionOutput.model_validate_json(record.messages[-1].content)
                for result_item in answer.results:
                    dimensions["type:" + result_item.type] += 1
                    dimensions["answerability:" + result_item.answerability.status] += 1
                    for issue in result_item.answerability.issues:
                        dimensions["issue:" + issue] += 1
            write_private_json(
                run / "progress.json",
                {
                    "completed": sum(counts.values()),
                    "total": len(jobs),
                    "accepted": counts["accepted"],
                    "quarantined": counts["quarantined"],
                },
            )
            control.report(
                "generating",
                run,
                completed=sum(counts.values()),
                total=len(jobs),
                accepted=counts["accepted"],
                quarantined=counts["quarantined"],
                reused=reused,
                cached=cached,
            )
            control.checkpoint()
        control.report(
            "publishing",
            run,
            completed=sum(counts.values()),
            total=len(jobs),
            accepted=counts["accepted"],
            quarantined=counts["quarantined"],
            reused=reused,
            cached=cached,
        )
        control.checkpoint()
        shortages = {
            cell: settings.minimumAcceptedPerCell - accepted[cell]
            for cell in all_cells
            if accepted[cell] < settings.minimumAcceptedPerCell
        }
        coverage_ok = not shortages and counts["accepted"] > 0
        report = {
            "schemaVersion": 1,
            "configurationSha256": configuration,
            "generationRecipeSha256": decision_generation_recipe_digest(),
            "modelIdentitySha256": identity.metadataSha256,
            "status": "completed" if coverage_ok else "coverage-incomplete",
            "counts": {"accepted": counts["accepted"], "quarantined": counts["quarantined"]},
            "reasons": dict(sorted(reasons.items())),
            "acceptedDimensions": dict(sorted(dimensions.items())),
            "cells": {
                cell: {
                    "planned": planned[cell],
                    "accepted": accepted[cell],
                    "quarantined": quarantined[cell],
                }
                for cell in all_cells
            },
            "shortages": shortages,
            "sourceProjection": exclusions,
            **(
                {
                    "scopedSourceProjection": supplement.report.model_dump(mode="json"),
                    "carriedCounts": {
                        "accepted": sum(o.status == "accepted" for o in carried_outcomes.values()),
                        "quarantined": sum(
                            o.status == "quarantined" for o in carried_outcomes.values()
                        ),
                    },
                }
                if supplement is not None
                else {}
            ),
            "minimumAcceptedPerCell": settings.minimumAcceptedPerCell,
            "plannedCandidates": len(jobs),
            "requestedMaximum": config.generation.maxCandidates,
            "calibrationQualified": False,
            "diversity": diversity,
            "acceptedKinds": {
                key: accepted_kinds[key] for key in ("generatedDerivative", "verifiedProjection")
            },
            "publishedRows": {
                "lineageParents": len(lineage_parents),
                "heldOutDiagnosticSeeds": heldout_count,
                "total": len(records) if counts["accepted"] else 0,
            },
        }
        if counts["accepted"]:
            represented_families = {record.familyId for record in records.values()}
            manifest = _publish(
                run,
                "native-decisions",
                list(records.values()),
                rights + _derived_sources(rights, identity),
                {key: value for key, value in families.items() if key in represented_families},
                config.seed,
            )
            control.checkpoint()
            report["datasetArtifactId"] = manifest.root.artifactId
            artifacts.append(_created(manifest, run / "datasets/native-decisions"))
        store_object(run / "coverage.json", report)
        if not coverage_ok:
            raise ModelError(
                "OUTPUT_INVALID",
                "Native data generation did not meet its accepted coverage gate",
                location=ErrorLocation(kind="artifact-path", value=str(run / "coverage.json")),
            )
        warnings.append(
            "Generator identity is server metadata, not a verified model-weight digest."
        )
        result = CurateResult(
            command="curate",
            status="completed",
            runPath=str(run),
            configurationSha256=configuration,
            sourceRecords=len(plan.records),
            generatedAccepted=counts["accepted"],
            generatedQuarantined=counts["quarantined"],
            datasets=artifacts,
            reportPath=str(run / "completed-report.json"),
            warnings=warnings,
        )
        store_object(run / "completed-report.json", result.model_dump(mode="json"))
        control.report(
            "completed",
            run,
            completed=sum(counts.values()),
            total=len(jobs),
            accepted=counts["accepted"],
            quarantined=counts["quarantined"],
            reused=reused,
            cached=cached,
        )
        return result


def _validate_outcome(
    seed: DecisionSeed,
    job: CandidateJob,
    outcome: CandidateOutcome,
    identity: EndpointModelIdentity,
    *,
    recipe: str | None = None,
) -> None:
    if outcome.jobId != job.jobId:
        raise ModelError("INTEGRITY_FAILED", "Native outcome does not belong to its job")
    record = outcome.record
    if record is None:
        return
    if seed.mode == "annotate":
        if record != seed.parent:
            raise ModelError("INTEGRITY_FAILED", "Verified projection changed its parent record")
        _validate_record(seed, record)
        return
    if (
        record.id != "generated-" + job.jobId
        or record.familyId != job.familyId
        or record.language != job.language
        or record.groupKeys != seed.parent.groupKeys
        or record.sourceId != "generated-" + seed.parent.sourceId
        or record.generation is None
        or record.generation.parentRecordIds != [seed.parent.id]
        or record.generation.modelIdentitySha256 != identity.metadataSha256
        or record.generation.modelId != identity.modelId
        or record.generation.promptSha256 != (recipe or decision_generation_recipe_digest())
    ):
        raise ModelError("INTEGRITY_FAILED", "Native outcome changed its frozen provenance")
    _validate_record(seed, record)

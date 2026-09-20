"""Resumable public-source preparation, local generation and immutable publication."""

from __future__ import annotations

import os
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from typing import cast

from ..artifacts import load_verified_artifact, sha256_file, write_private_json
from ..configuration import load_config
from ..contracts.artifacts import ArtifactManifest
from ..contracts.base import canonical_digest
from ..contracts.cli import CreatedArtifactResult, CurateResult
from ..contracts.inputs import (
    DataRecord,
    DatasetConfig,
    FrozenFamilyAssignment,
    ResolvedDatasetConfig,
    ResolvedSourceDeclaration,
    SourceDeclaration,
)
from ..data import prepare_dataset
from ..errors import ModelError
from ..setup import check_workspace_git_policy
from .contracts import CandidateJob, CandidateOutcome, CurationConfig, CurationPlan, SourceBatch
from .endpoint import EndpointModelIdentity, _select_model, discover_models
from .environment import apply_curation_environment
from .generation import generation_recipe_digest
from .planning import freeze_sources
from .runtime import CurationControl, cached_request_count
from .storage import (
    canonical_bytes,
    ensure_directory,
    load_object,
    run_lock,
    store_object,
    write_once,
)

_PROMPT_VERSION = "curation-v1"


def _source_batches(
    config: CurationConfig, run: Path, cache: Path, offline: bool
) -> list[SourceBatch]:
    from .sources import acquire_source

    batches: list[SourceBatch] = []
    for selected in config.sources:
        path = run / "sources" / (selected.id + ".json")
        if path.exists() or path.is_symlink():
            batch = SourceBatch.model_validate(load_object(path))
            # Also verify/reuse the exact pinned downloads; no corrupted cache repair.
            observed = acquire_source(
                selected.id, cache, offline=offline, max_records=selected.maxRecords
            )
            if observed != batch:
                raise ModelError("INTEGRITY_FAILED", "Source conversion changed on resume")
        else:
            batch = acquire_source(
                selected.id, cache, offline=offline, max_records=selected.maxRecords
            )
            store_object(path, batch.model_dump(mode="json"))
        batches.append(batch)
    return batches


def _preserve_family_rights(
    sources: list[ResolvedSourceDeclaration],
    plan: CurationPlan,
) -> list[ResolvedSourceDeclaration]:
    """Carry restrictive duplicate-source terms into retained source declarations."""
    catalog = {source.id: source for source in sources}
    related = {source.id: {source.id} for source in sources}
    for row in plan.records:
        family = plan.frozenFamilies[cast(str, row.record.familyId)]
        related[row.record.sourceId].update(
            value.rsplit(":", 1)[0] for value in family.sourceSplits
        )
    result: list[ResolvedSourceDeclaration] = []
    for source in sources:
        contributors = [catalog[key] for key in sorted(related[source.id])]
        if len(contributors) == 1:
            result.append(source)
            continue
        data = source.model_dump(mode="json")
        for field in ("license", "licenseEvidence", "attribution"):
            data[field] = "; ".join(
                item.id + ": " + str(getattr(item, field)) for item in contributors
            )
        data["sharedTrainingAllowed"] = all(item.sharedTrainingAllowed for item in contributors)
        data["redistributionAllowed"] = all(item.redistributionAllowed for item in contributors)
        assessments = {item.commercialUse for item in contributors}
        data["commercialUse"] = (
            "restricted"
            if "restricted" in assessments
            else "unknown"
            if "unknown" in assessments
            else "allowed"
        )
        data["restrictions"] = sorted(
            {restriction for item in contributors for restriction in item.restrictions}
            | {
                "Connected source-family provenance: "
                + canonical_bytes([item.model_dump(mode="json") for item in contributors])
                .decode()
                .strip()
            }
        )
        private = [item for item in contributors if item.privacy == "private"]
        if private:
            data["privacy"] = "private"
            data["authorizationRef"] = "; ".join(
                item.id + ": " + cast(str, item.authorizationRef) for item in private
            )
        result.append(ResolvedSourceDeclaration.model_validate(data))
    return result


def _publish(
    run: Path,
    name: str,
    records: list[DataRecord],
    sources: list[ResolvedSourceDeclaration],
    families: dict[str, FrozenFamilyAssignment],
    seed: int,
) -> ArtifactManifest:
    inputs = run / "publication-inputs" / name
    ensure_directory(inputs / "records")
    used = {record.sourceId for record in records}
    declarations: list[SourceDeclaration] = []
    for source in sorted(sources, key=lambda item: item.id):
        if source.id not in used:
            continue
        selected = sorted(
            (record for record in records if record.sourceId == source.id),
            key=lambda record: record.id,
        )
        write_once(
            inputs / "records" / (source.id + ".jsonl"),
            b"".join(canonical_bytes(record.model_dump(mode="json")) for record in selected),
        )
        declarations.append(
            SourceDeclaration.model_validate(
                {
                    **source.model_dump(mode="json"),
                    "path": "records/" + source.id + ".jsonl",
                }
            )
        )
    if {item.id for item in declarations} != used:
        raise ModelError("DATA_RIGHTS_DENIED", "Published records require declared source rights")
    config = DatasetConfig(
        schemaVersion=1,
        name=name,
        sources=declarations,
        seed=seed,
        frozenFamilies=families,
        maxRecords=max(4, len(records)),
    )
    recipe = inputs / "dataset.json"
    write_once(recipe, canonical_bytes(config.model_dump(mode="json")))
    destination = run / "datasets" / name
    ensure_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        manifest = load_verified_artifact(destination)
        if manifest.root.kind != "dataset":
            raise ModelError("INTEGRITY_FAILED", "Published curation output is not a dataset")
        expected = ResolvedDatasetConfig.model_validate(
            {
                **config.model_dump(mode="json", exclude={"sources"}),
                "sources": [
                    {
                        **source.model_dump(mode="json", exclude={"path"}),
                        "restrictions": sorted(source.restrictions),
                    }
                    for source in declarations
                ],
            }
        )
        if manifest.root.details.datasetConfig != expected:
            raise ModelError("INTEGRITY_FAILED", "Published dataset recipe changed")
        for summary in manifest.root.details.sourceFiles:
            if sha256_file(inputs / "records" / (summary.sourceId + ".jsonl")) != (
                summary.size,
                summary.sha256,
            ):
                raise ModelError("INTEGRITY_FAILED", "Published dataset source bytes changed")
        return manifest
    return prepare_dataset(recipe, destination)


def _created(manifest: ArtifactManifest, path: Path) -> CreatedArtifactResult:
    return CreatedArtifactResult(
        outputPath=str(path),
        artifactId=manifest.root.artifactId,
        kind=manifest.root.kind,
        stage=manifest.root.stage,
        customer=None,
    )


def _scenario_source() -> ResolvedSourceDeclaration:
    return ResolvedSourceDeclaration(
        id="foliqant-scenarios",
        license="Project-authored research scenarios",
        licenseEvidence="Foliqant versioned scenario generator; not third-party documents",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="unknown",
        attribution="Foliqant scenario generator",
        restrictions=["No distribution license or commercial clearance is asserted."],
    )


def _derived_sources(
    sources: list[ResolvedSourceDeclaration],
    identity: EndpointModelIdentity,
) -> list[ResolvedSourceDeclaration]:
    result: list[ResolvedSourceDeclaration] = []
    for source in sources:
        result.append(
            ResolvedSourceDeclaration.model_validate(
                {
                    **source.model_dump(mode="json"),
                    "id": "generated-" + source.id,
                    "license": source.license + "; local generator output terms unresolved",
                    "licenseEvidence": source.licenseEvidence
                    + "; generator metadata "
                    + identity.metadataSha256,
                    "commercialUse": "restricted"
                    if source.commercialUse == "restricted"
                    else "unknown",
                    "redistributionAllowed": False,
                    "restrictions": sorted(
                        set(
                            source.restrictions
                            + [
                                "Check output-use terms of local model "
                                + identity.modelId
                                + " before redistribution or commercial use.",
                            ]
                        )
                    ),
                }
            )
        )
    return result


def _jobs(
    config: CurationConfig, source: CurationPlan, scenarios: CurationPlan
) -> list[CandidateJob]:
    jobs: list[CandidateJob] = []
    training = [
        row
        for row in source.records
        if source.frozenFamilies[cast(str, row.record.familyId)].split == "train"
        and not any(tag.startswith("augmentation-ineligible:") for tag in row.record.tags)
    ]
    training.sort(key=lambda row: canonical_digest({"seed": config.seed, "id": row.record.id}))
    selections = [
        entry
        for pair in zip_longest(
            [(row, "synthetic-regression", scenarios) for row in scenarios.records],
            [(row, "training-augmentation", source) for row in training],
        )
        for entry in pair
        if entry is not None
    ]
    for index, (row, purpose, plan) in enumerate(selections):
        family = cast(str, row.record.familyId)
        if purpose == "synthetic-regression":
            operation = next(tag[9:] for tag in row.record.tags if tag.startswith("scenario:"))
        else:
            operation = ("paraphrase", "irrelevant-context", "source-question")[index % 3]
        for language in config.generation.languages:
            payload = {
                "parentRecordId": row.record.id,
                "familyId": family,
                "split": plan.frozenFamilies[family].split,
                "language": language,
                "purpose": purpose,
                "operation": operation,
            }
            jobs.append(
                CandidateJob.model_validate(
                    {
                        **payload,
                        "jobId": canonical_digest(
                            {
                                "job": payload,
                                "configuration": source.configurationSha256,
                                "prompts": generation_recipe_digest(),
                            }
                        ),
                    }
                )
            )
            if len(jobs) >= config.generation.maxCandidates:
                return jobs
    return jobs


def run_curation(
    config_path: Path,
    workspace: Path | None = None,
    *,
    prepare_only: bool = False,
    offline: bool = False,
    repair_from: Path | None = None,
    continue_from: Path | None = None,
    control: CurationControl | None = None,
) -> CurateResult:
    """Prepare or resume one complete unattended local curation run."""
    selected_control = control or CurationControl()
    with selected_control.active():
        return _run_curation(
            config_path,
            workspace,
            prepare_only=prepare_only,
            offline=offline,
            repair_from=repair_from,
            continue_from=continue_from,
            control=selected_control,
        )


def _run_curation(
    config_path: Path,
    workspace: Path | None,
    *,
    prepare_only: bool,
    offline: bool,
    repair_from: Path | None,
    continue_from: Path | None,
    control: CurationControl,
) -> CurateResult:
    from .sources import source_catalog_digest

    config = apply_curation_environment(load_config(config_path, CurationConfig), os.environ)
    if config.decisionData is not None:
        from .decision_runner import run_decision_curation

        return run_decision_curation(
            config,
            workspace,
            prepare_only=prepare_only,
            offline=offline,
            repair_from=repair_from,
            continue_from=continue_from,
            control=control,
        )
    if continue_from is not None:
        raise ModelError("ARGUMENT_INVALID", "Continuation requires native decision curation")
    configuration_digest = canonical_digest(config.model_dump(mode="json"))
    catalog_digest = source_catalog_digest()
    identity_digest = canonical_digest(
        {
            "config": configuration_digest,
            "catalog": catalog_digest,
            "implementation": _PROMPT_VERSION,
            "generationRecipe": generation_recipe_digest(),
        }
    )
    root = (workspace or Path.home() / ".local/share/foliqant").expanduser().absolute()
    check_workspace_git_policy(root)
    if repair_from is not None and prepare_only:
        raise ModelError("ARGUMENT_INVALID", "A repair pass cannot be prepare-only")
    repair = None
    if repair_from is not None:
        from .repair import load_repair_pass

        repair = load_repair_pass(
            root,
            repair_from,
            config,
            recipe_sha256=generation_recipe_digest(),
            native=False,
        )
    run = (
        root
        / "curation"
        / (
            config.name
            + ("-repair-" + repair.run_id[:12] if repair else "-" + identity_digest[:12])
        )
    )
    cache = root / "curation-downloads" / catalog_digest[:16]
    ensure_directory(run)
    control.report("preparing", run)
    with run_lock(run):
        store_object(run / "configuration.json", config.model_dump(mode="json"))
        if repair is not None:
            from .repair import initialize_repair_child

            initialize_repair_child(run, repair, recipe_sha256=generation_recipe_digest())
            batches = repair.source_batches
        else:
            batches = _source_batches(config, run, cache, offline)
        control.checkpoint()
        plan = freeze_sources(
            batches,
            seed=config.seed,
            configuration_digest=configuration_digest,
            catalog_digest=catalog_digest,
        )
        store_object(run / "source-plan.json", plan.model_dump(mode="json"))
        # The persisted plan must be exactly equal even after an interrupted generation run.
        plan = CurationPlan.model_validate(load_object(run / "source-plan.json"))
        if repair is not None:
            from .repair import verify_parent_plan

            verify_parent_plan(repair, plan)
        sources = _preserve_family_rights([batch.source for batch in batches], plan)
        base_records = [row.record for row in plan.records]
        base = _publish(
            run, "source-corpus", base_records, sources, plan.frozenFamilies, config.seed
        )
        artifacts = [_created(base, run / "datasets/source-corpus")]
        control.checkpoint()
        if prepare_only:
            result = CurateResult(
                command="curate",
                status="prepared",
                runPath=str(run),
                configurationSha256=configuration_digest,
                sourceRecords=len(base_records),
                generatedAccepted=0,
                generatedQuarantined=0,
                datasets=artifacts,
                reportPath=str(run / "prepared-report.json"),
                warnings=[
                    "Research corpus only; no local model generation or human review claimed."
                ],
            )
            store_object(run / "prepared-report.json", result.model_dump(mode="json"))
            control.report("completed", run)
            return result

        from .generation import generate_candidate
        from .scenarios import scenario_records

        control.report("discovering-model", run)
        identity = _select_model(config.endpoint, discover_models(config.endpoint))
        control.checkpoint()
        if repair is not None:
            from .repair import verify_parent_model

            verify_parent_model(repair, identity)
        store_object(run / "model-identity.json", identity.model_dump(mode="json"))
        scenario_batch = SourceBatch(
            source=_scenario_source(),
            revision=_PROMPT_VERSION,
            assets=[],
            records=scenario_records(config),
            totalAvailable=config.generation.scenarioFamilies,
        )
        scenario_plan = freeze_sources(
            [scenario_batch],
            seed=config.seed,
            configuration_digest=configuration_digest,
            catalog_digest=catalog_digest,
        )
        if repair is not None and scenario_plan.model_dump(mode="json") != load_object(
            repair.parent / "scenario-plan.json"
        ):
            raise ModelError("INTEGRITY_FAILED", "Repair scenario plan differs from its parent")
        store_object(run / "scenario-plan.json", scenario_plan.model_dump(mode="json"))
        jobs = _jobs(config, plan, scenario_plan)
        prior_rejections: dict[str, CandidateOutcome] = {}
        if repair is not None:
            from .repair import repair_jobs, verify_job_plan

            verify_job_plan(repair, jobs)
            jobs, prior_rejections = repair_jobs(repair)
        store_object(run / "jobs.json", [job.model_dump(mode="json") for job in jobs])
        parents = {row.record.id: row.record for row in plan.records + scenario_plan.records}
        augmented = list(base_records)
        regression = [row.record for row in scenario_plan.records]
        known = {
            canonical_digest([message.model_dump(mode="json") for message in record.messages])
            for record in augmented + regression
        }
        if repair is not None:
            known.update(
                canonical_digest(
                    [message.model_dump(mode="json") for message in outcome.record.messages]
                )
                for outcome in repair.outcomes.values()
                if outcome.status == "accepted" and outcome.record is not None
            )
        counts: Counter[str] = Counter()
        reused = 0
        cached = cached_request_count(run / "requests")
        coverage: Counter[str] = Counter()
        for row in plan.records:
            if plan.frozenFamilies[cast(str, row.record.familyId)].split == "train":
                for tag in row.record.tags:
                    if tag.startswith("augmentation-ineligible:"):
                        coverage[tag + ":excluded"] += 1
        for job in jobs:
            control.checkpoint()
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
                        outcome = generate_candidate(
                            config,
                            identity=identity,
                            parent=parents[job.parentRecordId],
                            job=job,
                            cache_dir=run / "requests",
                        )
                    else:
                        from .repair import prior_response

                        outcome = generate_candidate(
                            config,
                            identity=identity,
                            parent=parents[job.parentRecordId],
                            job=job,
                            cache_dir=run / "requests",
                            prior_rejection=prior_rejections[job.jobId],
                            prior_response=prior_response(
                                repair, prior_rejections[job.jobId], phase="generator"
                            ),
                            request_namespace=repair.namespace,
                        )
                if outcome.record is not None:
                    content = canonical_digest(
                        [message.model_dump(mode="json") for message in outcome.record.messages]
                    )
                    if content in known:
                        from .repair import quarantine_outcome

                        outcome = quarantine_outcome(outcome, "duplicate-conversation")
                store_object(path, outcome.model_dump(mode="json"))
            if outcome.jobId != job.jobId:
                raise ModelError("INTEGRITY_FAILED", "Candidate outcome does not belong to its job")
            counts[outcome.status] += 1
            for dimension, value in (
                ("purpose", job.purpose),
                ("language", job.language),
                ("operation", job.operation),
                ("source", parents[job.parentRecordId].sourceId),
                ("reason", outcome.reason),
            ):
                coverage[dimension + ":" + value + ":" + outcome.status] += 1
            if outcome.record is not None:
                record = outcome.record
                if (
                    record.familyId != job.familyId
                    or record.language != job.language
                    or record.sourceId != "generated-" + parents[job.parentRecordId].sourceId
                    or record.generation is None
                    or record.generation.parentRecordIds != [job.parentRecordId]
                    or record.generation.modelIdentitySha256 != identity.metadataSha256
                ):
                    raise ModelError(
                        "INTEGRITY_FAILED", "Generated candidate changed its frozen assignment"
                    )
                known.add(
                    canonical_digest([item.model_dump(mode="json") for item in record.messages])
                )
                (augmented if job.purpose == "training-augmentation" else regression).append(record)
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
        store_object(
            run / "coverage.json",
            {
                "counts": dict(sorted(coverage.items())),
                "sourceExclusions": plan.excludedCounts,
                "plannedCandidates": len(jobs),
                "requestedMaximum": config.generation.maxCandidates,
            },
        )
        augmented_manifest = _publish(
            run,
            "augmented-corpus",
            augmented,
            sources + _derived_sources(sources, identity),
            plan.frozenFamilies,
            config.seed,
        )
        scenario_source = _scenario_source()
        regression_manifest = _publish(
            run,
            "synthetic-regression",
            regression,
            [scenario_source] + _derived_sources([scenario_source], identity),
            scenario_plan.frozenFamilies,
            config.seed,
        )
        artifacts += [
            _created(augmented_manifest, run / "datasets/augmented-corpus"),
            _created(regression_manifest, run / "datasets/synthetic-regression"),
        ]
        control.checkpoint()
        warnings = [
            "Automatic checks are not human review or financial quality certification.",
            "Generator identity is server metadata; no weight fingerprint was verified.",
        ]
        if counts["quarantined"]:
            warnings.append(
                "Quarantined candidates are excluded; no manual record editing is required."
            )
        if not counts["accepted"]:
            warnings.append(
                "No generated candidate passed; published corpus contains source/seed records only."
            )
        result = CurateResult(
            command="curate",
            status="completed",
            runPath=str(run),
            configurationSha256=configuration_digest,
            sourceRecords=len(base_records),
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

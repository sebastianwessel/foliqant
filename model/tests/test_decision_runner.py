"""Native curation must balance coverage without exposing held-out families."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from foliqant_model.contracts.inputs import FrozenFamilyAssignment
from foliqant_model.curation.contracts import CurationConfig
from foliqant_model.curation.decision_contracts import DecisionDataSettings
from foliqant_model.curation.decision_runner import build_decision_jobs
from foliqant_model.curation.decision_seeds import build_authored_seeds


def _install_test_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Supply a tiny source and a deterministic endpoint double for orchestration tests."""
    from foliqant_model.contracts.base import canonical_digest
    from foliqant_model.contracts.inputs import (
        ChatMessage,
        DataRecord,
        GenerationProvenance,
        ResolvedSourceDeclaration,
    )
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateOutcome, ImportedRecord, SourceBatch
    from foliqant_model.curation.endpoint import EndpointModelIdentity

    rights = ResolvedSourceDeclaration(
        id="banking77",
        license="test",
        licenseEvidence="test-only",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="restricted",
        attribution="test",
        restrictions=["Inherited test source restriction"],
    )
    rows = [
        ImportedRecord(
            record=DataRecord(
                schemaVersion=1,
                id="source-" + split,
                sourceId="banking77",
                language="en",
                groupKeys=["group-" + split],
                origin="human",
                reviewed=False,
                messages=[
                    ChatMessage(role="user", content="Unique input " + split),
                    ChatMessage(role="assistant", content="Unique answer " + split),
                ],
            ),
            originalSplit=split,
            task="classification",
            originalId=split,
        )
        for split in ("train", "validation", "calibration", "test")
    ]
    batch = SourceBatch(
        source=rights, revision="fixture", assets=[], records=rows, totalAvailable=4
    )

    def source_batches(_config, run, _cache, _offline):  # type: ignore[no-untyped-def]
        from foliqant_model.curation.storage import store_object

        store_object(run / "sources/banking77.json", batch.model_dump(mode="json"))
        return [batch]

    monkeypatch.setattr(runner, "_source_batches", source_batches)
    identity = EndpointModelIdentity(
        modelId="local-test",
        modelType="llm",
        publisher=None,
        architecture=None,
        format=None,
        quantization=None,
        sizeBytes=None,
        maxContextLength=None,
        metadataSha256="a" * 64,
        immutableRevision=None,
        structuredOutput="unknown",
    )
    monkeypatch.setattr(runner, "discover_models", lambda config: [identity])
    calls: list[str] = []

    def generate(config, *, identity, seed, job, cache_dir):  # type: ignore[no-untyped-def]
        assert job.split == "train"
        calls.append(job.parentRecordId)
        task = seed.input.model_copy(deep=True)
        for source in task.state.sources:
            source.text = "Case details: " + source.text
        output = runner.canonical_decision_output(seed, task)
        generation = GenerationProvenance(
            provider="openai-compatible",
            modelId=identity.modelId,
            modelIdentitySha256=identity.metadataSha256,
            promptSha256=runner.decision_generation_recipe_digest(),
            parametersSha256=canonical_digest({"job": job.jobId}),
            requestSha256="b" * 64,
            parentRecordIds=[seed.parent.id],
        )
        record = DataRecord.model_validate(
            {
                **seed.parent.model_dump(mode="json"),
                "id": "generated-" + job.jobId,
                "sourceId": "generated-" + seed.parent.sourceId,
                "origin": "teacher",
                "generation": generation.model_dump(mode="json"),
                "messages": [
                    *[message.model_dump(mode="json") for message in seed.parent.messages[:-2]],
                    {"role": "user", "content": task.model_dump_json()},
                    {"role": "assistant", "content": output.model_dump_json()},
                ],
            }
        )
        return CandidateOutcome(
            jobId=job.jobId,
            status="accepted",
            reason="automated-checks-passed",
            attempts=1,
            requestSha256="c" * 64,
            responseSha256="d" * 64,
            record=record,
        )

    monkeypatch.setattr(runner, "generate_decision_candidate", generate)
    return calls


def test_native_publication_and_resume_are_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from foliqant_model.artifacts import load_verified_artifact
    from foliqant_model.curation.decision_runner import run_decision_curation
    from foliqant_model.curation.storage import load_object

    calls = _install_test_pipeline(monkeypatch)
    config = CurationConfig.model_validate(
        {
            "sources": [{"id": "banking77", "maxRecords": 10}],
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 4, "maxAttempts": 1},
        }
    )
    prepared = run_decision_curation(config, tmp_path, prepare_only=True)
    assert prepared.status == "prepared" and calls == []
    first = run_decision_curation(config, tmp_path)
    assert first.generatedAccepted == 4
    assert first.generatedQuarantined == 0
    assert len(calls) == 4
    assert run_decision_curation(config, tmp_path) == first
    assert len(calls) == 4
    manifest = load_verified_artifact(Path(first.runPath) / "datasets/native-decisions")
    assert manifest.root.kind == "dataset"
    assert manifest.root.details.diagnostic
    report = load_object(Path(first.runPath) / "coverage.json")
    assert report["calibrationQualified"] is False


def test_prepare_publishes_projected_source_with_sorted_combined_families(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from foliqant_model.artifacts import load_verified_artifact
    from foliqant_model.contracts.inputs import ChatMessage, DataRecord, ResolvedSourceDeclaration
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import ImportedRecord, SourceBatch
    from foliqant_model.curation.decision_runner import run_decision_curation
    from foliqant_model.curation.storage import load_object

    rights = ResolvedSourceDeclaration(
        id="banking77",
        license="test banking license",
        licenseEvidence="test-only evidence",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="restricted",
        attribution="test banking source",
        restrictions=["Inherited banking test restriction"],
    )
    labels = ["cash_withdrawal", "card_payment"]
    rows: list[ImportedRecord] = []
    source_splits = ("train", "train", "validation", "calibration", "test")
    for index, split in enumerate(source_splits):
        record_id = f"banking-{split}-{index}"
        record = DataRecord(
            schemaVersion=1,
            id=record_id,
            sourceId="banking77",
            language="en",
            groupKeys=[f"banking-group-{split}-{index}"],
            messages=[
                ChatMessage(role="system", content="Choose one supplied banking label."),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "labels": labels,
                            "request": f"Cash withdrawal request example {index}",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
                ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {"intent": "cash_withdrawal"},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ],
            tags=["banking77"],
            origin="human",
            reviewed=False,
        )
        rows.append(
            ImportedRecord(
                record=record,
                originalSplit=split,  # type: ignore[arg-type]
                task="classification",
                originalId=record_id,
            )
        )
    batch = SourceBatch(
        source=rights,
        revision="projection-fixture",
        assets=[],
        records=rows,
        totalAvailable=len(rows),
    )
    monkeypatch.setattr(runner, "_source_batches", lambda *args: [batch])
    config = CurationConfig.model_validate(
        {
            "name": "projected-source-publication-test",
            "sources": [{"id": "banking77", "maxRecords": 5}],
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 5,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 1, "maxAttempts": 1},
        }
    )

    result = run_decision_curation(config, tmp_path, prepare_only=True)

    seeds = load_object(Path(result.runPath) / "native-seeds.json")
    assert isinstance(seeds, list)
    projected = [item for item in seeds if item["parent"]["sourceId"] == "native-banking77"]
    assert len(projected) == 5
    assert all(item["parent"]["origin"] == "synthetic" for item in projected)
    assert all(
        any(tag.startswith("original-source-record:") for tag in item["parent"]["tags"])
        for item in projected
    )
    manifest = load_verified_artifact(Path(result.runPath) / "datasets/native-seeds")
    assert manifest.root.kind == "dataset"
    rights_by_source = {item.sourceId: item for item in manifest.root.sourceRights}
    assert "native-banking77" in rights_by_source
    assert "Inherited banking test restriction" in rights_by_source["native-banking77"].restrictions


def test_coverage_shortage_is_failure_with_preserved_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from foliqant_model.curation.decision_runner import run_decision_curation
    from foliqant_model.curation.storage import load_object
    from foliqant_model.errors import ModelError

    calls = _install_test_pipeline(monkeypatch)
    config = CurationConfig.model_validate(
        {
            "sources": [{"id": "banking77", "maxRecords": 10}],
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 1,
            },
            "generation": {"maxCandidates": 1, "maxAttempts": 1},
        }
    )
    with pytest.raises(ModelError, match="accepted coverage gate") as failure:
        run_decision_curation(config, tmp_path)
    assert failure.value.exit_code == 4
    assert failure.value.location is not None
    report_path = Path(failure.value.location.value)
    report = load_object(report_path)
    assert report["status"] == "coverage-incomplete"
    assert report["shortages"]
    assert (report_path.parent / "datasets/native-decisions/manifest.json").exists()
    with pytest.raises(ModelError, match="accepted coverage gate"):
        run_decision_curation(config, tmp_path)
    assert len(calls) == 1


def test_job_plan_balances_cells_and_never_exposes_heldouts() -> None:
    settings = DecisionDataSettings(examplesPerScenario=4, sourceExamplesPerSource=0)
    seeds = build_authored_seeds(settings, seed=42, languages=["en"])
    families = {
        seed.parent.familyId: FrozenFamilyAssignment(
            split="test" if index == 0 else "train",
            sourceSplits=["foliqant-decisions:unspecified"],
        )
        for index, seed in enumerate(seeds)
    }
    config = CurationConfig.model_validate(
        {"decisionData": settings.model_dump(), "generation": {"maxCandidates": 12}}
    )
    jobs = build_decision_jobs(config, seeds, families)
    parents = {seed.parent.id: seed for seed in seeds}
    cells = Counter((parents[job.parentRecordId].scenario, job.language) for job in jobs)
    assert len(jobs) == 12
    assert len({job.jobId for job in jobs}) == 12
    assert max(cells.values()) == 1
    assert all(job.split == "train" and job.purpose == "decision-training" for job in jobs)
    assert all(families[job.familyId].split == "train" for job in jobs)
    assert jobs == build_decision_jobs(config, list(reversed(seeds)), families)


def test_native_recipe_modes_are_explicit() -> None:
    from foliqant_model.configuration import load_config

    root = Path(__file__).resolve().parents[2]
    full = load_config(root / "model/examples/native-full.yaml", CurationConfig)
    pilot = load_config(root / "model/examples/native-pilot.yaml", CurationConfig)
    assert full.decisionData is not None and pilot.decisionData is not None
    assert full.name != pilot.name
    assert full.generation.maxCandidates > pilot.generation.maxCandidates
    assert full.decisionData.minimumAcceptedPerCell > 0
    assert pilot.decisionData.minimumAcceptedPerCell == 0
    for recipe in (full, pilot):
        assert recipe.generation.languages == ["en", "de"]
        assert recipe.endpoint.baseUrl == "http://127.0.0.1:8000/v1"
        assert recipe.endpoint.model == "incoai/Qwen3.8-27B-Splash"
        assert recipe.endpoint.reasoningEffort == "low"
        assert recipe.endpoint.temperature == 0.1
        assert recipe.endpoint.maxTokens == 8192
        assert recipe.endpoint.timeoutSeconds == 300
    assert pilot.generation.maxCandidates == 16


def test_bilingual_pilot_covers_both_languages_without_splitting_translations() -> None:
    from foliqant_model.configuration import load_config

    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "model/examples/native-pilot.yaml", CurationConfig)
    assert config.decisionData is not None
    seeds = build_authored_seeds(config.decisionData, seed=config.seed, languages=["en", "de"])
    families = {
        seed.parent.familyId: FrozenFamilyAssignment(
            split="train", sourceSplits=["foliqant-decisions:unspecified"]
        )
        for seed in seeds
    }
    parents = {seed.parent.id: seed for seed in seeds}
    jobs = build_decision_jobs(config, seeds, families)
    assert Counter(job.language for job in jobs) == {"en": 8, "de": 8}
    for language in ("en", "de"):
        selected = [parents[job.parentRecordId] for job in jobs if job.language == language]
        assert {question.type for seed in selected for question in seed.input.questions} == {
            "choice",
            "multiselect",
            "predicate",
            "ordinal",
            "request_units",
        }
        assert {"requests-partial", "conditional-unresolved", "adequacy-omitted-request"} <= {
            seed.scenario for seed in selected
        }
    for scenario in {seed.scenario for seed in seeds}:
        siblings = [seed for seed in seeds if seed.scenario == scenario]
        assert {seed.parent.familyId for seed in siblings if seed.parent.language == "en"} == {
            seed.parent.familyId for seed in siblings if seed.parent.language == "de"
        }


def test_eight_job_english_pilot_covers_answer_types_and_failure_modes() -> None:
    from foliqant_model.curation.decision_contracts import DecisionInput

    seeds = build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])
    families = {
        seed.parent.familyId: FrozenFamilyAssignment(
            split="train", sourceSplits=["foliqant-decisions:unspecified"]
        )
        for seed in seeds
    }
    config = CurationConfig.model_validate({"generation": {"maxCandidates": 8}})
    parents = {seed.parent.id: seed for seed in seeds}
    chosen = [parents[job.parentRecordId] for job in build_decision_jobs(config, seeds, families)]
    types = {
        question.type
        for seed in chosen
        for question in DecisionInput.model_validate_json(seed.parent.messages[1].content).questions
    }
    assert types == {"choice", "multiselect", "predicate", "ordinal", "request_units"}
    assert {"requests-partial", "conditional-unresolved", "adequacy-omitted-request"} <= {
        seed.scenario for seed in chosen
    }


def test_native_data_is_opt_in_for_existing_recipes() -> None:
    assert CurationConfig().decisionData is None


def test_generation_wrapper_help_does_not_require_uv_or_env(tmp_path: Path) -> None:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts/generate-data"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 0
    assert "--pilot" in result.stdout
    assert "does not train" in result.stdout.lower()


@pytest.mark.parametrize("flag", ["--pilot=maybe", "--profile", "--pilot", "--config"])
def test_invalid_wrapper_combinations_are_rejected(flag: str, tmp_path: Path) -> None:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    args = [flag, "--config", "other.yaml"] if flag == "--pilot" else [flag]
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts/generate-data"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


@pytest.mark.parametrize("mode", ["rewrite", "annotate"])
def test_rejected_and_unattempted_train_parents_do_not_enter_final_corpus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateOutcome
    from foliqant_model.curation.storage import load_object

    _install_test_pipeline(monkeypatch)
    original_generate = runner.generate_decision_candidate
    original_plan = runner._seeds_and_families

    def selected_plan(*args):  # type: ignore[no-untyped-def]
        seeds, families, rights, counts = original_plan(*args)
        return (
            [seed.model_copy(update={"mode": mode}) for seed in seeds],
            families,
            rights,
            counts,
        )

    monkeypatch.setattr(runner, "_seeds_and_families", selected_plan)
    outcomes: dict[str, str] = {}

    def selective_generate(config, *, identity, seed, job, cache_dir):  # type: ignore[no-untyped-def]
        if outcomes:
            outcomes[job.parentRecordId] = "quarantined"
            return CandidateOutcome(
                jobId=job.jobId,
                status="quarantined",
                reason="solver-semantic-mismatch",
                attempts=1,
                requestSha256="a" * 64,
                responseSha256="b" * 64,
                record=None,
            )
        outcomes[job.parentRecordId] = "accepted"
        outcome = original_generate(
            config, identity=identity, seed=seed, job=job, cache_dir=cache_dir
        )
        return outcome.model_copy(update={"record": seed.parent}) if mode == "annotate" else outcome

    monkeypatch.setattr(runner, "generate_decision_candidate", selective_generate)
    config = CurationConfig.model_validate(
        {
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 3, "maxAttempts": 1},
        }
    )
    result = runner.run_decision_curation(config, tmp_path)
    import json

    run = Path(result.runPath)
    records = [
        json.loads(line)
        for line in (run / "datasets/native-decisions/records/train.jsonl").read_text().splitlines()
    ]
    parents = {row["id"] for row in records if "generation" not in row}
    assert parents == {key for key, value in outcomes.items() if value == "accepted"}
    assert len(records) == (2 if mode == "rewrite" else 1)
    all_seeds = load_object(run / "native-seeds.json")
    assert len(all_seeds) > len(records)
    families = load_object(run / "native-families.json")
    for split in ("validation", "calibration", "test"):
        actual = {
            row["id"]: row
            for row in (
                json.loads(line)
                for line in (run / f"datasets/native-decisions/records/{split}.jsonl")
                .read_text()
                .splitlines()
            )
        }
        expected = {
            entry["parent"]["id"]: entry["parent"]
            for entry in all_seeds
            if families[entry["parent"]["familyId"]]["split"] == split
        }
        assert actual == expected
    assert {key for key, value in outcomes.items() if value == "quarantined"}.isdisjoint(parents)
    assert runner.run_decision_curation(config, tmp_path) == result


def test_no_accepted_candidates_publish_no_training_corpus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateOutcome
    from foliqant_model.errors import ModelError

    _install_test_pipeline(monkeypatch)

    def reject(config, *, identity, seed, job, cache_dir):  # type: ignore[no-untyped-def]
        return CandidateOutcome(
            jobId=job.jobId,
            status="quarantined",
            reason="solver-semantic-mismatch",
            attempts=1,
            requestSha256="a" * 64,
            responseSha256="b" * 64,
            record=None,
        )

    monkeypatch.setattr(runner, "generate_decision_candidate", reject)
    config = CurationConfig.model_validate(
        {
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 2, "maxAttempts": 1},
        }
    )
    with pytest.raises(ModelError, match="accepted coverage gate") as failure:
        runner.run_decision_curation(config, tmp_path)
    assert failure.value.location is not None
    run = Path(failure.value.location.value).parent
    assert (run / "datasets/native-seeds/manifest.json").exists()
    assert not (run / "datasets/native-decisions").exists()


def test_equivalent_native_task_cannot_cross_frozen_families() -> None:
    from foliqant_model.curation.decision_runner import _validate_seed_partitions
    from foliqant_model.errors import ModelError

    seed = build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])[0]
    other = seed.model_copy(
        update={
            "parent": seed.parent.model_copy(
                update={"id": "different-id", "familyId": "other-family", "groupKeys": ["other"]}
            )
        }
    )
    families = {
        seed.parent.familyId: FrozenFamilyAssignment(split="train", sourceSplits=["test:train"]),
        "other-family": FrozenFamilyAssignment(split="test", sourceSplits=["test:test"]),
    }
    with pytest.raises(ModelError, match="Equivalent native inputs cross families"):
        _validate_seed_partitions([seed, other], families)


def test_answer_bearing_identity_keeps_every_allowed_source() -> None:
    from foliqant_model.contracts.inputs import ChatMessage
    from foliqant_model.curation.decision_contracts import DecisionSource
    from foliqant_model.curation.decision_runner import _task_identities

    seed = build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])[0]

    def with_task(task):  # type: ignore[no-untyped-def]
        parent = seed.parent.model_copy(
            update={
                "messages": [
                    *seed.parent.messages[:-2],
                    ChatMessage(role="user", content=task.model_dump_json()),
                    seed.parent.messages[-1],
                ]
            }
        )
        return seed.model_copy(update={"parent": parent})

    unreachable = seed.input.model_copy(deep=True)
    unreachable.state.sources.append(
        DecisionSource(id="unavailable", kind="metadata", text="Queue position 7.")
    )
    allowed = unreachable.model_copy(deep=True)
    allowed.questions[0].allowedSourceIds.append("unavailable")

    exact, answer_bearing = _task_identities(seed)
    unreachable_exact, unreachable_answer_bearing = _task_identities(with_task(unreachable))
    _, allowed_answer_bearing = _task_identities(with_task(allowed))
    assert exact != unreachable_exact
    assert answer_bearing == unreachable_answer_bearing
    assert allowed_answer_bearing != answer_bearing


def test_projected_duplicates_are_excluded_before_the_source_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from foliqant_model.contracts.inputs import (
        ChatMessage,
        DataRecord,
        ResolvedSourceDeclaration,
    )
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CurationPlan, ImportedRecord
    from foliqant_model.curation.decision_contracts import DecisionSource

    labels = ["cash_withdrawal", "card_payment"]
    requests = {
        "exact-a": "Repeated cash request.",
        "exact-b": "Repeated cash request.",
        "metadata-a": "Repeated card request.",
        "metadata-b": "Repeated card request.",
        "unique": "A unique request.",
    }
    rows = []
    families = {}
    for key, request in requests.items():
        family = "family-" + key
        record = DataRecord(
            schemaVersion=1,
            id="record-" + key,
            sourceId="banking77",
            language="en",
            groupKeys=["group-" + key],
            familyId=family,
            origin="human",
            reviewed=False,
            messages=[
                ChatMessage(role="system", content="Classify the request."),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        {"labels": labels, "request": request}, separators=(",", ":")
                    ),
                ),
                ChatMessage(role="assistant", content='{"intent":"cash_withdrawal"}'),
            ],
        )
        rows.append(
            ImportedRecord(
                record=record,
                originalSplit="train",
                task="classification",
                originalId=key,
            )
        )
        families[family] = FrozenFamilyAssignment(split="train", sourceSplits=["banking77:train"])
    plan = CurationPlan(
        configurationSha256="a" * 64,
        catalogSha256="b" * 64,
        records=rows,
        frozenFamilies=families,
        excludedCounts={},
    )
    rights = ResolvedSourceDeclaration(
        id="banking77",
        license="test",
        licenseEvidence="test fixture",
        trainingAllowed=True,
        sharedTrainingAllowed=True,
        redistributionAllowed=False,
        privacy="public",
        commercialUse="restricted",
        attribution="test",
        restrictions=[],
    )
    original_project = runner.project_source

    def project_with_unavailable_metadata(row):  # type: ignore[no-untyped-def]
        projected = original_project(row)
        assert projected is not None
        if not row.originalId.startswith("metadata-"):
            return projected
        task = projected.input.model_copy(deep=True)
        task.state.sources.append(
            DecisionSource(
                id="unavailable-metadata",
                kind="metadata",
                text="Audit marker " + row.originalId,
            )
        )
        parent = projected.parent.model_copy(
            update={
                "messages": [
                    *projected.parent.messages[:-2],
                    ChatMessage(role="user", content=task.model_dump_json()),
                    projected.parent.messages[-1],
                ]
            }
        )
        return projected.model_copy(update={"parent": parent})

    monkeypatch.setattr(runner, "project_source", project_with_unavailable_metadata)
    config = CurationConfig.model_validate(
        {
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 3,
                "minimumAcceptedPerCell": 0,
            }
        }
    )
    seeds, actual_families, _, counts = runner._seeds_and_families(config, plan, [rights])
    projected = [seed for seed in seeds if seed.parent.sourceId == "native-banking77"]
    assert len(projected) == 3
    assert counts == {
        "banking77:exact-duplicate": 1,
        "banking77:projected": 5,
        "banking77:selected": 3,
        "banking77:unreachable-source-only-duplicate": 1,
    }
    assert sum(family in actual_families for family in families) == 3


def test_projected_duplicate_with_conflicting_target_fails_closed() -> None:
    from foliqant_model.contracts.inputs import ChatMessage
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.errors import ModelError

    seed = next(
        item
        for item in build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])
        if item.scenario == "choice-answerable"
    )
    conflicting = seed.oracle.model_copy(deep=True)
    result = conflicting.results[0]
    assert result.type == "choice" and result.answer is not None
    result.answer.optionId = (
        "balance_request" if result.answer.optionId != "balance_request" else "address_change"
    )
    first = seed.model_copy(update={"mode": "annotate", "rewriteSourceIds": []})
    second_parent = seed.parent.model_copy(
        update={
            "id": "conflicting-projection",
            "messages": [
                *seed.parent.messages[:-1],
                ChatMessage(role="assistant", content=conflicting.model_dump_json()),
            ],
        }
    )
    second = first.model_copy(update={"parent": second_parent})
    family = seed.parent.familyId
    assert family is not None
    families = {
        family: FrozenFamilyAssignment(
            split="train", sourceSplits=["foliqant-decisions:unspecified"]
        )
    }
    with pytest.raises(ModelError, match="conflicting targets"):
        runner._validate_seed_partitions([first, second], families)


def test_verified_annotation_publishes_original_once_without_generated_prose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateOutcome
    from foliqant_model.curation.storage import load_object

    _install_test_pipeline(monkeypatch)
    original_plan = runner._seeds_and_families

    def annotation_plan(*args):  # type: ignore[no-untyped-def]
        seeds, families, rights, counts = original_plan(*args)
        return (
            [seed.model_copy(update={"mode": "annotate"}) for seed in seeds],
            families,
            rights,
            counts,
        )

    def verify(config, *, identity, seed, job, cache_dir):  # type: ignore[no-untyped-def]
        return CandidateOutcome(
            jobId=job.jobId,
            status="accepted",
            reason="automated-checks-passed",
            attempts=1,
            requestSha256="a" * 64,
            responseSha256="b" * 64,
            record=seed.parent,
        )

    monkeypatch.setattr(runner, "_seeds_and_families", annotation_plan)
    monkeypatch.setattr(runner, "generate_decision_candidate", verify)
    config = CurationConfig.model_validate(
        {
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 2, "maxAttempts": 1},
        }
    )
    result = runner.run_decision_curation(config, tmp_path)
    run = Path(result.runPath)
    train = [
        json.loads(line)
        for line in (run / "datasets/native-decisions/records/train.jsonl").read_text().splitlines()
    ]
    assert len(train) == 2 and all("generation" not in record for record in train)
    seeds = {
        entry["parent"]["id"]: entry["parent"] for entry in load_object(run / "native-seeds.json")
    }
    assert all(record == seeds[record["id"]] for record in train)
    coverage = load_object(run / "coverage.json")
    assert coverage["acceptedKinds"] == {"generatedDerivative": 0, "verifiedProjection": 2}
    assert coverage["publishedRows"]["lineageParents"] == 0
    assert runner.run_decision_curation(config, tmp_path) == result


def test_cached_reference_prose_cannot_be_replaced_by_matching_label() -> None:
    from foliqant_model.contracts.inputs import ChatMessage
    from foliqant_model.curation.decision_runner import _validate_record
    from foliqant_model.errors import ModelError

    seed = build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])[0]
    altered = seed.oracle.model_copy(deep=True)
    altered.results[0].explanation.summary = "An additional unspecified contract must be followed."
    record = seed.parent.model_copy(
        update={
            "messages": [
                *seed.parent.messages[:-1],
                ChatMessage(role="assistant", content=altered.model_dump_json()),
            ]
        }
    )
    with pytest.raises(ModelError, match="canonical reference prose"):
        _validate_record(seed, record)


def test_cached_rewrite_cannot_change_uncited_metadata() -> None:
    from foliqant_model.contracts.inputs import ChatMessage
    from foliqant_model.curation.decision_contracts import DecisionSource
    from foliqant_model.curation.decision_runner import _validate_record
    from foliqant_model.errors import ModelError

    seed = build_authored_seeds(DecisionDataSettings(), seed=42, languages=["en"])[0]
    original = seed.input.model_copy(deep=True)
    original.state.sources.append(
        DecisionSource(id="audit-log", kind="metadata", text="Queue position 1.")
    )
    parent = seed.parent.model_copy(
        update={
            "messages": [
                *seed.parent.messages[:-2],
                ChatMessage(role="user", content=original.model_dump_json()),
                seed.parent.messages[-1],
            ]
        }
    )
    seed = seed.model_copy(update={"parent": parent})
    changed = original.model_copy(deep=True)
    changed.state.sources[-1].text = "Queue position 2."
    record = parent.model_copy(
        update={
            "messages": [
                *parent.messages[:-2],
                ChatMessage(role="user", content=changed.model_dump_json()),
                parent.messages[-1],
            ]
        }
    )
    with pytest.raises(ModelError, match="reference support"):
        _validate_record(seed, record)


def test_native_repair_run_retries_only_rejects_and_preserves_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_model.contracts.base import canonical_digest
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateJob, CandidateOutcome
    from foliqant_model.curation.decision_runner import run_decision_curation
    from foliqant_model.curation.storage import load_object
    from foliqant_model.errors import ModelError

    _install_test_pipeline(monkeypatch)
    accepted_generator = runner.generate_decision_candidate
    config = CurationConfig.model_validate(
        {
            "sources": [{"id": "banking77", "maxRecords": 10}],
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 2, "maxAttempts": 1},
        }
    )
    first_calls: list[str] = []

    def first_generate(
        config,
        *,
        identity,
        seed,
        job,
        cache_dir,
    ):  # type: ignore[no-untyped-def]
        first_calls.append(job.jobId)
        if len(first_calls) == 1:
            return accepted_generator(
                config, identity=identity, seed=seed, job=job, cache_dir=cache_dir
            )
        return CandidateOutcome(
            jobId=job.jobId,
            status="quarantined",
            reason="solver-disagreed",
            attempts=1,
            requestSha256=canonical_digest({"request": job.jobId}),
            responseSha256=canonical_digest({"response": job.jobId}),
            record=None,
        )

    monkeypatch.setattr(runner, "generate_decision_candidate", first_generate)
    first = run_decision_curation(config, tmp_path)
    parent = Path(first.runPath)
    parent_jobs = [
        CandidateJob.model_validate(value) for value in load_object(parent / "jobs.json")
    ]
    accepted_job, rejected_job = parent_jobs
    accepted_bytes = (parent / "outcomes" / f"{accepted_job.jobId}.json").read_bytes()

    def snapshot(directory: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file() and path.name not in {"progress.json", "run.lock"}
        }

    parent_before = snapshot(parent)
    repair_calls: list[CandidateJob] = []

    def repair_generate(
        selected_config,
        *,
        identity,
        seed,
        job,
        cache_dir,
        prior_rejection,
        prior_response,
        request_namespace,
    ):  # type: ignore[no-untyped-def]
        assert prior_rejection.jobId == rejected_job.jobId
        assert prior_rejection.status == "quarantined"
        assert prior_response is None
        assert request_namespace.startswith("repair-")
        repair_calls.append(job)
        return accepted_generator(
            selected_config,
            identity=identity,
            seed=seed,
            job=job,
            cache_dir=cache_dir,
        )

    monkeypatch.setattr(runner, "generate_decision_candidate", repair_generate)
    repaired = run_decision_curation(config, tmp_path, repair_from=parent)
    child = Path(repaired.runPath)

    assert repaired.generatedAccepted == 2 and repaired.generatedQuarantined == 0
    assert len(repair_calls) == 1
    assert repair_calls[0].jobId != rejected_job.jobId
    assert (child / "outcomes" / f"{accepted_job.jobId}.json").read_bytes() == accepted_bytes
    assert parent_before == snapshot(parent)
    assert any(item.outputPath.endswith("native-decisions") for item in repaired.datasets)
    assert run_decision_curation(config, tmp_path, repair_from=parent) == repaired
    assert len(repair_calls) == 1

    changed = config.model_copy(
        update={"generation": config.generation.model_copy(update={"maxCandidates": 3})}
    )
    with pytest.raises(ModelError, match="configuration differs"):
        run_decision_curation(changed, tmp_path, repair_from=parent)
    assert len(repair_calls) == 1


def test_continue_partial_native_run_after_recipe_change_preserves_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foliqant_model.curation import decision_runner as runner
    from foliqant_model.curation.contracts import CandidateOutcome
    from foliqant_model.curation.storage import load_object
    from foliqant_model.errors import ModelError

    calls = _install_test_pipeline(monkeypatch)
    generate = runner.generate_decision_candidate
    config = CurationConfig.model_validate(
        {
            "sources": [{"id": "banking77", "maxRecords": 10}],
            "decisionData": {
                "examplesPerScenario": 4,
                "sourceExamplesPerSource": 0,
                "minimumAcceptedPerCell": 0,
            },
            "generation": {"maxCandidates": 3, "maxAttempts": 1},
        }
    )
    attempted = 0

    def stop_partway(config, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal attempted
        attempted += 1
        if attempted == 1:
            return generate(config, **kwargs)
        if attempted == 2:
            return CandidateOutcome(
                jobId=kwargs["job"].jobId,
                status="quarantined",
                reason="solver-disagreed",
                attempts=1,
                requestSha256="b" * 64,
                responseSha256="c" * 64,
                record=None,
            )
        raise ModelError("TIMEOUT", "test timeout")

    monkeypatch.setattr(runner, "generate_decision_candidate", stop_partway)
    with pytest.raises(ModelError, match="deadline"):
        runner.run_decision_curation(config, tmp_path)
    parent = next((tmp_path / "curation").iterdir())
    before = {str(f.relative_to(parent)): f.read_bytes() for f in parent.rglob("*") if f.is_file()}
    original_jobs = load_object(parent / "jobs.json")
    original_outcome = load_object(parent / "outcomes" / (original_jobs[0]["jobId"] + ".json"))
    old_recipe = original_outcome["record"]["generation"]["promptSha256"]
    monkeypatch.setattr(runner, "decision_generation_recipe_digest", lambda: "f" * 64)
    monkeypatch.setattr(runner, "generate_decision_candidate", generate)

    prepared = runner.run_decision_curation(
        config, tmp_path, continue_from=parent, prepare_only=True
    )
    assert prepared.generatedAccepted == 0 and prepared.generatedQuarantined == 0
    assert len(calls) == 1
    child = Path(prepared.runPath)
    jobs = load_object(child / "jobs.json")
    assert jobs[:2] == original_jobs[:2]
    assert jobs[2]["jobId"] != original_jobs[2]["jobId"]
    assert load_object(child / "outcomes" / (jobs[0]["jobId"] + ".json")) == original_outcome

    result = runner.run_decision_curation(config, tmp_path, continue_from=parent)
    assert result.generatedAccepted == 2 and result.generatedQuarantined == 1
    assert len(calls) == 2
    assert (
        load_object(child / "outcomes" / (jobs[0]["jobId"] + ".json"))["record"]["generation"][
            "promptSha256"
        ]
        == old_recipe
    )
    assert (
        load_object(child / "outcomes" / (jobs[2]["jobId"] + ".json"))["record"]["generation"][
            "promptSha256"
        ]
        == "f" * 64
    )
    assert runner.run_decision_curation(config, tmp_path, continue_from=parent) == result
    assert len(calls) == 2
    assert before == {
        str(f.relative_to(parent)): f.read_bytes() for f in parent.rglob("*") if f.is_file()
    }

    def repair(config, **kwargs):  # type: ignore[no-untyped-def]
        for key in ("prior_rejection", "prior_response", "request_namespace"):
            kwargs.pop(key)
        return generate(config, **kwargs)

    monkeypatch.setattr(runner, "generate_decision_candidate", repair)
    repaired = runner.run_decision_curation(config, tmp_path, repair_from=child)
    assert repaired.generatedAccepted == 3 and repaired.generatedQuarantined == 0
    assert len(calls) == 3

    with pytest.raises(ModelError, match="configuration differs"):
        runner.run_decision_curation(
            config.model_copy(update={"seed": config.seed + 1}), tmp_path, continue_from=parent
        )
    with pytest.raises(ModelError, match="not both"):
        runner.run_decision_curation(config, tmp_path, continue_from=parent, repair_from=parent)
    outcome_path = parent / "outcomes" / (original_jobs[0]["jobId"] + ".json")
    outcome_path.write_text("{}")
    with pytest.raises(ModelError):
        runner.run_decision_curation(config, tmp_path, continue_from=parent)
    assert len(calls) == 3

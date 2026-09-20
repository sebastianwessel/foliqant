"""Projection children preserve completed work and use only new train tasks."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_decision_runner import _install_test_pipeline

from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord, GenerationProvenance
from foliqant_model.curation import decision_runner as runner
from foliqant_model.curation.contracts import CandidateOutcome, CurationConfig, SourceBatch
from foliqant_model.curation.projection_preparation import prepare_source_projections
from foliqant_model.curation.storage import load_object, run_lock, store_object
from foliqant_model.errors import ModelError


def _pipeline(monkeypatch: pytest.MonkeyPatch) -> tuple[CurationConfig, list[str]]:
    calls = _install_test_pipeline(monkeypatch)
    original_sources = runner._source_batches

    def sources(config, run, cache, offline):  # type: ignore[no-untyped-def]
        base = original_sources(config, run, cache, offline)[0]
        import json

        rows = []
        for index, row in enumerate([*base.records, *([base.records[0]] * 8)]):
            record = row.record.model_copy(
                update={
                    "id": "table-" + str(index) + "-" + row.record.id,
                    "sourceId": "tatqa",
                    "groupKeys": ["table-" + str(index) + "-" + row.record.groupKeys[0]],
                    "messages": [
                        ChatMessage(
                            role="user",
                            content=json.dumps(
                                {
                                    "table": [
                                        ["Metric", "2021", "2020"],
                                        ["Revenue " + str(index), "12", "10"],
                                    ],
                                    "paragraphs": [],
                                    "question": "Unused original QA",
                                }
                            ),
                        ),
                        ChatMessage(role="assistant", content='{"answer":"unused"}'),
                    ],
                }
            )
            rows.append(row.model_copy(update={"record": record, "task": "question-answering"}))
        batch = SourceBatch(
            source=base.source.model_copy(update={"id": "tatqa"}),
            revision="test",
            assets=[],
            records=rows,
            totalAvailable=len(rows),
        )
        from foliqant_model.curation import projection_preparation

        monkeypatch.setattr(
            projection_preparation,
            "_load_catalog",
            lambda: SimpleNamespace(sources={"banking77": base, "tatqa": batch}),
        )
        store_object(run / "sources/tatqa.json", batch.model_dump(mode="json"))
        return [base, batch]

    def generate(config, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["seed"].mode == "annotate":
            calls.append(kwargs["job"].parentRecordId)
            return CandidateOutcome(
                jobId=kwargs["job"].jobId,
                status="accepted",
                reason="automated-checks-passed",
                attempts=1,
                requestSha256="b" * 64,
                responseSha256="c" * 64,
                record=kwargs["seed"].parent,
            )
        seed, job, identity = kwargs["seed"], kwargs["job"], kwargs["identity"]
        calls.append(job.parentRecordId)
        task = seed.input.model_copy(deep=True)
        for source in task.state.sources:
            if source.id in seed.rewriteSourceIds:
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
                    *[m.model_dump(mode="json") for m in seed.parent.messages[:-2]],
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
            requestSha256="b" * 64,
            responseSha256="c" * 64,
            record=record,
        )

    monkeypatch.setattr(runner, "_source_batches", sources)
    monkeypatch.setattr(runner, "generate_decision_candidate", generate)
    config = CurationConfig.model_validate(
        {
            "sources": [{"id": "banking77", "maxRecords": 10}, {"id": "tatqa", "maxRecords": 20}],
            "decisionData": {"sourceExamplesPerSource": 10, "minimumAcceptedPerCell": 0},
            "generation": {"maxCandidates": 1000, "maxAttempts": 1},
        }
    )
    return config, calls


def _bytes(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_extension_preparation_resume_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, calls = _pipeline(monkeypatch)
    initial_generate = runner.generate_decision_candidate

    def parent_generate(config, **kwargs):  # type: ignore[no-untyped-def]
        if not calls:
            calls.append(kwargs["job"].parentRecordId)
            return CandidateOutcome(
                jobId=kwargs["job"].jobId,
                status="quarantined",
                reason="solver-disagreed",
                attempts=1,
                requestSha256="b" * 64,
                responseSha256="c" * 64,
                record=None,
            )
        return initial_generate(config, **kwargs)

    monkeypatch.setattr(runner, "generate_decision_candidate", parent_generate)
    completed = runner.run_decision_curation(config, tmp_path)
    monkeypatch.setattr(runner, "generate_decision_candidate", initial_generate)
    parent = Path(completed.runPath)
    before = _bytes(parent)
    old_jobs = load_object(parent / "jobs.json")
    plan_result = prepare_source_projections(parent, tmp_path / "projections")
    assert plan_result.selectedRecords == 10
    # Identical inputs in another run still require that run's own preparation.
    import shutil

    from foliqant_model.curation.projection_extension import load_projection_extension

    other_parent = parent.with_name(parent.name + "-copy")
    shutil.copytree(parent, other_parent)
    with pytest.raises(ModelError, match="different parent run"):
        load_projection_extension(
            tmp_path,
            other_parent,
            config,
            Path(plan_result.planPath),
            recipe=runner.decision_generation_recipe_digest(),
        )
    initial_calls = len(calls)
    discovery = runner.discover_models
    monkeypatch.setattr(runner, "discover_models", lambda *_: pytest.fail("offline discovery"))
    prepared = runner.run_decision_curation(
        config,
        tmp_path,
        extend_projections_from=parent,
        projection_plan=Path(plan_result.planPath),
        prepare_only=True,
    )
    child = Path(prepared.runPath)
    child_jobs = load_object(child / "jobs.json")
    assert child_jobs[: len(old_jobs)] == old_jobs
    assert len(child_jobs) > len(old_jobs)
    assert len(calls) == initial_calls
    for job in old_jobs:
        name = "outcomes/" + job["jobId"] + ".json"
        assert (child / name).read_bytes() == (parent / name).read_bytes()
    monkeypatch.setattr(runner, "discover_models", discovery)
    good_generate = runner.generate_decision_candidate

    def reject(config, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs["job"].parentRecordId)
        return CandidateOutcome(
            jobId=kwargs["job"].jobId,
            status="quarantined",
            reason="solver-disagreed",
            attempts=1,
            requestSha256="b" * 64,
            responseSha256="c" * 64,
            record=None,
        )

    monkeypatch.setattr(runner, "generate_decision_candidate", reject)
    from foliqant_model.curation.runtime import CurationControl

    class StopAfterFirstNew(CurationControl):
        def report(self, phase, run, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("completed") == len(old_jobs) + 1:
                self._pause_requested = True

    with pytest.raises(ModelError, match="paused"):
        runner.run_decision_curation(
            config,
            tmp_path,
            extend_projections_from=parent,
            projection_plan=Path(plan_result.planPath),
            control=StopAfterFirstNew(),
        )
    result = runner.run_decision_curation(
        config,
        tmp_path,
        extend_projections_from=parent,
        projection_plan=Path(plan_result.planPath),
    )
    assert result.generatedAccepted == completed.generatedAccepted
    assert result.generatedQuarantined == completed.generatedQuarantined + len(child_jobs) - len(
        old_jobs
    )
    assert len(calls) == initial_calls + len(child_jobs) - len(old_jobs)
    assert _bytes(parent) == before

    with pytest.raises(ModelError, match="configuration differs"):
        runner.run_decision_curation(
            config.model_copy(update={"seed": config.seed + 1}),
            tmp_path,
            extend_projections_from=parent,
            projection_plan=Path(plan_result.planPath),
        )
    assert (
        runner.run_decision_curation(
            config,
            tmp_path,
            extend_projections_from=parent,
            projection_plan=Path(plan_result.planPath),
        )
        == result
    )
    assert len(calls) == len(child_jobs)

    def repair(config, **kwargs):  # type: ignore[no-untyped-def]
        for key in ("prior_rejection", "prior_response", "request_namespace"):
            kwargs.pop(key, None)
        return good_generate(config, **kwargs)

    monkeypatch.setattr(runner, "generate_decision_candidate", repair)
    repaired = runner.run_decision_curation(config, tmp_path, repair_from=child)
    assert repaired.generatedQuarantined == 0
    assert repaired.generatedAccepted == len(child_jobs)
    assert _bytes(parent) == before

    from foliqant_model.curation.projection_extension import load_inherited_projection

    assert load_inherited_projection(Path(repaired.runPath)) == load_inherited_projection(child)
    marker_path = child / "projection-extension.json"
    marker = load_object(marker_path)
    marker["projectionPlanSha256"] = "0" * 64
    marker_path.unlink()
    store_object(marker_path, marker)
    with pytest.raises(ModelError, match="ancestry differs"):
        load_inherited_projection(child)


def test_extension_requires_finished_parent_and_argument_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = _pipeline(monkeypatch)
    prepared = runner.run_decision_curation(config, tmp_path, prepare_only=True)
    parent = Path(prepared.runPath)
    plan = prepare_source_projections(parent, tmp_path / "projections")
    monkeypatch.setattr(runner, "discover_models", lambda *_: pytest.fail("unexpected discovery"))
    with pytest.raises(ModelError):
        runner.run_decision_curation(
            config,
            tmp_path,
            extend_projections_from=parent,
            projection_plan=Path(plan.planPath),
        )
    with pytest.raises(ModelError, match="both parent and plan"):
        runner.run_decision_curation(config, tmp_path, extend_projections_from=parent)
    with run_lock(parent):
        with pytest.raises(ModelError):
            runner.run_decision_curation(
                config,
                tmp_path,
                extend_projections_from=parent,
                projection_plan=Path(plan.planPath),
                prepare_only=True,
            )


def test_extension_reserves_old_job_budget() -> None:
    from foliqant_model.contracts.inputs import FrozenFamilyAssignment
    from foliqant_model.curation.projection_extension import extension_jobs

    seed = SimpleNamespace(parent=SimpleNamespace(familyId="new-family"))
    extension = SimpleNamespace(
        parent=SimpleNamespace(jobs=[object()]),
        plan=SimpleNamespace(
            seeds=[seed],
            frozenFamilies={
                "new-family": FrozenFamilyAssignment(split="train", sourceSplits=["fixture:train"])
            },
        ),
    )
    config = CurationConfig.model_validate({"generation": {"maxCandidates": 1}})
    with pytest.raises(ModelError, match="budget"):
        extension_jobs(extension, [object()], config)  # type: ignore[arg-type]

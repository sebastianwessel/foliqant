"""Deterministic scenario and resumable local-generation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from foliqant_model.contracts.base import JsonValue, canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord
from foliqant_model.curation import generation
from foliqant_model.curation.contracts import CandidateJob, CurationConfig, GenerationSettings
from foliqant_model.curation.endpoint import (
    EndpointModelIdentity,
    GenerationRejected,
    GenerationRejection,
    GenerationResponse,
)
from foliqant_model.curation.generation import generate_candidate
from foliqant_model.curation.scenarios import scenario_records
from foliqant_model.errors import ModelError
from foliqant_model.scoring import parse_strict_json


def _config(*, attempts: int = 2, families: int = 10) -> CurationConfig:
    return CurationConfig(
        generation=GenerationSettings(
            maxCandidates=100,
            maxAttempts=attempts,
            languages=["en", "de"],
            scenarios=[
                "withdrawn-request",
                "multiple-intents",
                "missing-evidence",
                "conflicting-information",
                "changed-deadline",
            ],
            scenarioFamilies=families,
            maxInputCharacters=16_000,
        )
    )


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="local-model",
        modelType="llm",
        publisher="local",
        architecture="test",
        format="gguf",
        quantization="q4",
        sizeBytes=1024,
        maxContextLength=8192,
        metadataSha256="a" * 64,
        immutableRevision=None,
        structuredOutput="unknown",
    )


def _job(
    parent: DataRecord, *, purpose: str = "synthetic-regression", split: str = "test"
) -> CandidateJob:
    assert parent.familyId is not None
    scenario_tag = next(tag for tag in parent.tags if tag.startswith("scenario:"))
    return CandidateJob.model_validate(
        {
            "jobId": canonical_digest(
                {"family": parent.familyId, "purpose": purpose, "split": split}
            ),
            "parentRecordId": parent.id,
            "familyId": parent.familyId,
            "split": split,
            "language": parent.language,
            "purpose": purpose,
            "operation": scenario_tag.removeprefix("scenario:"),
        }
    )


def _response(
    config: CurationConfig,
    identity: EndpointModelIdentity,
    output: dict[str, object],
    schema: dict[str, object],
    marker: str,
    messages: list[ChatMessage],
    seed: int,
) -> GenerationResponse:
    model = EndpointModelIdentity.model_validate(
        {**identity.model_dump(mode="json"), "structuredOutput": "verified-for-request"}
    )
    return GenerationResponse(
        model=model,
        output=cast(dict[str, JsonValue], output),
        finishReason="stop",
        requestSha256=generation._endpoint_request_sha256(
            config,
            identity,
            messages=messages,
            schema=schema,
            seed=seed,
        ),
        schemaSha256=canonical_digest(schema),
        rawResponseSha256=canonical_digest({"response": marker, "output": output}),
        elapsedSeconds=0.1,
    )


def test_scenarios_are_deterministic_varied_and_human_claim_free() -> None:
    config = _config(families=10)
    first = scenario_records(config)
    second = scenario_records(config)

    assert first == second
    assert len(first) == config.generation.scenarioFamilies
    assert len({row.record.familyId for row in first}) == len(first)
    assert {row.record.language for row in first} == {"en", "de"}
    assert {tag for row in first for tag in row.record.tags if tag.startswith("scenario:")} == {
        f"scenario:{scenario}" for scenario in config.generation.scenarios
    }
    for row in first:
        record = row.record
        assert row.originalSplit == "unspecified"
        assert record.sourceId == "foliqant-scenarios"
        assert record.origin == "synthetic"
        assert record.reviewed is False
        assert record.familyId is not None
        assert record.groupKeys == [record.familyId]
        prompt = record.messages[-2].content
        assert "JSON" in prompt
        assert "Authored scenario:" not in prompt
        assert "scenario:" not in prompt
        assert "withdrawn request maps to" in record.messages[0].content
        expected = parse_strict_json(record.messages[-1].content)
        assert expected.valid and isinstance(expected.value, dict)
        evidence = expected.value["evidence"]
        assert isinstance(evidence, list)
        assert all(isinstance(item, str) and item in prompt for item in evidence)


def test_generation_recipe_identity_binds_retry_feedback_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = generation.generation_recipe_digest()
    monkeypatch.setattr(generation, "_RETRY_FEEDBACK_VERSION", "changed-for-test")
    assert generation.generation_recipe_digest() != before


def test_request_format_versions_both_generation_recipes(monkeypatch: pytest.MonkeyPatch) -> None:
    from foliqant_model.curation import decision_generation

    generic = generation.generation_recipe_digest()
    native = decision_generation.decision_generation_recipe_digest()
    monkeypatch.setattr(generation, "GENERATION_REQUEST_FORMAT", "changed-for-test")
    monkeypatch.setattr(decision_generation, "GENERATION_REQUEST_FORMAT", "changed-for-test")
    assert generation.generation_recipe_digest() != generic
    assert decision_generation.decision_generation_recipe_digest() != native


def test_schema_order_changes_call_identity() -> None:
    schemas = [{"properties": {"zulu": {}, "alpha": {}}}, {"properties": {"alpha": {}, "zulu": {}}}]
    calls = [
        generation._call_identity(
            _config(),
            _identity(),
            messages=[ChatMessage(role="user", content="Test")],
            schema=schema,
            seed=1,
            prompt_version="test",
        )
        for schema in schemas
    ]
    assert canonical_digest(schemas[0]) == canonical_digest(schemas[1])
    assert canonical_digest(calls[0]) != canonical_digest(calls[1])


def test_generation_accepts_checked_candidate_and_resumes_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    parent = scenario_records(config)[0].record
    job = _job(parent)
    identity = _identity()
    calls: list[dict[str, object]] = []

    def fake_generate(
        _endpoint: object,
        *,
        model_id: str,
        messages: list[ChatMessage],
        schema: dict[str, object],
        seed: int,
    ) -> GenerationResponse:
        calls.append({"messages": messages, "model": model_id, "schema": schema, "seed": seed})
        if schema["required"] == ["input"]:
            output: dict[str, object] = {"input": parent.messages[-2].content}
        else:
            expected = parent.messages[-1].content
            checker_text = "\n".join(message.content for message in messages)
            assert expected not in checker_text
            assert job.operation not in checker_text
            output = {"answer": expected, "supported": True, "issues": []}
        return _response(
            config,
            identity,
            output,
            schema,
            f"{len(calls)}-{seed}",
            messages,
            seed,
        )

    monkeypatch.setattr(generation, "generate_json", fake_generate)
    first = generate_candidate(
        config,
        identity=identity,
        parent=parent,
        job=job,
        cache_dir=tmp_path,
    )
    assert first.status == "accepted"
    assert first.attempts == 1
    assert len(calls) == 2
    assert first.record is not None
    assert first.record.id == f"generated-{job.jobId}"
    assert first.record.sourceId == "generated-foliqant-scenarios"
    assert first.record.familyId == parent.familyId
    assert first.record.groupKeys == parent.groupKeys
    assert first.record.origin == "teacher"
    assert first.record.reviewed is False
    assert first.record.generation is not None
    assert first.record.generation.provider == "openai-compatible"
    assert first.record.generation.modelIdentitySha256 == identity.metadataSha256
    assert first.record.generation.parentRecordIds == [parent.id]
    assert first.record.generation.modelWeightsSha256 is None

    def unavailable(*_args: object, **_kwargs: object) -> GenerationResponse:
        raise AssertionError("resume must not call the endpoint")

    monkeypatch.setattr(generation, "generate_json", unavailable)
    resumed = generate_candidate(
        config,
        identity=identity,
        parent=parent,
        job=job,
        cache_dir=tmp_path,
    )
    assert resumed == first


def test_invalid_final_response_is_cached_completely_and_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(attempts=1)
    identity = _identity()
    messages = [ChatMessage(role="user", content="Return the object")]
    schema = {"type": "object", "required": ["input"]}
    seed = 17
    final_response = "{" + ("x" * 40_000)
    calls = 0

    def rejected(*_args: object, **_kwargs: object) -> GenerationResponse:
        nonlocal calls
        calls += 1
        raise GenerationRejected(
            GenerationRejection(
                message="Generated structured output is not strict JSON",
                requestSha256=generation._endpoint_request_sha256(
                    config,
                    identity,
                    messages=messages,
                    schema=schema,
                    seed=seed,
                ),
                rawResponseSha256=canonical_digest({"raw": final_response}),
                finalAssistantResponse=final_response,
            )
        )

    monkeypatch.setattr(generation, "generate_json", rejected)
    for _ in range(2):
        with pytest.raises(GenerationRejected) as failure:
            generation._cached_generation(
                config,
                identity,
                messages=messages,
                schema=schema,
                seed=seed,
                prompt_version="test-invalid-v1",
                cache_dir=tmp_path,
            )
        assert failure.value.rejection.finalAssistantResponse == final_response

    assert calls == 1
    cached = list((tmp_path / "calls").glob("*.json"))
    assert len(cached) == 1
    assert generation.load_cached_final_response(tmp_path, cached[0].stem) == final_response


def test_checker_prompt_requires_complete_task_serialization_without_reference_answer() -> None:
    config = _config()
    parent = scenario_records(config)[3].record
    job = _job(parent)
    candidate = parent.messages[-2].content
    messages = generation._checker_messages(candidate, parent, job)

    combined = "\n".join(message.content for message in messages)
    assert parent.messages[-1].content not in combined
    assert "entire serialized JSON object" in messages[0].content
    assert "including every required field and value" in messages[0].content
    assert (
        "manual-review decision can be the fully supported intended answer" in messages[0].content
    )
    assert "copy the complete exact text inside the relevant quotation marks" in messages[0].content
    assert "without the quote characters" in messages[0].content
    assert "intentional conflicts" in messages[0].content
    payload = json.loads(messages[1].content)
    assert "operation" not in payload
    assert payload["candidateInput"] == candidate
    assert "whole object with every requested field" in payload["task"]
    assert "leave issues empty unless the task itself is malformed" in payload["task"]


def test_changed_facts_are_quarantined_with_distinct_retry_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(attempts=2)
    parent = scenario_records(config)[0].record
    job = _job(parent)
    identity = _identity()
    seeds: list[int] = []
    message_sets: list[list[ChatMessage]] = []

    def changed_fact(
        _endpoint: object,
        *,
        model_id: str,
        messages: list[ChatMessage],
        schema: dict[str, object],
        seed: int,
    ) -> GenerationResponse:
        del model_id
        seeds.append(seed)
        message_sets.append(messages)
        return _response(
            config,
            identity,
            {"input": parent.messages[-2].content + " Added quantity 999999."},
            schema,
            f"changed-{seed}",
            messages,
            seed,
        )

    monkeypatch.setattr(generation, "generate_json", changed_fact)
    outcome = generate_candidate(
        config,
        identity=identity,
        parent=parent,
        job=job,
        cache_dir=tmp_path,
    )
    assert outcome.status == "quarantined"
    assert outcome.reason == "candidate-numbers-changed"
    assert outcome.attempts == 2
    assert outcome.record is None
    assert len(seeds) == 2 and len(set(seeds)) == 2
    assert [trace.status for trace in outcome.attemptTrace] == [
        "quarantined",
        "quarantined",
    ]
    assert all(trace.reason == "candidate-numbers-changed" for trace in outcome.attemptTrace)
    assert all(trace.calls[0].phase == "generator" for trace in outcome.attemptTrace)
    assert outcome.attemptTrace[0].calls[0].callId != outcome.attemptTrace[1].calls[0].callId
    assert [message.role for message in message_sets[1][-2:]] == ["assistant", "user"]
    correction = json.loads(message_sets[1][-1].content)
    assert correction["rejectionReason"] == "candidate-numbers-changed"
    assert "answer" not in correction


def test_plain_reference_label_in_candidate_is_not_treated_as_answer_leak() -> None:
    parent = DataRecord.model_validate(
        {
            "schemaVersion": 1,
            "id": "classification-parent",
            "sourceId": "source",
            "language": "en",
            "groupKeys": ["classification-family"],
            "familyId": "classification-family",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Allowed labels: card_arrival, cash_withdrawal. Classify card_arrival."
                    ),
                },
                {"role": "assistant", "content": "card_arrival"},
            ],
            "tags": [],
            "origin": "human",
            "reviewed": False,
        }
    )
    assert (
        generation._candidate_problem(
            parent,
            "Allowed labels: card_arrival, cash_withdrawal. Classify card_arrival.",
            max_characters=16_000,
        )
        is None
    )


def test_oversized_parent_is_quarantined_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    scenario = scenario_records(config)[0].record
    payload = scenario.model_dump(mode="json")
    payload["messages"][-2]["content"] = "x" * (config.generation.maxInputCharacters + 1)
    parent = DataRecord.model_validate(payload)
    calls = 0

    def forbidden(*_args: object, **_kwargs: object) -> GenerationResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("oversized parent must not reach endpoint")

    monkeypatch.setattr(generation, "generate_json", forbidden)
    outcome = generate_candidate(
        config,
        identity=_identity(),
        parent=parent,
        job=_job(parent),
        cache_dir=tmp_path,
    )
    assert outcome.status == "quarantined"
    assert outcome.reason == "parent-input-too-long"
    assert outcome.attempts == 1
    assert outcome.record is None
    assert calls == 0


def test_endpoint_outage_fails_the_run_instead_of_quarantining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    parent = scenario_records(config)[0].record

    def outage(*_args: object, **_kwargs: object) -> GenerationResponse:
        raise ModelError("NETWORK_FAILED", "Local endpoint is unavailable")

    monkeypatch.setattr(generation, "generate_json", outage)
    with pytest.raises(ModelError) as failure:
        generate_candidate(
            config,
            identity=_identity(),
            parent=parent,
            job=_job(parent),
            cache_dir=tmp_path,
        )
    assert failure.value.code == "NETWORK_FAILED"


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Local endpoint returned no structured output", "model-no-structured-output"),
        ("Local endpoint truncated the generated output", "model-output-truncated"),
        ("Generated output does not match its schema", "model-output-schema-invalid"),
        ("Unrecognized safe worker failure", "model-output-invalid"),
    ],
)
def test_safe_endpoint_output_failure_class_is_preserved_in_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    reason: str,
) -> None:
    config = _config(attempts=1)
    parent = scenario_records(config)[0].record

    def invalid(*_args: object, **_kwargs: object) -> GenerationResponse:
        raise ModelError("OUTPUT_INVALID", message)

    monkeypatch.setattr(generation, "generate_json", invalid)
    outcome = generate_candidate(
        config,
        identity=_identity(),
        parent=parent,
        job=_job(parent),
        cache_dir=tmp_path,
    )
    assert outcome.status == "quarantined"
    assert outcome.reason == reason
    assert outcome.record is None


def test_cache_tampering_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    parent = scenario_records(config)[0].record
    job = _job(parent)
    identity = _identity()

    def accepted(
        _endpoint: object,
        *,
        model_id: str,
        messages: list[ChatMessage],
        schema: dict[str, object],
        seed: int,
    ) -> GenerationResponse:
        del model_id
        output: dict[str, object]
        if schema["required"] == ["input"]:
            output = {"input": parent.messages[-2].content}
        else:
            output = {"answer": parent.messages[-1].content, "supported": True, "issues": []}
        return _response(
            config,
            identity,
            output,
            schema,
            f"{len(messages)}-{seed}",
            messages,
            seed,
        )

    monkeypatch.setattr(generation, "generate_json", accepted)
    generate_candidate(config, identity=identity, parent=parent, job=job, cache_dir=tmp_path)
    cached = sorted((tmp_path / "calls").glob("*.json"))[0]
    envelope = json.loads(cached.read_text(encoding="utf-8"))
    envelope["payload"]["response"]["finishReason"] = "changed"
    cached.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ModelError) as failure:
        generate_candidate(config, identity=identity, parent=parent, job=job, cache_dir=tmp_path)
    assert failure.value.code == "INTEGRITY_FAILED"


def test_job_boundaries_fail_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config()
    scenario = scenario_records(config)[0].record
    calls = 0

    def forbidden(*_args: object, **_kwargs: object) -> GenerationResponse:
        nonlocal calls
        calls += 1
        raise AssertionError

    monkeypatch.setattr(generation, "generate_json", forbidden)
    with pytest.raises(ModelError) as wrong_split:
        generate_candidate(
            config,
            identity=_identity(),
            parent=scenario,
            job=_job(scenario, purpose="training-augmentation", split="validation"),
            cache_dir=tmp_path,
        )
    assert wrong_split.value.code == "ARGUMENT_INVALID"

    payload = scenario.model_dump(mode="json")
    payload["sourceId"] = "imported-source"
    imported = DataRecord.model_validate(payload, strict=True)
    with pytest.raises(ModelError) as wrong_source:
        generate_candidate(
            config,
            identity=_identity(),
            parent=imported,
            job=_job(imported),
            cache_dir=tmp_path,
        )
    assert wrong_source.value.code == "ARGUMENT_INVALID"
    assert calls == 0

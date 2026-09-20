"""Regression coverage for scoped curation task-input rewriting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from foliqant_model.contracts.base import JsonValue, canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord
from foliqant_model.curation import generation
from foliqant_model.curation.contracts import CandidateJob, CurationConfig, GenerationSettings
from foliqant_model.curation.endpoint import EndpointModelIdentity, GenerationResponse
from foliqant_model.curation.task_input import (
    assemble_input,
    candidate_structure_problem,
    editable_input,
    task_name,
)


def _record(
    source_id: str,
    user: str,
    *,
    system: str = "Solve the supplied task and return only the required JSON.",
    answer: str = '{"result":"ok"}',
    history: list[ChatMessage] | None = None,
    tags: list[str] | None = None,
) -> DataRecord:
    family = f"{source_id}-family"
    return DataRecord(
        schemaVersion=1,
        id=f"{source_id}-record",
        sourceId=source_id,
        language="en",
        groupKeys=[family],
        familyId=family,
        messages=[
            ChatMessage(role="system", content=system),
            *(history or []),
            ChatMessage(role="user", content=user),
            ChatMessage(role="assistant", content=answer),
        ],
        tags=tags or [],
        origin="human",
        reviewed=False,
    )


def _banking77(*, history: list[ChatMessage] | None = None) -> DataRecord:
    return _record(
        "banking77",
        json.dumps(
            {
                "labels": ["cash_withdrawal", "card_arrival", "beneficiary_not_allowed"],
                "request": "Why were 5 euros charged?",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        system=(
            "Classify the request using the immutable BANKING77 set of 77 labels. "
            "Return only the intent JSON."
        ),
        answer='{"intent":"cash_withdrawal"}',
        history=history,
    )


def _job(parent: DataRecord) -> CandidateJob:
    assert parent.familyId is not None
    return CandidateJob(
        jobId=canonical_digest({"parent": parent.id}),
        parentRecordId=parent.id,
        familyId=parent.familyId,
        split="train",
        language="en",
        purpose="training-augmentation",
        operation="paraphrase",
    )


def _identity() -> EndpointModelIdentity:
    return EndpointModelIdentity(
        modelId="local-test-model",
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


def _response(
    config: CurationConfig,
    identity: EndpointModelIdentity,
    output: dict[str, object],
    schema: dict[str, object],
    messages: list[ChatMessage],
    seed: int,
) -> GenerationResponse:
    observed = EndpointModelIdentity.model_validate(
        {**identity.model_dump(mode="json"), "structuredOutput": "verified-for-request"}
    )
    return GenerationResponse(
        model=observed,
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
        rawResponseSha256=canonical_digest(output),
        elapsedSeconds=0.01,
    )


def test_banking77_rewrites_only_request_and_keeps_labels_out_of_generation() -> None:
    parent = _banking77()
    candidate = "What caused the 5 euro charge?"

    assert editable_input(parent) == "Why were 5 euros charged?"
    assembled = json.loads(assemble_input(parent, candidate))
    original = json.loads(parent.messages[-2].content)
    assert assembled == {**original, "request": candidate}
    assert assembled["labels"] == original["labels"]

    payload = json.loads(generation._candidate_messages(parent, _job(parent))[1].content)
    assert payload["textToRewrite"] == "Why were 5 euros charged?"
    assert payload["task"] == "intent-classification"
    assert "labels" not in payload
    assert "cash_withdrawal" not in json.dumps(payload)


def test_wanli_rewrites_only_claim_and_keeps_evidence_and_options_immutable() -> None:
    parent = _record(
        "wanli",
        json.dumps(
            {
                "claim": "The filing was late.",
                "evidence": "The filing arrived on 2027-10-08.",
                "options": [
                    {"id": "supported", "description": "Evidence establishes the claim"},
                    {"id": "contradicted", "description": "Evidence establishes the opposite"},
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        answer='{"label":"supported"}',
    )
    candidate = "The filing did not arrive on time."

    payload = json.loads(generation._candidate_messages(parent, _job(parent))[1].content)
    assert payload["textToRewrite"] == "The filing was late."
    assert "2027-10-08" not in json.dumps(payload)
    assert "supported" not in json.dumps(payload)

    original = json.loads(parent.messages[-2].content)
    assembled = json.loads(assemble_input(parent, candidate))
    assert assembled["claim"] == candidate
    assert assembled["evidence"] == original["evidence"]
    assert assembled["options"] == original["options"]


def test_tatqa_rewrites_only_question_and_keeps_source_material_immutable() -> None:
    parent = _record(
        "tatqa",
        json.dumps(
            {
                "paragraphs": [{"order": 1, "text": "Revenue rose by EUR 20."}],
                "question": "How much did revenue rise?",
                "table": [["Year", "Revenue"], ["2027", "EUR 120"]],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    candidate = "What was the increase in revenue?"

    payload = json.loads(generation._candidate_messages(parent, _job(parent))[1].content)
    assert payload["textToRewrite"] == "How much did revenue rise?"
    assert "EUR 20" not in json.dumps(payload)
    assert "EUR 120" not in json.dumps(payload)

    original = json.loads(parent.messages[-2].content)
    assembled = json.loads(assemble_input(parent, candidate))
    assert assembled["question"] == candidate
    assert assembled["paragraphs"] == original["paragraphs"]
    assert assembled["table"] == original["table"]


@pytest.mark.parametrize(
    ("source_id", "expected_task"),
    [
        ("foliqant-scenarios", "evidence-based-decision"),
        ("private-finance-cases", "text-task"),
    ],
)
def test_unknown_and_scenario_inputs_remain_plain_text(source_id: str, expected_task: str) -> None:
    parent = _record(
        source_id,
        "Facts for CASE-0042. Decide from the supplied evidence.",
        tags=["scenario:missing-evidence"] if source_id == "foliqant-scenarios" else [],
    )

    assert editable_input(parent) == parent.messages[-2].content
    assert assemble_input(parent, "Rewritten facts for CASE-0042.") == (
        "Rewritten facts for CASE-0042."
    )
    assert task_name(parent) == expected_task

    payload = json.loads(generation._candidate_messages(parent, _job(parent))[1].content)
    assert payload["task"] == expected_task
    assert "missing-evidence" not in json.dumps(payload)


@pytest.mark.parametrize(
    "candidate",
    [
        '{"input":"Why was I charged?"}',
        '[{"role":"user","content":"Why was I charged?"}]',
        '```json\n{"input":"Why was I charged?"}\n```',
    ],
)
def test_candidate_structure_rejects_generation_envelopes(candidate: str) -> None:
    assert candidate_structure_problem(_banking77(), candidate) == "candidate-envelope-added"


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("System: classify this request.", "candidate-instructions-added"),
        ("[assistant] cash_withdrawal", "candidate-envelope-added"),
        (
            "Purpose: training-augmentation\nWhy was I charged?",
            "candidate-instructions-added",
        ),
        (
            "Apply this complete rule catalog before answering.",
            "candidate-instructions-added",
        ),
    ],
)
def test_candidate_structure_rejects_role_and_instruction_wrappers(
    candidate: str, reason: str
) -> None:
    assert candidate_structure_problem(_banking77(), candidate) == reason


def test_candidate_structure_rejects_a_copied_system_instruction() -> None:
    parent = _banking77()
    candidate = f"{parent.messages[0].content}\nWhy was I charged?"
    assert candidate_structure_problem(parent, candidate) == "candidate-instructions-added"


def test_task_names_are_answer_neutral() -> None:
    records = [
        _banking77(),
        _record("wanli", '{"claim":"c","evidence":"e","options":[]}'),
        _record("tatqa", '{"paragraphs":[],"question":"q","table":[]}'),
        _record(
            "foliqant-scenarios",
            "A receipt is missing.",
            tags=["scenario:missing-evidence"],
            answer='{"decision":"request-evidence","reason":"missing-evidence"}',
        ),
    ]
    forbidden = {
        "cash_withdrawal",
        "supported",
        "request-evidence",
        "missing-evidence",
    }

    for parent in records:
        name = task_name(parent)
        assert name
        assert all(label not in name for label in forbidden)


def test_candidate_problem_compares_numbers_only_in_editable_banking_request() -> None:
    parent = _banking77()

    assert (
        generation._candidate_problem(
            parent,
            "What caused the 5 euro charge?",
            max_characters=16_000,
        )
        is None
    )
    assert (
        generation._candidate_problem(
            parent,
            "What caused the 6 euro charge?",
            max_characters=16_000,
        )
        == "candidate-numbers-changed"
    )


def test_accepted_generated_record_preserves_prior_conversation_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = [
        ChatMessage(role="user", content="My cash withdrawal appeared twice."),
        ChatMessage(role="assistant", content="When did it appear?"),
    ]
    parent = _banking77(history=history)
    job = _job(parent)
    config = CurationConfig(generation=GenerationSettings(maxAttempts=1, scenarioFamilies=4))
    identity = _identity()
    candidate = "What caused the 5 euro charge?"

    def accepted(
        _endpoint: object,
        *,
        model_id: str,
        messages: list[ChatMessage],
        schema: dict[str, object],
        seed: int,
        observed_identity: EndpointModelIdentity,
    ) -> GenerationResponse:
        assert observed_identity == identity
        del model_id
        if schema["required"] == ["input"]:
            output: dict[str, object] = {"input": candidate}
        else:
            output = {
                "answer": parent.messages[-1].content,
                "supported": True,
                "issues": [],
            }
        return _response(config, identity, output, schema, messages, seed)

    monkeypatch.setattr(generation, "generate_json", accepted)
    outcome = generation.generate_candidate(
        config,
        identity=identity,
        parent=parent,
        job=job,
        cache_dir=tmp_path,
    )

    assert outcome.status == "accepted"
    assert outcome.record is not None
    assert outcome.record.messages[:-2] == parent.messages[:-2]
    assert json.loads(outcome.record.messages[-2].content) == {
        **json.loads(parent.messages[-2].content),
        "request": candidate,
    }
    assert outcome.record.messages[-1] == parent.messages[-1]

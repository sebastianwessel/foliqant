"""Regression tests for versioned curation prompt and checker contracts."""

from __future__ import annotations

import json

import pytest

from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import ChatMessage, DataRecord
from foliqant_model.curation import generation, task_input
from foliqant_model.curation.contracts import CandidateJob
from foliqant_model.curation.scenarios import scenario_recipe_digest


def _record(
    source_id: str,
    user: str,
    answer: str,
    *,
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
            ChatMessage(
                role="system",
                content="Use the supplied facts and return exactly the requested JSON.",
            ),
            *(history or []),
            ChatMessage(role="user", content=user),
            ChatMessage(role="assistant", content=answer),
        ],
        tags=tags or [],
        origin="synthetic",
        reviewed=False,
    )


def _job(parent: DataRecord, operation: str) -> CandidateJob:
    assert parent.familyId is not None
    return CandidateJob.model_validate(
        {
            "jobId": canonical_digest({"parent": parent.id, "operation": operation}),
            "parentRecordId": parent.id,
            "familyId": parent.familyId,
            "split": "test",
            "language": "en",
            "purpose": "synthetic-regression",
            "operation": operation,
        }
    )


def _scenario(*, history: list[ChatMessage] | None = None) -> DataRecord:
    return _record(
        "foliqant-scenarios",
        (
            'Authored facts: Later message: "No receipt is available for CASE-0042."\n'
            "Task: decide from the facts and return decision, reason, and evidence."
        ),
        (
            '{"decision":"request-evidence","evidence":'
            '["No receipt is available for CASE-0042."],"reason":"missing-evidence"}'
        ),
        history=history,
        tags=["authored-scenario", "scenario:missing-evidence", "synthetic-regression"],
    )


def test_prompt_versions_are_advanced_for_the_scoped_contract() -> None:
    assert generation._GENERATOR_PROMPT_VERSION == "candidate-scoped-input-v4"
    assert generation._CHECKER_PROMPT_VERSION == "candidate-independent-check-v6"


def test_generation_recipe_digest_changes_with_a_transformation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = generation.generation_recipe_digest()
    changed = dict(generation._TRANSFORMATIONS)
    changed["default"] += " Preserve the requested register."
    monkeypatch.setattr(generation, "_TRANSFORMATIONS", changed)

    assert generation.generation_recipe_digest() != before


def test_generation_recipe_digest_changes_with_task_input_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = generation.generation_recipe_digest()
    changed = dict(task_input._TASKS)
    changed["foliqant-scenarios"] = "changed-neutral-task"
    monkeypatch.setattr(task_input, "_TASKS", changed)

    assert task_input.task_input_recipe()["tasks"] == dict(sorted(changed.items()))
    assert generation.generation_recipe_digest() != before


def test_generation_recipe_binds_transformations_and_task_input_recipe() -> None:
    recipe = {
        "checker": {
            "promptVersion": generation._CHECKER_PROMPT_VERSION,
            "schema": generation._CHECKER_SCHEMA,
            "system": generation._CHECKER_SYSTEM,
            "task": generation._CHECKER_TASK,
            "answerFormats": generation._ANSWER_FORMATS,
        },
        "generator": {
            "promptVersion": generation._GENERATOR_PROMPT_VERSION,
            "schema": generation._GENERATOR_SCHEMA,
            "system": generation._GENERATOR_SYSTEM,
            "transformations": generation._TRANSFORMATIONS,
        },
        "taskInput": task_input.task_input_recipe(),
        "scenarios": scenario_recipe_digest(),
        "retryFeedback": generation._retry_feedback_recipe(),
        "requestFormat": generation.GENERATION_REQUEST_FORMAT,
    }
    assert generation.generation_recipe_digest() == canonical_digest(recipe)


@pytest.mark.parametrize(
    "operation",
    [
        "withdrawn-request",
        "multiple-intents",
        "missing-evidence",
        "conflicting-information",
        "changed-deadline",
    ],
)
def test_generator_envelope_omits_scenario_operation_and_reference_label(operation: str) -> None:
    parent = _scenario()
    messages = generation._candidate_messages(parent, _job(parent, operation))
    combined = "\n".join(message.content for message in messages)
    payload = json.loads(messages[1].content)

    assert payload["task"] == "evidence-based-decision"
    assert set(payload) == {"language", "task", "textToRewrite", "transformation"}
    assert payload["transformation"] == generation._TRANSFORMATIONS["default"]
    assert operation not in combined
    assert parent.messages[-1].content not in combined
    assert "request-evidence" not in combined


def test_checker_contract_reconstructs_ordered_prior_context_explicitly() -> None:
    history = [
        ChatMessage(role="user", content="The customer requested a refund."),
        ChatMessage(role="assistant", content="Was supporting evidence supplied?"),
    ]
    parent = _scenario(history=history)
    messages = generation._checker_messages(
        parent.messages[-2].content,
        parent,
        _job(parent, "missing-evidence"),
    )
    payload = json.loads(messages[1].content)

    assert payload["context"] == [message.model_dump(mode="json") for message in history]
    assert payload["candidateInput"] == parent.messages[-2].content
    assert payload["instructions"] == [parent.messages[0].content]
    assert "ordered prior messages" in messages[0].content
    assert "candidateInput as the final user message" in messages[0].content
    assert "ordered prior messages" in payload["task"]
    assert "answerFormat" in payload["task"]


def test_scenario_checker_requires_an_evidence_array() -> None:
    parent = _scenario()
    messages = generation._checker_messages(
        parent.messages[-2].content,
        parent,
        _job(parent, "missing-evidence"),
    )
    payload = json.loads(messages[1].content)

    assert payload["answerFormat"] == generation._ANSWER_FORMATS["foliqant-scenarios"]
    assert "evidence: array" in payload["answerFormat"]
    assert "Always use an array" in payload["answerFormat"]


def test_tatqa_checker_declares_observed_answer_types() -> None:
    parent = _record(
        "tatqa",
        '{"paragraphs":[],"question":"What was the increase?","table":[]}',
        (
            '{"answer":20,"answerFrom":"table","answerType":"arithmetic",'
            '"derivation":"120-100","scale":"million"}'
        ),
    )
    payload = json.loads(
        generation._checker_messages(
            "What increase was reported?",
            parent,
            _job(parent, "changed-deadline"),
        )[1].content
    )
    answer_format = payload["answerFormat"]

    assert "span or multi-span" in answer_format
    assert "answer is an array" in answer_format
    assert "arithmetic, answer is a JSON number" in answer_format
    assert "count, answer is a string" in answer_format
    assert "table, text or table-text" in answer_format

"""Source-aware guards for the one-field curation rewrite boundary."""

from __future__ import annotations

import json

from foliqant_model.contracts.inputs import DataRecord
from foliqant_model.curation.task_input import candidate_structure_problem, task_input_recipe


def _record(
    source: str,
    user: str,
    answer: str,
    *,
    earlier: list[dict[str, str]] | None = None,
) -> DataRecord:
    return DataRecord.model_validate(
        {
            "schemaVersion": 1,
            "id": f"{source}-parent",
            "sourceId": source,
            "language": "en",
            "groupKeys": [f"{source}-family"],
            "messages": [
                {"role": "system", "content": "Follow the source task contract."},
                *(earlier or []),
                {"role": "user", "content": user},
                {"role": "assistant", "content": answer},
            ],
            "tags": [],
            "origin": "human",
            "reviewed": False,
        }
    )


def test_task_input_recipe_versions_every_source_mapping() -> None:
    assert task_input_recipe() == {
        "editableFields": {
            "banking77": "request",
            "tatqa": "question",
            "wanli": "claim",
        },
        "guards": {
            "contextCopyMinCharacters": 24,
            "currencyPattern": (
                "(?<![A-Za-z])(?:AUD|BGN|BRL|CAD|CHF|CNY|CZK|DKK|EUR|GBP|HKD|HUF|INR|JPY|"
                "KRW|MXN|NOK|NZD|PLN|RON|SEK|SGD|TRY|USD|ZAR)(?![A-Za-z])|[€$£¥₹₩]"
            ),
            "rolePattern": (
                "(?im)^\\s*(?:\\[/?(?:system|user|assistant)\\]|(?:system|user|assistant)\\s*:)"
            ),
            "rulePattern": (
                "(?i)\\b(?:maps? to decision|apply this (?:complete )?rule|operation:|purpose:)"
            ),
        },
        "guardVersion": "scoped-task-input-guards-v2",
        "targetFields": {
            "banking77": ["intent"],
            "foliqant-scenarios": ["decision", "reason"],
            "wanli": ["label"],
        },
        "tasks": {
            "banking77": "intent-classification",
            "foliqant-scenarios": "evidence-based-decision",
            "tatqa": "financial-question-answering",
            "wanli": "evidence-assessment",
        },
    }


def test_rejects_pilot_wanli_task_that_copies_immutable_evidence_and_options() -> None:
    evidence = "Every silver badge in the invented trial was stored inside the locked blue cabinet."
    parent = _record(
        "wanli",
        json.dumps(
            {
                "claim": "The silver badges were stored in the blue cabinet.",
                "evidence": evidence,
                "options": [
                    {"description": "The evidence establishes the opposite", "id": "contradicted"},
                    {"description": "The evidence establishes the claim", "id": "supported"},
                    {
                        "description": "The evidence does not establish either way",
                        "id": "insufficient",
                    },
                ],
            },
            separators=(",", ":"),
        ),
        '{"label":"supported"}',
    )
    copied_task = (
        f'Based on the evidence "{evidence}", is the claim "The silver badges were stored in '
        'the blue cabinet." supported, contradicted, or does the evidence not establish either?'
    )

    assert candidate_structure_problem(parent, copied_task) == "candidate-context-copied"


def test_rejects_immutable_tatqa_paragraph_copied_into_question() -> None:
    paragraph = "Fiscal 2019 actuals are translated at the average foreign exchange rate."
    parent = _record(
        "tatqa",
        json.dumps(
            {
                "paragraphs": [{"order": 1, "text": paragraph}],
                "question": "Which exchange rate was used?",
                "table": [],
            },
            separators=(",", ":"),
        ),
        '{"answer":["1.2773"],"answerFrom":"text","answerType":"span","derivation":"","scale":""}',
    )

    assert (
        candidate_structure_problem(parent, f"{paragraph} Which exchange rate was used?")
        == "candidate-context-copied"
    )


def test_rejects_prior_conversation_context_copied_into_final_user_rewrite() -> None:
    prior = "The account holder already supplied the complete signed authorization form."
    parent = _record(
        "custom-source",
        "Could you confirm receipt?",
        "Yes.",
        earlier=[
            {"role": "user", "content": prior},
            {"role": "assistant", "content": "I will check the submitted documents."},
        ],
    )

    assert (
        candidate_structure_problem(parent, f"{prior} Could you confirm receipt?")
        == "candidate-context-copied"
    )


def test_rejects_new_banking_target_identifier_but_allows_preexisting_identifier() -> None:
    labels = ["cash_withdrawal_not_recognised", "cash_withdrawal_charge"]
    parent = _record(
        "banking77",
        json.dumps(
            {"labels": labels, "request": "I do not recognize this cash withdrawal."},
            separators=(",", ":"),
        ),
        '{"intent":"cash_withdrawal_not_recognised"}',
    )
    assert (
        candidate_structure_problem(parent, "This request is cash_withdrawal_not_recognised.")
        == "candidate-answer-cue-added"
    )

    preexisting = _record(
        "banking77",
        json.dumps(
            {
                "labels": labels,
                "request": "The app displays cash_withdrawal_not_recognised for this withdrawal.",
            },
            separators=(",", ":"),
        ),
        '{"intent":"cash_withdrawal_not_recognised"}',
    )
    assert (
        candidate_structure_problem(
            preexisting,
            "Why does the app show cash_withdrawal_not_recognised for this withdrawal?",
        )
        is None
    )


def test_wanli_requires_an_explicit_label_cue_and_preserves_natural_language() -> None:
    parent = _record(
        "wanli",
        json.dumps(
            {
                "claim": "The proposal has broad backing.",
                "evidence": "Every committee member signed the proposal after the final vote.",
                "options": [
                    {"description": "The evidence establishes the opposite", "id": "contradicted"},
                    {"description": "The evidence establishes the claim", "id": "supported"},
                    {
                        "description": "The evidence does not establish either way",
                        "id": "insufficient",
                    },
                ],
            },
            separators=(",", ":"),
        ),
        '{"label":"supported"}',
    )

    assert (
        candidate_structure_problem(parent, "The label is supported for this proposal.")
        == "candidate-answer-cue-added"
    )
    assert (
        candidate_structure_problem(parent, "The proposal is supported by a broad coalition.")
        is None
    )


def test_scenario_guard_rejects_hyphenated_answer_id_not_factual_withdrawal_word() -> None:
    parent = _record(
        "foliqant-scenarios",
        'Authored facts: "Please withdraw request CASE-0005 in full."',
        '{"decision":"no-action","evidence":["Please withdraw request CASE-0005 in full."],'
        '"reason":"request-withdrawn"}',
    )

    assert (
        candidate_structure_problem(
            parent,
            'Authored facts: "Please withdraw request CASE-0005 in full." '
            "Reason: request-withdrawn.",
        )
        == "candidate-answer-cue-added"
    )
    assert (
        candidate_structure_problem(
            parent,
            'Authored facts: "The request CASE-0005 was withdrawn in full."',
        )
        is None
    )


def test_rejects_changed_or_added_known_currency_in_editable_text() -> None:
    parent = _record(
        "foliqant-scenarios",
        "The transfer request is for EUR 250.",
        '{"decision":"manual-review","evidence":[],"reason":"conflicting-information"}',
    )

    assert (
        candidate_structure_problem(parent, "The transfer request is for USD 250.")
        == "candidate-currencies-changed"
    )
    assert (
        candidate_structure_problem(parent, "The EUR transfer request is for EUR 250.")
        == "candidate-currencies-changed"
    )
    assert candidate_structure_problem(parent, "A transfer of EUR 250 was requested.") is None

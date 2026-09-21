from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import foliqant.decisions as shared_decisions
from foliqant.decisions import (
    Answerability,
    Citation,
    DecisionInput,
    DecisionOutput,
    Explanation,
    semantic_signature,
    validate_decision_output,
)
from foliqant_model.contracts import base as model_base
from foliqant_model.curation.decision_contracts import DecisionDataSettings


def _task() -> DecisionInput:
    return DecisionInput.model_validate(
        {
            "schemaVersion": 2,
            "state": {
                "sources": [{"id": "message-1", "kind": "message", "text": "Send my statement."}]
            },
            "questions": [
                {
                    "id": "route",
                    "type": "choice",
                    "prompt": "Choose one route.",
                    "criteria": ["Use the explicit request."],
                    "allowedSourceIds": ["message-1"],
                    "options": [
                        {"id": "refund", "description": "Refund a fee"},
                        {"id": "statement", "description": "Send a statement"},
                    ],
                }
            ],
        },
        strict=True,
    )


def _output(*, quote: str = "Send my statement.") -> DecisionOutput:
    return DecisionOutput.model_validate(
        {
            "schemaVersion": 2,
            "results": [
                {
                    "questionId": "route",
                    "type": "choice",
                    "answerability": {"status": "answerable", "issues": []},
                    "answer": {"optionId": "statement"},
                    "explanation": {
                        "summary": "The statement request is explicit.",
                        "evidence": [{"sourceId": "message-1", "quote": quote}],
                        "contraryEvidence": [],
                        "missingFacts": [],
                    },
                }
            ],
        },
        strict=True,
    )


def test_model_reexports_shared_primitives_without_a_second_contract_copy() -> None:
    assert model_base.ContractModel is shared_decisions.ContractModel
    assert model_base.Id is shared_decisions.Id
    assert model_base.NonEmptyStr is shared_decisions.NonEmptyStr
    assert model_base.SchemaVersion is shared_decisions.SchemaVersion
    assert not hasattr(shared_decisions, "DecisionDataSettings")


def test_settings_are_strict_and_bounded() -> None:
    assert DecisionDataSettings().examplesPerScenario == 4
    with pytest.raises(ValidationError):
        DecisionDataSettings(examplesPerScenario=3)
    with pytest.raises(ValidationError):
        DecisionDataSettings.model_validate({"familiesPerScenario": 20}, strict=True)
    with pytest.raises(ValidationError):
        DecisionDataSettings(sourceExamplesPerSource=True)
    with pytest.raises(ValidationError):
        DecisionDataSettings(minimumAcceptedPerCell=1001)


def test_answerability_issues_are_the_three_generic_input_failures() -> None:
    expected = {
        "no_supported_answer",
        "conflicting_information",
        "multiple_valid_options",
    }
    for issue in expected:
        assert Answerability(status="not_answerable", issues=[issue]).issues == [issue]
    with pytest.raises(ValidationError):
        Answerability.model_validate(
            {"status": "not_answerable", "issues": ["invalid_question"]}, strict=True
        )


def test_explanation_summary_has_its_own_400_character_boundary() -> None:
    long_detail = "d" * 401
    explanation = Explanation(
        summary="s" * 400,
        evidence=[Citation(sourceId="source", quote=long_detail)],
        contraryEvidence=[],
        missingFacts=[long_detail],
    )

    assert len(explanation.summary) == 400
    assert explanation.evidence[0].quote == long_detail
    assert explanation.missingFacts == [long_detail]
    with pytest.raises(ValidationError):
        Explanation(
            summary="s" * 401,
            evidence=explanation.evidence,
            contraryEvidence=[],
            missingFacts=explanation.missingFacts,
        )


def test_contracts_are_closed_and_references_are_validated() -> None:
    payload = _task().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        DecisionInput.model_validate(payload, strict=True)

    payload = _task().model_dump(mode="json")
    payload["questions"][0]["allowedSourceIds"] = ["missing"]
    with pytest.raises(ValidationError):
        DecisionInput.model_validate(payload, strict=True)


def test_semantic_validation_checks_result_bijection_options_and_citations() -> None:
    assert validate_decision_output(_task(), _output()) == []
    bad = _output(quote="invented quote").model_dump(mode="json")
    bad["results"][0]["answer"]["optionId"] = "invented"
    result = DecisionOutput.model_validate(bad, strict=True)
    assert validate_decision_output(_task(), result) == [
        "question:route:explanation:citation:0:quote-not-found",
        "question:route:unknown-option",
    ]

    empty = DecisionOutput.model_construct(schemaVersion=1, results=[])
    assert validate_decision_output(_task(), empty) == ["question:route:missing-result"]


def test_predicate_unknown_is_distinct_from_false() -> None:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 2,
            "state": {"sources": [{"id": "s", "kind": "document", "text": "No count."}]},
            "questions": [
                {
                    "id": "p",
                    "type": "predicate",
                    "prompt": "Was the fee charged twice?",
                    "criteria": ["True and false both require explicit evidence."],
                    "allowedSourceIds": ["s"],
                }
            ],
        },
        strict=True,
    )
    payload = {
        "schemaVersion": 2,
        "results": [
            {
                "questionId": "p",
                "type": "predicate",
                "answerability": {
                    "status": "not_answerable",
                    "issues": ["no_supported_answer"],
                },
                "answer": {"value": "unknown"},
                "explanation": {
                    "summary": "The count is absent.",
                    "evidence": [],
                    "contraryEvidence": [],
                    "missingFacts": ["Charge count."],
                },
            }
        ],
    }
    result = DecisionOutput.model_validate(payload, strict=True)
    assert validate_decision_output(task, result) == []
    payload["results"][0]["answer"]["value"] = "false"
    false_result = DecisionOutput.model_validate(payload, strict=True)
    assert validate_decision_output(task, false_result) == [
        "question:p:substantive-on-unanswerable"
    ]


def test_request_units_allow_same_category_and_validate_relations() -> None:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 2,
            "state": {
                "sources": [{"id": "m", "kind": "message", "text": "Send January and March."}]
            },
            "questions": [
                {
                    "id": "requests",
                    "type": "request_units",
                    "prompt": "Find every request.",
                    "criteria": ["Keep distinct requested items."],
                    "allowedSourceIds": ["m"],
                    "catalog": [{"id": "statement", "description": "Send a statement"}],
                    "allowNoMatch": False,
                }
            ],
        },
        strict=True,
    )
    citation = {"sourceId": "m", "quote": "Send January and March."}
    payload = {
        "schemaVersion": 2,
        "results": [
            {
                "questionId": "requests",
                "type": "request_units",
                "answerability": {
                    "status": "answerable",
                    "issues": ["no_supported_answer"],
                },
                "answer": {
                    "units": [
                        {
                            "id": "r1",
                            "status": "active",
                            "categoryId": "statement",
                            "subject": "January",
                            "description": "January statement",
                            "evidence": [citation],
                        },
                        {
                            "id": "r2",
                            "status": "active",
                            "categoryId": "statement",
                            "subject": "March",
                            "description": "March statement",
                            "evidence": [citation],
                        },
                    ],
                    "relations": [
                        {
                            "type": "requires",
                            "requestId": "r2",
                            "requiredRequestId": "r1",
                        },
                        {
                            "type": "precedes",
                            "beforeRequestId": "r2",
                            "afterRequestId": "r1",
                        },
                    ],
                },
                "explanation": {
                    "summary": "Two statement items are requested.",
                    "evidence": [citation],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            }
        ],
    }
    result = DecisionOutput.model_validate(payload, strict=True)
    assert validate_decision_output(task, result) == ["question:requests:relation-cycle"]
    payload["results"][0]["answer"]["relations"] = []
    first = DecisionOutput.model_validate(payload, strict=True)
    assert validate_decision_output(task, first) == []
    changed = first.model_copy(deep=True)
    assert changed.results[0].type == "request_units"
    assert changed.results[0].answer is not None
    changed.results[0].answer.units[0].description = "Paraphrased January item"
    assert semantic_signature(first) == semantic_signature(changed)
    changed.results[0].answer.units[1].id = "r3"
    assert semantic_signature(first) != semantic_signature(changed)


def test_request_relations_reject_conflicts_and_canonicalize_symmetric_sets() -> None:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 2,
            "state": {"sources": [{"id": "m", "kind": "message", "text": "Do A or B."}]},
            "questions": [
                {
                    "id": "condition",
                    "type": "predicate",
                    "prompt": "Is the condition true?",
                    "criteria": ["Use the explicit condition."],
                    "allowedSourceIds": ["m"],
                },
                {
                    "id": "requests",
                    "type": "request_units",
                    "prompt": "Find every request.",
                    "criteria": ["Keep alternatives distinct."],
                    "allowedSourceIds": ["m"],
                    "catalog": [],
                    "allowNoMatch": True,
                },
            ],
        },
        strict=True,
    )
    citation = {"sourceId": "m", "quote": "Do A or B."}
    base = {
        "schemaVersion": 2,
        "results": [
            {
                "questionId": "condition",
                "type": "predicate",
                "answerability": {"status": "not_answerable", "issues": ["no_supported_answer"]},
                "answer": {"value": "unknown"},
                "explanation": {
                    "summary": "The condition is not supplied.",
                    "evidence": [],
                    "contraryEvidence": [],
                    "missingFacts": ["The condition."],
                },
            },
            {
                "questionId": "requests",
                "type": "request_units",
                "answerability": {
                    "status": "answerable",
                    "issues": ["no_supported_answer"],
                },
                "answer": {
                    "units": [
                        {
                            "id": "r1",
                            "status": "conditional",
                            "categoryId": None,
                            "subject": "A",
                            "description": "Do A",
                            "evidence": [citation],
                        },
                        {
                            "id": "r2",
                            "status": "conditional",
                            "categoryId": None,
                            "subject": "B",
                            "description": "Do B",
                            "evidence": [citation],
                        },
                    ],
                    "relations": [
                        {"type": "mutually_exclusive", "requestIds": ["r2", "r1"]},
                        {
                            "type": "conditional_on",
                            "requestId": "r1",
                            "predicateQuestionId": "condition",
                            "requiredValue": "true",
                        },
                    ],
                },
                "explanation": {
                    "summary": "The requests are alternatives.",
                    "evidence": [citation],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            },
        ],
    }
    first = DecisionOutput.model_validate(base, strict=True)
    assert validate_decision_output(task, first) == []
    reordered_payload = first.model_dump(mode="json")
    reordered_payload["results"][1]["answer"]["relations"][0]["requestIds"] = ["r1", "r2"]
    reordered = DecisionOutput.model_validate(reordered_payload, strict=True)
    assert semantic_signature(first) == semantic_signature(reordered)

    duplicate_payload = first.model_dump(mode="json")
    duplicate_payload["results"][1]["answer"]["relations"].append(
        {"type": "mutually_exclusive", "requestIds": ["r1", "r2"]}
    )
    duplicate = DecisionOutput.model_validate(duplicate_payload, strict=True)
    assert "question:requests:duplicate-relation" in validate_decision_output(task, duplicate)

    conflicting_payload = first.model_dump(mode="json")
    conflicting_payload["results"][1]["answer"]["relations"].append(
        {
            "type": "conditional_on",
            "requestId": "r1",
            "predicateQuestionId": "condition",
            "requiredValue": "false",
        }
    )
    conflicting = DecisionOutput.model_validate(conflicting_payload, strict=True)
    assert "question:requests:relation-conflicting-condition" in validate_decision_output(
        task, conflicting
    )

    impossible_payload = first.model_dump(mode="json")
    impossible_payload["results"][1]["answer"]["relations"].append(
        {"type": "requires", "requestId": "r1", "requiredRequestId": "r2"}
    )
    impossible = DecisionOutput.model_validate(impossible_payload, strict=True)
    assert "question:requests:relation-exclusive-dependency" in validate_decision_output(
        task, impossible
    )

    transitive_payload = first.model_dump(mode="json")
    transitive_payload["results"][1]["answer"]["units"].append(
        {
            "id": "r3",
            "status": "conditional",
            "categoryId": None,
            "subject": None,
            "description": "Do C",
            "evidence": [citation],
        }
    )
    transitive_payload["results"][1]["answer"]["relations"] = [
        {"type": "mutually_exclusive", "requestIds": ["r1", "r3"]},
        {"type": "requires", "requestId": "r2", "requiredRequestId": "r1"},
        {"type": "requires", "requestId": "r3", "requiredRequestId": "r2"},
    ]
    transitive = DecisionOutput.model_validate(transitive_payload, strict=True)
    assert "question:requests:relation-exclusive-dependency" in validate_decision_output(
        task, transitive
    )


def test_request_subject_is_exact_and_must_use_its_own_evidence() -> None:
    task = DecisionInput.model_validate(
        {
            "schemaVersion": 2,
            "state": {
                "sources": [{"id": "m", "kind": "message", "text": "Refund the EUR 152 charge."}]
            },
            "questions": [
                {
                    "id": "requests",
                    "type": "request_units",
                    "prompt": "Find every request.",
                    "criteria": ["Copy the quoted exact subject EUR 152."],
                    "allowedSourceIds": ["m"],
                    "catalog": [],
                    "allowNoMatch": True,
                }
            ],
        },
        strict=True,
    )
    payload = {
        "schemaVersion": 2,
        "results": [
            {
                "questionId": "requests",
                "type": "request_units",
                "answerability": {
                    "status": "answerable",
                    "issues": ["no_supported_answer"],
                },
                "answer": {
                    "units": [
                        {
                            "id": "r1",
                            "status": "active",
                            "categoryId": None,
                            "subject": "eur 152",
                            "description": "Refund the charge",
                            "evidence": [{"sourceId": "m", "quote": "Refund the EUR 152 charge."}],
                        }
                    ],
                    "relations": [],
                },
                "explanation": {
                    "summary": "The refund is explicit.",
                    "evidence": [{"sourceId": "m", "quote": "Refund the EUR 152 charge."}],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            }
        ],
    }
    result = DecisionOutput.model_validate(payload, strict=True)
    assert validate_decision_output(task, result) == [
        "question:requests:request:r1:subject-not-in-evidence"
    ]

    missing_issue_payload = result.model_dump(mode="json")
    missing_issue_payload["results"][0]["answerability"]["issues"] = []
    missing_issue_payload["results"][0]["answer"]["units"][0]["subject"] = "EUR 152"
    missing_issue = DecisionOutput.model_validate(missing_issue_payload, strict=True)
    assert validate_decision_output(task, missing_issue) == [
        "question:requests:null-category-without-no-supported-answer-issue"
    ]


def test_semantic_signature_ignores_explanation_but_retains_answerability_and_answer() -> None:
    first = _output()
    changed = first.model_copy(deep=True)
    changed.results[0].explanation.summary = "Different free explanation."
    changed.results[0].explanation.evidence[0].quote = "statement"
    assert semantic_signature(first) == semantic_signature(changed)

    payload = changed.model_dump(mode="json")
    payload["results"][0]["answer"]["optionId"] = "refund"
    different = DecisionOutput.model_validate(payload, strict=True)
    assert semantic_signature(first) != semantic_signature(different)


def test_semantic_signature_treats_multiselect_as_an_unordered_set() -> None:
    payload = {
        "schemaVersion": 2,
        "results": [
            {
                "questionId": "categories",
                "type": "multiselect",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"optionIds": ["statement", "refund"]},
                "explanation": {
                    "summary": "Both apply.",
                    "evidence": [],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            }
        ],
    }
    first = DecisionOutput.model_validate(payload, strict=True)
    payload["results"][0]["answer"]["optionIds"] = ["refund", "statement"]
    reordered = DecisionOutput.model_validate(payload, strict=True)
    assert semantic_signature(first) == semantic_signature(reordered)


def test_output_contract_contains_no_confidence_or_probability_field() -> None:
    schema = json.dumps(DecisionOutput.model_json_schema(), sort_keys=True).casefold()
    assert "confidence" not in schema
    assert "probability" not in schema

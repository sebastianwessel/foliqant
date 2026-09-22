"""Runtime support assessments preserve typed answers and evidence semantics."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

from foliqant.contracts.decisions import (
    ChoiceResult,
    DecisionOutput,
    MultiselectResult,
    OrdinalResult,
    PredicateResult,
    RequestUnitsResult,
    validate_decision_output,
)
from foliqant.decisions import DecisionInput


def _task():
    options = [{"id": "a", "description": "First"}, {"id": "b", "description": "Second"}]
    questions = []
    for kind in ("choice", "multiselect", "predicate", "ordinal", "request_units"):
        question = {
            "id": kind,
            "type": kind,
            "prompt": "Apply the supplied criteria.",
            "criteria": ["Use the stated requests."],
            "allowedSourceIds": ["message"],
        }
        if kind in {"choice", "multiselect"}:
            question["options"] = options
        if kind == "multiselect":
            question.update(minSelections=0, maxSelections=2)
        if kind == "ordinal":
            question["levels"] = options
        if kind == "request_units":
            question.update(catalog=options, allowNoMatch=True)
        questions.append(question)
    return DecisionInput.model_validate(
        {
            "state": {
                "sources": [
                    {"id": "message", "kind": "message", "text": "Send January and March."},
                    {"id": "forbidden", "kind": "document", "text": "December"},
                ]
            },
            "questions": questions,
        }
    )


def _output():
    answers = {
        "choice": {"optionId": "a"},
        "multiselect": {"optionIds": ["a", "b"]},
        "predicate": {"value": "false"},
        "ordinal": {"levelId": "b"},
        "request_units": {
            "units": [
                {
                    "id": key,
                    "status": "active",
                    "categoryId": "a",
                    "subject": subject,
                    "description": f"Send {subject}.",
                }
                for key, subject in (("r1", "January"), ("r2", "March"))
            ],
            "relations": [],
        },
    }
    return {
        "results": [
            {
                "questionId": kind,
                "type": kind,
                "answerability": {"status": "answerable", "issues": []},
                "answer": answer,
                "reason": "The supplied information supports this interpretation.",
                "evidence_strength": "strong",
            }
            for kind, answer in answers.items()
        ],
    }


@pytest.mark.parametrize("strength", [None, "limited", "strong"])
def test_every_task_accepts_support_without_turning_it_into_a_routing_threshold(strength):
    raw = _output()
    for result in raw["results"]:
        result["evidence_strength"] = strength
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == []


@pytest.fixture(scope="module")
def predicate_schemas():
    schema = DecisionOutput.model_json_schema()
    strict_schema = OpenAIJsonSchemaTransformer(deepcopy(schema), strict=True).walk()
    for candidate in (schema, strict_schema):
        Draft202012Validator.check_schema(candidate)
    return (Draft202012Validator(schema), Draft202012Validator(strict_schema))


@pytest.mark.parametrize("value", ["true", "false", "unknown"])
@pytest.mark.parametrize(
    "status", ["answerable", "partially_answerable", "not_answerable", "undetermined"]
)
@pytest.mark.parametrize("strength", [None, "limited", "strong"])
@pytest.mark.parametrize("issues", [[], ["no_supported_answer"]])
def test_predicate_schema_and_strict_provider_conversion_preserve_answerability_rules(
    predicate_schemas, value, status, strength, issues
):
    result = _output()["results"][2]
    result["answer"] = {"value": value}
    result["answerability"] = {"status": status, "issues": issues}
    result["evidence_strength"] = strength
    raw = {"results": [result]}
    # Use the native semantic authority rather than maintaining a second truth table.
    from foliqant.decisions.contracts import Answerability, validate_answerability

    expected = not validate_answerability(
        prefix="predicate",
        task_type="predicate",
        answerability=Answerability.model_validate(result["answerability"]),
        has_answer=True,
        predicate_value=value,
    )
    for schema in predicate_schemas:
        assert schema.is_valid(raw) is expected
    if expected:
        assert PredicateResult.model_validate(result).evidence_strength == strength
        assert DecisionOutput.model_validate(raw).results[0].answer.value == value
    else:
        with pytest.raises(ValidationError):
            PredicateResult.model_validate(result)
        with pytest.raises(ValidationError):
            DecisionOutput.model_validate(raw)


@pytest.mark.parametrize("status", ["not_answerable", "undetermined"])
@pytest.mark.parametrize("strength", [None, "limited", "strong"])
def test_whole_assessment_strength_is_independent_of_abstention(status, strength):
    raw = _output()
    for result in raw["results"]:
        result["answerability"] = {"status": status, "issues": ["no_supported_answer"]}
        result["answer"] = {"value": "unknown"} if result["type"] == "predicate" else None
        result["evidence_strength"] = strength
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == []


@pytest.mark.parametrize("strength", [None, "limited", "strong"])
def test_false_and_empty_collections_allow_an_independent_assessment(strength):
    raw = _output()
    raw["results"][1]["answer"]["optionIds"] = []
    raw["results"][4]["answer"]["units"] = []
    for result in raw["results"]:
        result["evidence_strength"] = strength
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == []


@pytest.mark.parametrize(
    "model,index",
    [
        (ChoiceResult, 0),
        (MultiselectResult, 1),
        (PredicateResult, 2),
        (OrdinalResult, 3),
        (RequestUnitsResult, 4),
    ],
)
def test_standalone_results_reject_mutated_or_constructed_answerability_inconsistency(model, index):
    raw = _output()["results"][index]
    valid = model.model_validate(raw)
    valid.answerability.status = "not_answerable"
    with pytest.raises(ValidationError):
        model.model_validate(valid)
    raw["answerability"]["status"] = "not_answerable"
    with pytest.raises(ValidationError):
        model.model_validate(raw)
    with pytest.raises(ValidationError):
        model.model_validate(model.model_construct(reason="Incomplete unvalidated value"))


def test_partial_collections_can_have_strong_support_without_claiming_completeness():
    raw = _output()
    for index in (1, 4):
        raw["results"][index]["answerability"] = {
            "status": "partially_answerable",
            "issues": ["no_supported_answer"],
        }
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == []
    raw["results"][1]["answer"]["optionIds"] = []
    raw["results"][4]["answer"]["units"] = []
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == [
        "question:multiselect:partial-answer-empty",
        "question:request_units:partial-answer-empty",
    ]


@pytest.mark.parametrize("reason", ["", "   ", "\n\t", "x" * 401])
def test_reason_must_be_nonblank_and_bounded(reason):
    raw = _output()
    raw["results"][0]["reason"] = reason
    with pytest.raises(ValidationError):
        DecisionOutput.model_validate(raw)


@pytest.mark.parametrize(
    "mutation", ["unknown_field", "explanation", "unit_evidence", "missing_strength", "none"]
)
def test_runtime_rejects_unknown_or_unspecified_assessment_shapes(mutation):
    raw = _output()
    if mutation == "unknown_field":
        raw["unexpected"] = True
    elif mutation == "explanation":
        raw["results"][0]["explanation"] = {"summary": "Unexpected"}
    elif mutation == "unit_evidence":
        raw["results"][4]["answer"]["units"][0]["evidence"] = []
    elif mutation == "missing_strength":
        del raw["results"][0]["evidence_strength"]
    else:
        raw["results"][0]["evidence_strength"] = "none"
    with pytest.raises(ValidationError):
        DecisionOutput.model_validate(raw)


@pytest.mark.parametrize(
    "index,key,value,problem",
    [
        (0, "optionId", "unknown", "question:choice:unknown-option"),
        (1, "optionIds", ["unknown"], "question:multiselect:unknown-option"),
        (3, "levelId", "unknown", "question:ordinal:unknown-level"),
    ],
)
def test_unknown_answer_identifiers_are_rejected(index, key, value, problem):
    raw = _output()
    raw["results"][index]["answer"][key] = value
    assert problem in validate_decision_output(_task(), DecisionOutput.model_validate(raw))


def test_authored_cardinality_still_applies():
    task = _task()
    task.questions[1].maxSelections = 1
    assert "question:multiselect:selection-cardinality" in validate_decision_output(
        task, DecisionOutput.model_validate(_output())
    )


@pytest.mark.parametrize("subject", ["December", "Invented", "january"])
def test_request_subject_must_occur_exactly_in_an_allowed_source(subject):
    raw = _output()
    raw["results"][4]["answer"]["units"][0]["subject"] = subject
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == [
        "question:request_units:request:r1:subject-not-in-source"
    ]


@pytest.mark.parametrize(
    "relations,problem",
    [
        (
            [{"type": "requires", "requestId": "r1", "requiredRequestId": "missing"}],
            "relation-unknown-request",
        ),
        (
            [{"type": "requires", "requestId": "r1", "requiredRequestId": "r1"}],
            "relation-self-reference",
        ),
        (
            [
                {
                    "type": "conditional_on",
                    "requestId": "r1",
                    "predicateQuestionId": "choice",
                    "requiredValue": "true",
                }
            ],
            "relation-unknown-predicate",
        ),
        (
            [
                {"type": "requires", "requestId": "r1", "requiredRequestId": "r2"},
                {"type": "precedes", "beforeRequestId": "r1", "afterRequestId": "r2"},
            ],
            "relation-cycle",
        ),
        (
            [
                {"type": "requires", "requestId": "r1", "requiredRequestId": "r2"},
                {"type": "mutually_exclusive", "requestIds": ["r1", "r2"]},
            ],
            "relation-exclusive-dependency",
        ),
        (
            [{"type": "precedes", "beforeRequestId": "r1", "afterRequestId": "r2"}] * 2,
            "duplicate-relation",
        ),
        (
            [
                {
                    "type": "conditional_on",
                    "requestId": "r1",
                    "predicateQuestionId": "predicate",
                    "requiredValue": value,
                }
                for value in ("true", "false")
            ],
            "relation-conflicting-condition",
        ),
    ],
)
def test_request_relations_keep_shared_structural_checks(relations, problem):
    raw = _output()
    raw["results"][4]["answer"]["relations"] = relations
    assert f"question:request_units:{problem}" in validate_decision_output(
        _task(), DecisionOutput.model_validate(raw)
    )


def test_valid_conditional_relation_and_repeated_category_units_remain_supported():
    raw = _output()
    raw["results"][4]["answer"]["relations"] = [
        {
            "type": "conditional_on",
            "requestId": "r1",
            "predicateQuestionId": "predicate",
            "requiredValue": "false",
        }
    ]
    assert validate_decision_output(_task(), DecisionOutput.model_validate(raw)) == []

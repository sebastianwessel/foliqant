"""Native decision adapter preserves contracts while exposing safe route facts."""

import warnings
from collections.abc import Mapping
from typing import Literal, cast

import pytest

from foliqant.adapters.decisions import (
    build_decision_input,
    validate_decision_result,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import (
    BindingPlan,
    DecisionOptionPlan,
    DecisionQuestionPlan,
    DecisionStepPlan,
    SourceLocation,
)
from foliqant.decisions import DecisionOutput


def _step(
    *questions: DecisionQuestionPlan, mode: Literal["single", "multiple"] = "single"
) -> DecisionStepPlan:
    return DecisionStepPlan(
        name="classify",
        type="decision",
        location=SourceLocation("steps/classify.yaml", 1, 1),
        model="local",
        sources=(("ticket", BindingPlan(kind="pointer", pointer="/payload/ticket")),),
        questions=questions,
        question_mode=mode,
        instructions="Classify the ticket.",
    )


def _choice(question_id: str = "classify") -> DecisionQuestionPlan:
    return DecisionQuestionPlan(
        id=question_id,
        type="choice",
        prompt="Which queue applies?",
        criteria=("Use only the ticket.",),
        allowed_source_ids=("ticket",),
        options=(
            DecisionOptionPlan("billing.queue-v2", "Billing support"),
            DecisionOptionPlan("technical", "Technical support"),
        ),
    )


def _predicate(question_id: str) -> DecisionQuestionPlan:
    return DecisionQuestionPlan(
        id=question_id,
        type="predicate",
        prompt="Is access blocked?",
        criteria=("Use only explicit statements.",),
        allowed_source_ids=("ticket",),
    )


def _ordinal(question_id: str = "classify") -> DecisionQuestionPlan:
    return DecisionQuestionPlan(
        id=question_id,
        type="ordinal",
        prompt="How urgent is the ticket?",
        criteria=("Use the stated urgency.",),
        allowed_source_ids=("ticket",),
        options=(
            DecisionOptionPlan("normal", "Normal urgency"),
            DecisionOptionPlan("High.Priority-v2", "High urgency"),
        ),
    )


def _multiselect(question_id: str) -> DecisionQuestionPlan:
    return DecisionQuestionPlan(
        id=question_id,
        type="multiselect",
        prompt="Which queues apply?",
        criteria=("Select every queue established by the ticket.",),
        allowed_source_ids=("ticket",),
        options=(
            DecisionOptionPlan("billing", "Billing support"),
            DecisionOptionPlan("technical", "Technical support"),
        ),
        min_selections=1,
        max_selections=2,
    )


def _sources(text: str = "Billing failed for invoice 17.") -> FrozenObject:
    return cast(FrozenObject, freeze_json({"ticket": text}))


def _explanation(quote: str = "Billing failed") -> dict[str, object]:
    return {
        "summary": "The ticket explicitly describes a billing failure.",
        "evidence": [{"sourceId": "ticket", "quote": quote}],
        "contraryEvidence": [],
        "missingFacts": [],
    }


def _choice_result(
    *,
    question_id: str = "classify",
    status: str = "answerable",
    option_id: str | None = "billing.queue-v2",
) -> dict[str, object]:
    return {
        "questionId": question_id,
        "type": "choice",
        "answerability": {
            "status": status,
            "issues": [] if status == "answerable" else ["missing_information"],
        },
        "answer": None if option_id is None else {"optionId": option_id},
        "explanation": _explanation()
        if status == "answerable"
        else {
            "summary": "The available text does not establish a queue.",
            "evidence": [],
            "contraryEvidence": [],
            "missingFacts": ["The affected service is missing."],
        },
    }


def test_builds_canonical_input_from_actual_nonblank_sources() -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())

    assert task.model_dump(mode="json") == {
        "schemaVersion": 1,
        "state": {
            "sources": [
                {"id": "ticket", "kind": "document", "text": "Billing failed for invoice 17."}
            ]
        },
        "questions": [
            {
                "id": "classify",
                "type": "choice",
                "prompt": "Which queue applies?",
                "criteria": ["Use only the ticket."],
                "allowedSourceIds": ["ticket"],
                "options": [
                    {"id": "billing.queue-v2", "description": "Billing support"},
                    {"id": "technical", "description": "Technical support"},
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "sources",
    [
        {},
        {"ticket": "value", "extra": "unbound"},
        {"ticket": None},
        {"ticket": ""},
        {"ticket": "  \n"},
    ],
)
def test_rejects_missing_extra_nonstring_and_blank_sources(
    sources: dict[str, object],
) -> None:
    with pytest.raises(ServiceError) as error:
        build_decision_input(_step(_choice()), cast(FrozenObject, freeze_json(sources)))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert str(error.value) == "The input does not satisfy the required contract."


def test_single_choice_is_unwrapped_immutable_and_keeps_exact_route_key() -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())
    result = validate_decision_result(
        step,
        task,
        {"schemaVersion": 1, "results": [_choice_result()]},
    )

    assert result.answerable is True
    assert result.route_key == "billing.queue-v2"
    assert thaw_json(result.value) == _choice_result()
    assert isinstance(result.value, Mapping)
    with pytest.raises(TypeError):
        result.value["answer"] = None  # type: ignore[index]


def test_predicate_routes_only_validated_true_or_false() -> None:
    question = _predicate("classify")
    step = _step(question)
    task = build_decision_input(step, _sources("Access is not blocked."))
    raw = {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": "classify",
                "type": "predicate",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"value": "false"},
                "explanation": {
                    **_explanation("Access is not blocked"),
                    "summary": "The ticket says access is not blocked.",
                },
            }
        ],
    }
    result = validate_decision_result(step, task, raw)
    assert result.answerable is True
    assert result.route_key == "false"


def test_ordinal_route_preserves_the_validated_level_id() -> None:
    step = _step(_ordinal())
    task = build_decision_input(step, _sources("This invoice issue is high priority."))
    raw = {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": "classify",
                "type": "ordinal",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"levelId": "High.Priority-v2"},
                "explanation": {
                    **_explanation("high priority"),
                    "summary": "The ticket explicitly states high priority.",
                },
            }
        ],
    }
    result = validate_decision_result(step, task, raw)
    assert result.answerable is True
    assert result.route_key == "High.Priority-v2"


def test_any_uncertainty_disables_routing_and_multi_question_retains_wrapper() -> None:
    step = _step(_choice("queue"), _predicate("blocked"), mode="multiple")
    task = build_decision_input(step, _sources())
    raw = {
        "schemaVersion": 1,
        "results": [
            _choice_result(question_id="queue", status="undetermined", option_id=None),
            {
                "questionId": "blocked",
                "type": "predicate",
                "answerability": {
                    "status": "undetermined",
                    "issues": ["missing_information"],
                },
                "answer": {"value": "unknown"},
                "explanation": {
                    "summary": "The ticket does not discuss access.",
                    "evidence": [],
                    "contraryEvidence": [],
                    "missingFacts": ["Access status is missing."],
                },
            },
        ],
    }
    result = validate_decision_result(step, task, raw)
    assert result.answerable is False
    assert result.route_key is None
    assert thaw_json(result.value) == raw


def test_answerable_multiselect_remains_inside_multi_question_wrapper() -> None:
    step = _step(_multiselect("queues"), _predicate("blocked"), mode="multiple")
    task = build_decision_input(
        step,
        _sources("Billing failed and technical access is blocked."),
    )
    raw = {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": "queues",
                "type": "multiselect",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"optionIds": ["billing", "technical"]},
                "explanation": {
                    **_explanation("Billing failed and technical"),
                    "summary": "The ticket names billing and technical problems.",
                },
            },
            {
                "questionId": "blocked",
                "type": "predicate",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"value": "true"},
                "explanation": {
                    **_explanation("access is blocked"),
                    "summary": "The ticket explicitly says access is blocked.",
                },
            },
        ],
    }
    result = validate_decision_result(step, task, raw)
    assert result.answerable is True
    assert result.route_key is None
    assert thaw_json(result.value) == raw


def test_existing_decision_output_is_reparsed_before_acceptance() -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())
    output = DecisionOutput.model_validate(
        {"schemaVersion": 1, "results": [_choice_result()]}, strict=True
    )

    result = validate_decision_result(step, task, output)
    assert result.answerable is True
    assert result.route_key == "billing.queue-v2"


@pytest.mark.parametrize("constructed", [False, True])
def test_mutated_or_constructed_model_cannot_bypass_validation_without_warning(
    constructed: bool,
) -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())
    if constructed:
        output = DecisionOutput.model_construct(schemaVersion=1, results=[])
    else:
        output = DecisionOutput.model_validate(
            {"schemaVersion": 1, "results": [_choice_result()]}, strict=True
        )
        output.results[0].questionId = "PRIVATE INVALID MUTATION"

    with warnings.catch_warnings(record=True) as caught:
        with pytest.raises(ServiceError) as error:
            validate_decision_result(step, task, output)
    assert caught == []
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert str(error.value) == "An operation returned an invalid result."
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "raw",
    [
        "PRIVATE_MODEL_TEXT",
        {"schemaVersion": 1, "results": [_choice_result()], "PRIVATE_FIELD": "SECRET"},
        {"schemaVersion": 1, "results": [{**_choice_result(), "answer": None}]},
        {
            "schemaVersion": 1,
            "results": [
                {
                    **_choice_result(),
                    "explanation": _explanation("PRIVATE_QUOTE_NOT_IN_SOURCE"),
                }
            ],
        },
        {"schemaVersion": 1, "results": [_choice_result(question_id="other")]},
    ],
)
def test_raw_schema_and_semantic_failures_are_safe_invalid_output(raw: object) -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())
    with pytest.raises(ServiceError) as error:
        validate_decision_result(step, task, raw)
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert str(error.value) == "An operation returned an invalid result."
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__

"""Native decision adapter preserves contracts while exposing safe route facts."""

import warnings
from collections.abc import Mapping
from typing import Literal, cast

import pytest

from foliqant.adapters.decisions import (
    build_decision_input,
    validate_decision_result,
)
from foliqant.contracts.decisions import DecisionOutput
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import (
    BindingPlan,
    DecisionOptionPlan,
    DecisionQuestionPlan,
    DecisionStepPlan,
    SourceLocation,
)


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
            "issues": [] if status == "answerable" else ["no_supported_answer"],
        },
        "answer": None if option_id is None else {"optionId": option_id},
        "reason": "The supplied information supports this assessment.",
        "evidence_strength": None if option_id is None else "strong",
    }


def test_builds_canonical_input_from_actual_nonblank_sources() -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())

    assert task.model_dump(mode="json") == {
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
        {"results": [_choice_result()]},
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
        "results": [
            {
                "questionId": "classify",
                "type": "predicate",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"value": "false"},
                "reason": "The ticket says access is not blocked.",
                "evidence_strength": "strong",
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
        "results": [
            {
                "questionId": "classify",
                "type": "ordinal",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"levelId": "High.Priority-v2"},
                "reason": "The ticket explicitly states high priority.",
                "evidence_strength": "strong",
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
        "results": [
            _choice_result(question_id="queue", status="undetermined", option_id=None),
            {
                "questionId": "blocked",
                "type": "predicate",
                "answerability": {
                    "status": "undetermined",
                    "issues": ["no_supported_answer"],
                },
                "answer": {"value": "unknown"},
                "reason": "The ticket does not discuss access.",
                "evidence_strength": None,
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
        "results": [
            {
                "questionId": "queues",
                "type": "multiselect",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"optionIds": ["billing", "technical"]},
                "reason": "The ticket names billing and technical problems.",
                "evidence_strength": "strong",
            },
            {
                "questionId": "blocked",
                "type": "predicate",
                "answerability": {"status": "answerable", "issues": []},
                "answer": {"value": "true"},
                "reason": "The ticket explicitly says access is blocked.",
                "evidence_strength": "strong",
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
    output = DecisionOutput.model_validate({"results": [_choice_result()]}, strict=True)

    result = validate_decision_result(step, task, output)
    assert result.answerable is True
    assert result.route_key == "billing.queue-v2"


def test_multiple_results_follow_supplied_question_order_without_mutating_model_output() -> None:
    step = _step(_choice("first"), _choice("second"), mode="multiple")
    task = build_decision_input(step, _sources())
    raw = {
        "results": [_choice_result(question_id="second"), _choice_result(question_id="first")],
    }
    result = validate_decision_result(step, task, raw)
    assert [item["questionId"] for item in thaw_json(result.value)["results"]] == [
        "first",
        "second",
    ]
    assert [item["questionId"] for item in raw["results"]] == ["second", "first"]


def test_limited_support_does_not_override_answerability_or_choice_routing() -> None:
    step = _step(_choice())
    raw = _choice_result()
    raw["evidence_strength"] = "limited"
    result = validate_decision_result(
        step, build_decision_input(step, _sources()), {"results": [raw]}
    )
    assert result.answerable is True
    assert result.route_key == "billing.queue-v2"


@pytest.mark.parametrize("kind", ["choice", "predicate"])
@pytest.mark.parametrize("status", ["not_answerable", "undetermined"])
@pytest.mark.parametrize("strength", [None, "limited", "strong"])
def test_assessed_abstention_keeps_unresolved_routing(kind, status, strength) -> None:
    step = _step(_choice() if kind == "choice" else _predicate("classify"))
    result = _choice_result(status=status, option_id=None)
    result["type"] = kind
    result["answer"] = None if kind == "choice" else {"value": "unknown"}
    result["evidence_strength"] = strength
    validated = validate_decision_result(
        step,
        build_decision_input(step, _sources()),
        {"results": [result]},
    )
    assert validated.answerable is False
    assert validated.route_key is None
    assert thaw_json(validated.value)["evidence_strength"] == strength


def test_predicate_unknown_cannot_claim_answerable_at_adapter_boundary() -> None:
    step = _step(_predicate("classify"))
    result = {
        "questionId": "classify",
        "type": "predicate",
        "answerability": {"status": "answerable", "issues": []},
        "answer": {"value": "unknown"},
        "reason": "The relevant information is absent.",
        "evidence_strength": "strong",
    }
    with pytest.raises(ServiceError) as error:
        validate_decision_result(
            step,
            build_decision_input(step, _sources()),
            {"results": [result]},
        )
    assert error.value.code == ErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("constructed", [False, True])
def test_mutated_or_constructed_model_cannot_bypass_validation_without_warning(
    constructed: bool,
) -> None:
    step = _step(_choice())
    task = build_decision_input(step, _sources())
    if constructed:
        output = DecisionOutput.model_construct(results=[])
    else:
        output = DecisionOutput.model_validate({"results": [_choice_result()]}, strict=True)
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
        {"results": [_choice_result()], "PRIVATE_FIELD": "SECRET"},
        {"results": [{**_choice_result(), "answer": None}]},
        {
            "results": [
                {
                    **_choice_result(),
                    "reason": "   ",
                    "evidence_strength": "strong",
                }
            ],
        },
        {"results": [_choice_result(question_id="other")]},
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


def test_decision_boundaries_reject_undeclared_fields() -> None:
    from pydantic import ValidationError

    from foliqant.decisions import DecisionInput

    step = _step(_choice())
    task = build_decision_input(step, _sources())
    raw_input = task.model_dump(mode="json")
    raw_input["unexpected"] = "value"
    with pytest.raises(ValidationError):
        DecisionInput.model_validate(raw_input)
    with pytest.raises(ServiceError) as error:
        validate_decision_result(step, task, {"unexpected": "value", "results": [_choice_result()]})
    assert error.value.code == ErrorCode.INVALID_OUTPUT


def test_runtime_output_has_one_closed_unversioned_shape() -> None:
    from pydantic import ValidationError

    output = {"results": [_choice_result()]}
    assert set(DecisionOutput.model_validate(output).model_dump()) == {"results"}
    with pytest.raises(ValidationError):
        DecisionOutput.model_validate({**output, "unexpected": True})


@pytest.mark.parametrize("unknown_issue", ["missing_information", "no_matching_option"])
def test_unknown_issue_codes_are_rejected_without_runtime_mapping(unknown_issue) -> None:
    step = _step(_choice())
    raw = _choice_result(status="not_answerable", option_id=None)
    raw["answerability"]["issues"] = [unknown_issue]
    with pytest.raises(ServiceError) as error:
        validate_decision_result(step, build_decision_input(step, _sources()), {"results": [raw]})
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert raw["answerability"]["issues"] == [unknown_issue]


@pytest.mark.parametrize(
    "issues",
    [
        ["no_supported_answer", "no_supported_answer"],
        ["no_supported_answer", "conflicting_information", "multiple_valid_options", "extra"],
    ],
)
def test_native_issues_are_unique_and_bounded(issues) -> None:
    from pydantic import ValidationError

    from foliqant.decisions import Answerability

    with pytest.raises(ValidationError):
        Answerability.model_validate({"status": "not_answerable", "issues": issues})


@pytest.mark.parametrize("allow_no_match", [False, True])
@pytest.mark.parametrize("issues", [[], ["conflicting_information"], ["no_supported_answer"]])
def test_null_request_category_requires_permission_and_supported_issue(
    allow_no_match, issues
) -> None:
    step = _step(
        DecisionQuestionPlan(
            id="classify",
            type="request_units",
            prompt="Identify the requests.",
            criteria=("Use explicit requests.",),
            allowed_source_ids=("ticket",),
            options=(DecisionOptionPlan("technical", "Technical requests"),),
            allow_no_match=allow_no_match,
        )
    )
    raw = {
        "results": [
            {
                "questionId": "classify",
                "type": "request_units",
                "answerability": {"status": "answerable", "issues": issues},
                "answer": {
                    "units": [
                        {
                            "id": "request",
                            "status": "active",
                            "categoryId": None,
                            "subject": None,
                            "description": "A billing request outside the catalog.",
                        }
                    ],
                    "relations": [],
                },
                "reason": "The supplied information supports this assessment.",
                "evidence_strength": "strong",
            }
        ],
    }
    task = build_decision_input(step, _sources())
    if allow_no_match and issues == ["no_supported_answer"]:
        validated = validate_decision_result(step, task, raw)
        assert thaw_json(validated.value) == raw["results"][0]
    else:
        with pytest.raises(ServiceError) as error:
            validate_decision_result(step, task, raw)
        assert error.value.code == ErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize(
    "value",
    [
        None,
        ["billing", "technical"],
        {"status": "needs_review", "answer": None},
        42,
        "Bitte {{ nicht }} ausführen.",
    ],
)
def test_json_source_format_preserves_structured_values(value: object) -> None:
    import json
    from dataclasses import replace

    step = replace(_step(_choice()), source_formats=(("ticket", "json"),))
    supplied = cast(FrozenObject, freeze_json({"ticket": value}))
    task = build_decision_input(step, supplied)
    assert json.loads(task.state.sources[0].text) == value
    assert task.state.sources[0].id == "ticket"
    assert thaw_json(supplied)["ticket"] == value


def test_text_source_still_rejects_structured_input_without_explicit_format() -> None:
    supplied = cast(FrozenObject, freeze_json({"ticket": {"answer": "billing"}}))
    with pytest.raises(ServiceError) as caught:
        build_decision_input(_step(_choice()), supplied)
    assert caught.value.code == ErrorCode.INVALID_INPUT

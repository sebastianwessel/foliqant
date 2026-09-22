"""Runtime decision responses, separate from immutable native V2 training contracts."""

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from foliqant.decisions.base import ContractModel, Id, NonEmptyStr
from foliqant.decisions.contracts import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionInput,
    MultiselectAnswer,
    MultiselectQuestion,
    OrdinalAnswer,
    OrdinalQuestion,
    PredicateAnswer,
    PredicateQuestion,
    RequestRelation,
    RequestUnitsQuestion,
    validate_answerability,
    validate_request_relations,
)


def _runtime_version(value: object) -> object:
    if type(value) is not int or value != 3:
        raise ValueError("must be the integer 3")
    return value


type EvidenceStrength = Literal["limited", "strong"]
"""Supplied support for a returned value, not model confidence or correctness."""

Reason = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=400, pattern=r"\S")]


def _support_problem(substantive: bool, strength: EvidenceStrength | None) -> str | None:
    if substantive and strength is None:
        return "evidence-strength-required"
    if not substantive and strength is not None:
        return "evidence-strength-must-be-null"
    return None


class RequestUnit(ContractModel):
    """One requested item; identifiers are local to this response."""

    id: Id
    status: Literal["active", "withdrawn", "conditional", "quoted"]
    categoryId: Id | None
    subject: NonEmptyStr | None
    description: NonEmptyStr


class RequestUnitsAnswer(ContractModel):
    """Requested items and their explicitly supported relationships."""

    units: Annotated[list[RequestUnit], Field(max_length=256)]
    relations: Annotated[list[RequestRelation], Field(max_length=256)]


class _Result(ContractModel):
    model_config = ConfigDict(revalidate_instances="always")

    questionId: Id
    answerability: Answerability
    reason: Reason
    evidence_strength: EvidenceStrength | None

    @model_validator(mode="after")
    def support_matches_answer(self) -> Self:
        if isinstance(self, PredicateResult):
            substantive = self.answer.value != "unknown"
        elif isinstance(self, (ChoiceResult, MultiselectResult, OrdinalResult, RequestUnitsResult)):
            substantive = self.answer is not None
        else:
            return self
        problem = _support_problem(substantive, self.evidence_strength)
        if problem is not None:
            raise ValueError(problem)
        return self


class ChoiceResult(_Result):
    """One selected option, or an explained abstention with null support."""

    type: Literal["choice"]
    answer: ChoiceAnswer | None


class MultiselectResult(_Result):
    """Selected options; strength describes the weakest returned constituent."""

    type: Literal["multiselect"]
    answer: MultiselectAnswer | None


class PredicateResult(_Result):
    """A supported true/false answer, or unknown with null support."""

    type: Literal["predicate"]
    answer: PredicateAnswer


class OrdinalResult(_Result):
    """One rubric level, or an explained abstention with null support."""

    type: Literal["ordinal"]
    answer: OrdinalAnswer | None


class RequestUnitsResult(_Result):
    """Request collection; completeness is expressed by answerability separately."""

    type: Literal["request_units"]
    answer: RequestUnitsAnswer | None


type DecisionResult = Annotated[
    ChoiceResult | MultiselectResult | PredicateResult | OrdinalResult | RequestUnitsResult,
    Field(discriminator="type"),
]


class DecisionOutput(ContractModel):
    """Runtime V3 output for unchanged native V2 questions and sources.

    ``evidence_strength`` is required and null only for abstentions. Ratings do
    not override answerability, catalog membership, or configured routing.
    """

    schemaVersion: Annotated[Literal[3], BeforeValidator(_runtime_version)] = 3
    results: Annotated[list[DecisionResult], Field(min_length=1, max_length=256)]


def validate_decision_output(task: DecisionInput, result: DecisionOutput) -> list[str]:
    """Check runtime structure and source-bound subjects, not semantic support.

    Call after strict model validation. An empty returned collection and a false
    predicate are substantive answers and require a non-null support assessment.
    """
    problems: list[str] = []
    questions = {question.id: question for question in task.questions}
    sources = {source.id: source for source in task.state.sources}
    seen: set[str] = set()
    for item in result.results:
        prefix = f"question:{item.questionId}"
        if item.questionId in seen:
            problems.append(f"{prefix}:duplicate-result")
        seen.add(item.questionId)
        question = questions.get(item.questionId)
        if question is None:
            problems.append(f"{prefix}:unknown-question")
            continue
        if item.type != question.type:
            problems.append(f"{prefix}:type-mismatch")
            continue
        status = item.answerability.status
        predicate_value = item.answer.value if isinstance(item, PredicateResult) else None
        problems.extend(
            validate_answerability(
                prefix=prefix,
                task_type=item.type,
                answerability=item.answerability,
                has_answer=item.answer is not None,
                predicate_value=predicate_value,
            )
        )
        substantive = item.answer is not None and predicate_value != "unknown"
        support_problem = _support_problem(substantive, item.evidence_strength)
        if support_problem is not None:
            problems.append(f"{prefix}:{support_problem}")

        if isinstance(question, ChoiceQuestion) and isinstance(item, ChoiceResult):
            if item.answer is not None and item.answer.optionId not in {
                option.id for option in question.options
            }:
                problems.append(f"{prefix}:unknown-option")
        elif isinstance(question, MultiselectQuestion) and isinstance(item, MultiselectResult):
            if item.answer is not None:
                if not set(item.answer.optionIds) <= {option.id for option in question.options}:
                    problems.append(f"{prefix}:unknown-option")
                count = len(item.answer.optionIds)
                if status == "partially_answerable" and count == 0:
                    problems.append(f"{prefix}:partial-answer-empty")
                if not question.minSelections <= count <= question.maxSelections:
                    problems.append(f"{prefix}:selection-cardinality")
        elif isinstance(question, OrdinalQuestion) and isinstance(item, OrdinalResult):
            if item.answer is not None and item.answer.levelId not in {
                level.id for level in question.levels
            }:
                problems.append(f"{prefix}:unknown-level")
        elif isinstance(question, RequestUnitsQuestion) and isinstance(item, RequestUnitsResult):
            if item.answer is None:
                continue
            units = item.answer.units
            if status == "partially_answerable" and not units:
                problems.append(f"{prefix}:partial-answer-empty")
            if any(unit.categoryId is None for unit in units) and (
                "no_supported_answer" not in item.answerability.issues
            ):
                problems.append(f"{prefix}:null-category-without-no-supported-answer-issue")
            unit_ids = {unit.id for unit in units}
            if len(unit_ids) != len(units):
                problems.append(f"{prefix}:duplicate-request-unit")
            catalog_ids = {option.id for option in question.catalog}
            for unit in units:
                unit_prefix = f"{prefix}:request:{unit.id}"
                if unit.categoryId is None and not question.allowNoMatch:
                    problems.append(f"{unit_prefix}:no-match-forbidden")
                elif unit.categoryId is not None and unit.categoryId not in catalog_ids:
                    problems.append(f"{unit_prefix}:unknown-category")
                if unit.subject is not None and not any(
                    unit.subject in sources[source_id].text
                    for source_id in question.allowedSourceIds
                ):
                    problems.append(f"{unit_prefix}:subject-not-in-source")
            problems.extend(
                validate_request_relations(
                    prefix=prefix,
                    unit_ids=unit_ids,
                    predicate_ids={
                        candidate.id
                        for candidate in task.questions
                        if isinstance(candidate, PredicateQuestion)
                    },
                    relations=item.answer.relations,
                )
            )
    for question_id in questions.keys() - seen:
        problems.append(f"question:{question_id}:missing-result")
    return sorted(set(problems))

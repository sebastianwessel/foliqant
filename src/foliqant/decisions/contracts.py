"""Strict inputs and shared values for typed business decisions."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from .base import ContractModel, Id, NonEmptyStr


class DecisionSource(ContractModel):
    id: Id
    kind: Literal["message", "document", "table", "policy", "metadata"]
    text: NonEmptyStr


class DecisionState(ContractModel):
    sources: Annotated[list[DecisionSource], Field(min_length=1, max_length=256)]

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value: list[DecisionSource]) -> list[DecisionSource]:
        if len({source.id for source in value}) != len(value):
            raise ValueError("source IDs must be unique")
        return value


class DecisionOption(ContractModel):
    id: Id
    description: NonEmptyStr


class _Question(ContractModel):
    id: Id
    prompt: NonEmptyStr
    criteria: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=64)]
    allowedSourceIds: Annotated[list[Id], Field(min_length=1, max_length=256)]

    @field_validator("criteria", "allowedSourceIds")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


def _unique_options(options: list[DecisionOption]) -> list[DecisionOption]:
    if len({option.id for option in options}) != len(options):
        raise ValueError("option IDs must be unique")
    return options


class ChoiceQuestion(_Question):
    type: Literal["choice"]
    options: Annotated[list[DecisionOption], Field(min_length=2, max_length=1024)]

    _options = field_validator("options")(_unique_options)


class MultiselectQuestion(_Question):
    type: Literal["multiselect"]
    options: Annotated[list[DecisionOption], Field(min_length=1, max_length=1024)]
    minSelections: Annotated[StrictInt, Field(ge=0, le=1024)]
    maxSelections: Annotated[StrictInt, Field(ge=1, le=1024)]

    _options = field_validator("options")(_unique_options)

    @model_validator(mode="after")
    def valid_cardinality(self) -> MultiselectQuestion:
        if self.minSelections > self.maxSelections:
            raise ValueError("minSelections must not exceed maxSelections")
        if self.maxSelections > len(self.options):
            raise ValueError("maxSelections must not exceed option count")
        return self


class PredicateQuestion(_Question):
    type: Literal["predicate"]


class OrdinalQuestion(_Question):
    type: Literal["ordinal"]
    levels: Annotated[list[DecisionOption], Field(min_length=2, max_length=128)]

    @field_validator("levels")
    @classmethod
    def unique_levels(cls, value: list[DecisionOption]) -> list[DecisionOption]:
        return _unique_options(value)


class RequestUnitsQuestion(_Question):
    type: Literal["request_units"]
    catalog: Annotated[list[DecisionOption], Field(max_length=1024)]
    allowNoMatch: StrictBool

    @field_validator("catalog")
    @classmethod
    def unique_catalog(cls, value: list[DecisionOption]) -> list[DecisionOption]:
        return _unique_options(value)


type DecisionQuestion = Annotated[
    ChoiceQuestion
    | MultiselectQuestion
    | PredicateQuestion
    | OrdinalQuestion
    | RequestUnitsQuestion,
    Field(discriminator="type"),
]


class DecisionInput(ContractModel):
    state: DecisionState
    questions: Annotated[list[DecisionQuestion], Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def valid_references(self) -> DecisionInput:
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("question IDs must be unique")
        source_ids = {source.id for source in self.state.sources}
        if any(not set(question.allowedSourceIds) <= source_ids for question in self.questions):
            raise ValueError("allowedSourceIds must reference state sources")
        return self


type AnswerabilityStatus = Literal[
    "answerable", "partially_answerable", "not_answerable", "undetermined"
]
type AnswerabilityIssue = Literal[
    "no_supported_answer",
    "conflicting_information",
    "multiple_valid_options",
]


class Answerability(ContractModel):
    status: AnswerabilityStatus
    issues: Annotated[list[AnswerabilityIssue], Field(max_length=3)]

    @field_validator("issues")
    @classmethod
    def unique_issues(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("answerability issues must be unique")
        return value


class ChoiceAnswer(ContractModel):
    optionId: Id


class MultiselectAnswer(ContractModel):
    optionIds: Annotated[list[Id], Field(max_length=1024)]

    @field_validator("optionIds")
    @classmethod
    def unique_options(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("selected option IDs must be unique")
        return value


class PredicateAnswer(ContractModel):
    value: Literal["true", "false", "unknown"]


class OrdinalAnswer(ContractModel):
    levelId: Id


class ConditionalRelation(ContractModel):
    type: Literal["conditional_on"]
    requestId: Id
    predicateQuestionId: Id
    requiredValue: Literal["true", "false"]


class RequiresRelation(ContractModel):
    type: Literal["requires"]
    requestId: Id
    requiredRequestId: Id


class PrecedesRelation(ContractModel):
    type: Literal["precedes"]
    beforeRequestId: Id
    afterRequestId: Id


class MutuallyExclusiveRelation(ContractModel):
    type: Literal["mutually_exclusive"]
    requestIds: Annotated[list[Id], Field(min_length=2, max_length=64)]

    @field_validator("requestIds")
    @classmethod
    def unique_requests(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("mutually exclusive request IDs must be unique")
        return value


type RequestRelation = Annotated[
    ConditionalRelation | RequiresRelation | PrecedesRelation | MutuallyExclusiveRelation,
    Field(discriminator="type"),
]


def _has_directed_cycle(edges: list[tuple[str, str]]) -> bool:
    graph: dict[str, list[str]] = defaultdict(list)
    for start, end in edges:
        graph[start].append(end)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(graph))


def _relation_key(relation: RequestRelation) -> tuple[object, ...]:
    if isinstance(relation, ConditionalRelation):
        return (
            relation.type,
            relation.requestId,
            relation.predicateQuestionId,
            relation.requiredValue,
        )
    if isinstance(relation, RequiresRelation):
        return (relation.type, relation.requestId, relation.requiredRequestId)
    if isinstance(relation, PrecedesRelation):
        return (relation.type, relation.beforeRequestId, relation.afterRequestId)
    return (relation.type, *sorted(relation.requestIds))


def _has_path(edges: list[tuple[str, str]], start: str, end: str) -> bool:
    graph: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        graph[source].append(target)
    pending = [start]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == end:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(graph[node])
    return False


def validate_answerability(
    *,
    prefix: str,
    task_type: str,
    answerability: Answerability,
    has_answer: bool,
    predicate_value: str | None,
) -> list[str]:
    """Check answer presence and status without interpreting support or explanations."""
    problems: list[str] = []
    status = answerability.status
    if status != "answerable" and not answerability.issues:
        problems.append(f"{prefix}:issues-required")
    if status == "partially_answerable" and task_type not in {"multiselect", "request_units"}:
        problems.append(f"{prefix}:partial-not-supported")
    if task_type == "predicate":
        if status == "answerable" and predicate_value == "unknown":
            problems.append(f"{prefix}:unknown-on-answerable")
        if status in {"not_answerable", "undetermined"} and predicate_value != "unknown":
            problems.append(f"{prefix}:substantive-on-unanswerable")
    else:
        if status in {"answerable", "partially_answerable"} and not has_answer:
            problems.append(f"{prefix}:answer-required")
        if status in {"not_answerable", "undetermined"} and has_answer:
            problems.append(f"{prefix}:answer-must-be-null")
    return problems


def validate_request_relations(
    *,
    prefix: str,
    unit_ids: set[str],
    predicate_ids: set[str],
    relations: list[RequestRelation],
) -> list[str]:
    """Validate request graph references and consistency without evidence formatting.

    This check does not establish
    whether the input text supports a relation.
    """
    problems: list[str] = []
    relation_keys: list[tuple[object, ...]] = []
    directed: list[tuple[str, str]] = []
    requires: list[tuple[str, str]] = []
    exclusive_groups: list[set[str]] = []
    conditions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for relation in relations:
        relation_keys.append(_relation_key(relation))
        if isinstance(relation, ConditionalRelation):
            if relation.requestId not in unit_ids:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.predicateQuestionId not in predicate_ids:
                problems.append(f"{prefix}:relation-unknown-predicate")
            conditions[(relation.requestId, relation.predicateQuestionId)].add(
                relation.requiredValue
            )
        elif isinstance(relation, RequiresRelation):
            if relation.requestId not in unit_ids or relation.requiredRequestId not in unit_ids:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.requestId == relation.requiredRequestId:
                problems.append(f"{prefix}:relation-self-reference")
            edge = (relation.requiredRequestId, relation.requestId)
            requires.append(edge)
            directed.append(edge)
        elif isinstance(relation, PrecedesRelation):
            if relation.beforeRequestId not in unit_ids or relation.afterRequestId not in unit_ids:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.beforeRequestId == relation.afterRequestId:
                problems.append(f"{prefix}:relation-self-reference")
            directed.append((relation.beforeRequestId, relation.afterRequestId))
        else:
            if not set(relation.requestIds) <= unit_ids:
                problems.append(f"{prefix}:relation-unknown-request")
            exclusive_groups.append(set(relation.requestIds))
    if len(relation_keys) != len(set(relation_keys)):
        problems.append(f"{prefix}:duplicate-relation")
    if _has_directed_cycle(directed):
        problems.append(f"{prefix}:relation-cycle")
    if any(len(values) > 1 for values in conditions.values()):
        problems.append(f"{prefix}:relation-conflicting-condition")
    if any(
        _has_path(requires, first, second) or _has_path(requires, second, first)
        for group in exclusive_groups
        for first in group
        for second in group
        if first < second
    ):
        problems.append(f"{prefix}:relation-exclusive-dependency")
    return problems

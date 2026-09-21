"""Strict message-content contracts for native typed decision training data."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from .base import ContractModel, Id, NonEmptyStr, SchemaVersion


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
    schemaVersion: SchemaVersion = 1
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


class Citation(ContractModel):
    sourceId: Id
    quote: NonEmptyStr


class Explanation(ContractModel):
    summary: Annotated[NonEmptyStr, Field(max_length=400)]
    evidence: Annotated[list[Citation], Field(max_length=256)]
    contraryEvidence: Annotated[list[Citation], Field(max_length=256)]
    missingFacts: Annotated[list[NonEmptyStr], Field(max_length=256)]


type AnswerabilityStatus = Literal[
    "answerable", "partially_answerable", "not_answerable", "undetermined"
]
type AnswerabilityIssue = Literal[
    "missing_information",
    "conflicting_information",
    "multiple_valid_options",
    "no_matching_option",
]


class Answerability(ContractModel):
    status: AnswerabilityStatus
    issues: Annotated[list[AnswerabilityIssue], Field(max_length=32)]

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


class RequestUnit(ContractModel):
    id: Id
    status: Literal["active", "withdrawn", "conditional", "quoted"]
    categoryId: Id | None
    subject: NonEmptyStr | None
    description: NonEmptyStr
    evidence: Annotated[list[Citation], Field(min_length=1, max_length=64)]


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


class RequestUnitsAnswer(ContractModel):
    units: Annotated[list[RequestUnit], Field(max_length=256)]
    relations: Annotated[list[RequestRelation], Field(max_length=256)]


class _Result(ContractModel):
    questionId: Id
    answerability: Answerability
    explanation: Explanation


class ChoiceResult(_Result):
    type: Literal["choice"]
    answer: ChoiceAnswer | None


class MultiselectResult(_Result):
    type: Literal["multiselect"]
    answer: MultiselectAnswer | None


class PredicateResult(_Result):
    type: Literal["predicate"]
    answer: PredicateAnswer


class OrdinalResult(_Result):
    type: Literal["ordinal"]
    answer: OrdinalAnswer | None


class RequestUnitsResult(_Result):
    type: Literal["request_units"]
    answer: RequestUnitsAnswer | None


type DecisionResult = Annotated[
    ChoiceResult | MultiselectResult | PredicateResult | OrdinalResult | RequestUnitsResult,
    Field(discriminator="type"),
]


class DecisionOutput(ContractModel):
    schemaVersion: SchemaVersion = 1
    results: Annotated[list[DecisionResult], Field(min_length=1, max_length=256)]


def _citation_problems(
    *,
    prefix: str,
    citations: list[Citation],
    sources: dict[str, DecisionSource],
    allowed: set[str],
) -> list[str]:
    problems: list[str] = []
    for index, citation in enumerate(citations):
        location = f"{prefix}:citation:{index}"
        source = sources.get(citation.sourceId)
        if source is None:
            problems.append(f"{location}:unknown-source")
        elif citation.sourceId not in allowed:
            problems.append(f"{location}:source-not-allowed")
        elif citation.quote not in source.text:
            problems.append(f"{location}:quote-not-found")
    return problems


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


def validate_decision_output(task: DecisionInput, result: DecisionOutput) -> list[str]:
    """Check cross-field consistency and exact evidence references, not prose entailment."""

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
        if status != "answerable" and not item.answerability.issues:
            problems.append(f"{prefix}:issues-required")
        if status == "partially_answerable" and item.type not in {
            "multiselect",
            "request_units",
        }:
            problems.append(f"{prefix}:partial-not-supported")
        if item.type != "predicate":
            if status in {"answerable", "partially_answerable"} and item.answer is None:
                problems.append(f"{prefix}:answer-required")
            if status in {"not_answerable", "undetermined"} and item.answer is not None:
                problems.append(f"{prefix}:answer-must-be-null")
        explanation_citations = item.explanation.evidence + item.explanation.contraryEvidence
        problems.extend(
            _citation_problems(
                prefix=f"{prefix}:explanation",
                citations=explanation_citations,
                sources=sources,
                allowed=set(question.allowedSourceIds),
            )
        )
        if status in {"answerable", "partially_answerable"} and not item.explanation.evidence:
            problems.append(f"{prefix}:evidence-required")

        if isinstance(question, ChoiceQuestion) and isinstance(item, ChoiceResult):
            if item.answer is not None and item.answer.optionId not in {
                option.id for option in question.options
            }:
                problems.append(f"{prefix}:unknown-option")
        elif isinstance(question, MultiselectQuestion) and isinstance(item, MultiselectResult):
            if item.answer is not None:
                option_ids = {option.id for option in question.options}
                if not set(item.answer.optionIds) <= option_ids:
                    problems.append(f"{prefix}:unknown-option")
                count = len(item.answer.optionIds)
                if status == "partially_answerable" and count == 0:
                    problems.append(f"{prefix}:partial-answer-empty")
                if not question.minSelections <= count <= question.maxSelections:
                    problems.append(f"{prefix}:selection-cardinality")
        elif isinstance(question, PredicateQuestion) and isinstance(item, PredicateResult):
            if status == "answerable" and item.answer.value == "unknown":
                problems.append(f"{prefix}:unknown-on-answerable")
            if status in {"not_answerable", "undetermined"} and item.answer.value != "unknown":
                problems.append(f"{prefix}:substantive-on-unanswerable")
            if status == "partially_answerable":
                problems.append(f"{prefix}:partial-not-supported")
        elif isinstance(question, OrdinalQuestion) and isinstance(item, OrdinalResult):
            if item.answer is not None and item.answer.levelId not in {
                level.id for level in question.levels
            }:
                problems.append(f"{prefix}:unknown-level")
        elif isinstance(question, RequestUnitsQuestion) and isinstance(item, RequestUnitsResult):
            if item.answer is not None:
                if status == "partially_answerable" and not item.answer.units:
                    problems.append(f"{prefix}:partial-answer-empty")
                if (
                    any(unit.categoryId is None for unit in item.answer.units)
                    and "no_matching_option" not in item.answerability.issues
                ):
                    problems.append(f"{prefix}:null-category-without-no-match-issue")
                problems.extend(
                    _request_answer_problems(
                        prefix=prefix,
                        task=task,
                        question=question,
                        answer=item.answer,
                        sources=sources,
                    )
                )
    for question_id in questions.keys() - seen:
        problems.append(f"question:{question_id}:missing-result")
    return sorted(set(problems))


def _request_answer_problems(
    *,
    prefix: str,
    task: DecisionInput,
    question: RequestUnitsQuestion,
    answer: RequestUnitsAnswer,
    sources: dict[str, DecisionSource],
) -> list[str]:
    problems: list[str] = []
    units = {unit.id: unit for unit in answer.units}
    if len(units) != len(answer.units):
        problems.append(f"{prefix}:duplicate-request-unit")
    catalog_ids = {option.id for option in question.catalog}
    for unit in answer.units:
        unit_prefix = f"{prefix}:request:{unit.id}"
        if unit.categoryId is None and not question.allowNoMatch:
            problems.append(f"{unit_prefix}:no-match-forbidden")
        elif unit.categoryId is not None and unit.categoryId not in catalog_ids:
            problems.append(f"{unit_prefix}:unknown-category")
        problems.extend(
            _citation_problems(
                prefix=unit_prefix,
                citations=unit.evidence,
                sources=sources,
                allowed=set(question.allowedSourceIds),
            )
        )
        if unit.subject is not None and not any(
            unit.subject in citation.quote for citation in unit.evidence
        ):
            problems.append(f"{unit_prefix}:subject-not-in-evidence")
    relation_keys: list[tuple[object, ...]] = []
    directed: list[tuple[str, str]] = []
    requires: list[tuple[str, str]] = []
    exclusive_groups: list[set[str]] = []
    conditions: dict[tuple[str, str], set[str]] = defaultdict(set)
    predicate_ids = {item.id for item in task.questions if isinstance(item, PredicateQuestion)}
    for relation in answer.relations:
        relation_keys.append(_relation_key(relation))
        if isinstance(relation, ConditionalRelation):
            if relation.requestId not in units:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.predicateQuestionId not in predicate_ids:
                problems.append(f"{prefix}:relation-unknown-predicate")
            conditions[(relation.requestId, relation.predicateQuestionId)].add(
                relation.requiredValue
            )
        elif isinstance(relation, RequiresRelation):
            if relation.requestId not in units or relation.requiredRequestId not in units:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.requestId == relation.requiredRequestId:
                problems.append(f"{prefix}:relation-self-reference")
            edge = (relation.requiredRequestId, relation.requestId)
            requires.append(edge)
            directed.append(edge)
        elif isinstance(relation, PrecedesRelation):
            if relation.beforeRequestId not in units or relation.afterRequestId not in units:
                problems.append(f"{prefix}:relation-unknown-request")
            if relation.beforeRequestId == relation.afterRequestId:
                problems.append(f"{prefix}:relation-self-reference")
            directed.append((relation.beforeRequestId, relation.afterRequestId))
        else:
            if not set(relation.requestIds) <= units.keys():
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


def semantic_signature(output: DecisionOutput) -> object:
    """Return answer semantics while ignoring generated explanation and citation prose."""

    results: list[object] = []
    for item in sorted(output.results, key=lambda value: value.questionId):
        answer: object
        if isinstance(item, RequestUnitsResult) and item.answer is not None:
            answer = {
                "units": [
                    {
                        "id": unit.id,
                        "status": unit.status,
                        "categoryId": unit.categoryId,
                        "subject": unit.subject,
                    }
                    for unit in sorted(item.answer.units, key=lambda value: value.id)
                ],
                "relations": sorted(
                    (_relation_key(relation) for relation in item.answer.relations),
                    key=repr,
                ),
            }
        elif isinstance(item, MultiselectResult) and item.answer is not None:
            answer = {"optionIds": sorted(item.answer.optionIds)}
        elif item.answer is None:
            answer = None
        else:
            answer = item.answer.model_dump(mode="json")
        results.append(
            {
                "questionId": item.questionId,
                "type": item.type,
                "answerability": {
                    "status": item.answerability.status,
                    "issues": sorted(item.answerability.issues),
                },
                "answer": answer,
            }
        )
    return {"schemaVersion": output.schemaVersion, "results": results}

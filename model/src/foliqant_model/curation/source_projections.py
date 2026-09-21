"""Bounded native projections for compatible auxiliary source annotations."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from foliqant.decisions import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceResult,
    Citation,
    DecisionInput,
    DecisionOption,
    DecisionOutput,
    DecisionQuestion,
    DecisionResult,
    DecisionSource,
    DecisionState,
    Explanation,
    MultiselectAnswer,
    MultiselectQuestion,
    MultiselectResult,
    OrdinalAnswer,
    OrdinalQuestion,
    OrdinalResult,
    PredicateAnswer,
    PredicateQuestion,
    PredicateResult,
)

from ..contracts.base import canonical_digest
from .contracts import ImportedRecord
from .decision_seeds import DecisionSeed, _projected_seed

PROJECTION_VERSION = "auxiliary-native-projections-v1"

_TASK_VERSIONS = {
    "typed-decisions": "typed-all-questions-v1",
    "multidogo-finance": "multidogo-18-intents-v1",
    "tatqa": "tatqa-adjacent-period-comparison-v1",
}

_TYPED_WORKFLOWS = {
    "workflow:agent_trace_observability",
    "workflow:customer_service",
    "workflow:invoice_processing",
    "workflow:security_incidents",
}

_MULTIDOGO_INTENTS = (
    "checkbalance",
    "checkoffereligibility",
    "closeaccount",
    "closinggreeting",
    "confirmation",
    "contentonly",
    "disputecharge",
    "getroutingnumber",
    "openaccount",
    "openinggreeting",
    "orderchecks",
    "outofdomain",
    "rejection",
    "replacecard",
    "reportlostcard",
    "thankyou",
    "transfermoney",
    "updateaddress",
)

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_DISPLAYED_NUMBER = re.compile(
    r"^\s*(?P<currency>[$£€¥])?\s*"
    r"(?P<body>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|"
    r"\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\))"
    r"\s*(?P<percent>%)?\s*$"
)


@dataclass(frozen=True)
class ProjectionResult:
    """A projected seed or a stable reason why the row was excluded."""

    seed: DecisionSeed | None
    reason: str


def _json_loads(value: str) -> object:
    def reject_constant(constant: str) -> object:
        raise ValueError(f"non-finite JSON constant: {constant}")

    return json.loads(value, parse_constant=reject_constant)


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _is_finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(cast(float, value))


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected nonempty text")
    return value


def _required_text_list(value: object, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError("expected text list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError("expected nonempty text list items")
    return cast(list[str], value)


def _required_text_map(value: object, *, minimum: int = 1) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) < minimum:
        raise ValueError("expected text mapping")
    if not all(
        isinstance(key, str) and key and isinstance(description, str) and description
        for key, description in value.items()
    ):
        raise ValueError("expected nonempty text mapping entries")
    return cast(dict[str, str], value)


def _messages(row: ImportedRecord, *, include_oracle: bool) -> tuple[dict[str, object], object]:
    if len(row.record.messages) < 2:
        raise ValueError("source row has no terminal task and answer")
    source = _json_loads(row.record.messages[-2].content)
    oracle = _json_loads(row.record.messages[-1].content) if include_oracle else None
    if not isinstance(source, dict):
        raise ValueError("source task is not an object")
    return cast(dict[str, object], source), oracle


def _answerability() -> Answerability:
    return Answerability(status="answerable", issues=[])


def _explanation(summary: str, source_id: str, quote: str) -> Explanation:
    if len(summary) > 400:
        raise ValueError("canonical projection explanation is too long")
    return Explanation(
        summary=summary,
        evidence=[Citation(sourceId=source_id, quote=quote)],
        contraryEvidence=[],
        missingFacts=[],
    )


def _versioned_seed(
    row: ImportedRecord, task: DecisionInput, oracle: DecisionOutput
) -> DecisionSeed:
    seed = _projected_seed(row, task, oracle)
    task_version = _TASK_VERSIONS[row.record.sourceId]
    digest = canonical_digest(
        {
            "oracle": oracle.model_dump(mode="json"),
            "projectionVersion": PROJECTION_VERSION,
            "sourceRecordId": row.record.id,
            "task": task.model_dump(mode="json"),
            "taskVersion": task_version,
        }
    )
    parent = seed.parent.model_copy(
        update={
            "id": f"native-{row.record.sourceId}.record.{digest[:32]}",
            "tags": sorted(
                set(seed.parent.tags)
                | ({tag for tag in row.record.tags if tag in _TYPED_WORKFLOWS})
                | {
                    f"projection-version:{PROJECTION_VERSION}",
                    f"projection-task:{task_version}",
                }
            ),
        }
    )
    return seed.model_copy(update={"parent": parent})


def _validate_teacher_gold(gold: object, question_type: str, labels: set[str]) -> dict[str, object]:
    if not isinstance(gold, dict):
        raise ValueError("gold question is not an object")
    typed = cast(dict[str, object], gold)
    required = {"confidence", "label", "probabilities", "type"}
    if question_type == "noul":
        required.add("noul")
    elif question_type == "score":
        required.add("score")
    if set(typed) != required or typed.get("type") != question_type:
        raise ValueError("gold question shape is invalid")
    label = typed.get("label")
    probabilities = typed.get("probabilities")
    if not isinstance(label, str) or label not in labels:
        raise ValueError("gold label is invalid")
    if not isinstance(probabilities, dict) or set(probabilities) != labels:
        raise ValueError("gold probabilities are invalid")
    if not all(_is_finite_number(value) for value in probabilities.values()):
        raise ValueError("gold probabilities are invalid")
    if not _is_finite_number(typed.get("confidence")):
        raise ValueError("gold confidence is invalid")
    if question_type == "noul" and not _is_finite_number(typed.get("noul")):
        raise ValueError("gold noul value is invalid")
    if question_type == "score" and not _is_finite_number(typed.get("score")):
        raise ValueError("gold score is invalid")
    return typed


def _typed_question(
    question_id: str,
    source: object,
    gold: object,
    source_id: str,
    source_quote: str,
) -> tuple[DecisionQuestion, DecisionResult]:
    if not isinstance(source, dict):
        raise ValueError("typed question is not an object")
    typed = cast(dict[str, object], source)
    question_type = typed.get("type")
    instructions = _required_text(typed.get("instructions"))

    if question_type == "choice":
        if set(typed) != {"criteria", "instructions", "type"}:
            raise ValueError("choice question shape is invalid")
        options = _required_text_map(typed.get("criteria"), minimum=2)
        teacher = _validate_teacher_gold(gold, "choice", set(options))
        label = cast(str, teacher["label"])
        choice_question = ChoiceQuestion(
            id=question_id,
            type="choice",
            prompt=instructions,
            criteria=[instructions],
            allowedSourceIds=[source_id],
            options=[
                DecisionOption(id=option_id, description=description)
                for option_id, description in options.items()
            ],
        )
        choice_result = ChoiceResult(
            questionId=question_id,
            type="choice",
            answerability=_answerability(),
            answer=ChoiceAnswer(optionId=label),
            explanation=_explanation(
                f"The supplied state supports: {options[label]}", source_id, source_quote
            ),
        )
        return choice_question, choice_result

    if question_type == "noul":
        if set(typed) not in (
            {"instructions", "type"},
            {"criteria", "instructions", "type"},
        ):
            raise ValueError("noul question shape is invalid")
        criteria_value = typed.get("criteria")
        if criteria_value is None:
            criteria = [instructions]
        else:
            mapped = _required_text_map(criteria_value, minimum=2)
            if set(mapped) != {"false", "true"}:
                raise ValueError("noul criteria are invalid")
            criteria = list(mapped.values())
        teacher = _validate_teacher_gold(gold, "noul", {"false", "true"})
        label = cast(Literal["false", "true"], teacher["label"])
        predicate_question = PredicateQuestion(
            id=question_id,
            type="predicate",
            prompt=instructions,
            criteria=criteria,
            allowedSourceIds=[source_id],
        )
        predicate_result = PredicateResult(
            questionId=question_id,
            type="predicate",
            answerability=_answerability(),
            answer=PredicateAnswer(value=label),
            explanation=_explanation(
                (
                    "The supplied state supports the stated condition."
                    if label == "true"
                    else "The supplied state contradicts the stated condition."
                ),
                source_id,
                source_quote,
            ),
        )
        return predicate_question, predicate_result

    if question_type == "score":
        if set(typed) != {"criteria", "instructions", "type"}:
            raise ValueError("score question shape is invalid")
        rubric = _required_text_list(typed.get("criteria"))
        level_ids = [str(index) for index in range(len(rubric))]
        teacher = _validate_teacher_gold(gold, "score", set(level_ids))
        label = cast(str, teacher["label"])
        ordinal_question = OrdinalQuestion(
            id=question_id,
            type="ordinal",
            prompt=instructions,
            criteria=rubric,
            allowedSourceIds=[source_id],
            levels=[
                DecisionOption(id=level_id, description=description)
                for level_id, description in zip(level_ids, rubric, strict=True)
            ],
        )
        ordinal_result = OrdinalResult(
            questionId=question_id,
            type="ordinal",
            answerability=_answerability(),
            answer=OrdinalAnswer(levelId=label),
            explanation=_explanation(
                f"The supplied state meets: {rubric[int(label)]}",
                source_id,
                source_quote,
            ),
        )
        return ordinal_question, ordinal_result

    raise ValueError("unsupported typed question type")


def _project_typed_decisions(row: ImportedRecord) -> DecisionSeed:
    source, oracle = _messages(row, include_oracle=True)
    if set(source) != {"questions", "state"}:
        raise ValueError("typed decision task shape is invalid")
    questions = source.get("questions")
    state = source.get("state")
    if not isinstance(questions, dict) or len(questions) != 5 or not isinstance(state, dict):
        raise ValueError("typed decision case is incomplete")
    if not isinstance(oracle, dict) or set(oracle) != set(questions):
        raise ValueError("typed decision gold does not cover the full case")

    workflow_tags = {tag for tag in row.record.tags if tag.startswith("workflow:")}
    if len(workflow_tags) != 1 or not workflow_tags <= _TYPED_WORKFLOWS:
        raise ValueError("typed decision workflow tag is invalid")

    state_text = _json_text(state)
    state_source_id = "shared-state"
    native_questions: list[DecisionQuestion] = []
    native_results: list[DecisionResult] = []
    for question_id, question in questions.items():
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("typed question ID is invalid")
        native_question, native_result = _typed_question(
            question_id,
            question,
            cast(dict[str, object], oracle).get(question_id),
            state_source_id,
            state_text,
        )
        native_questions.append(native_question)
        native_results.append(native_result)

    task = DecisionInput(
        state=DecisionState(
            sources=[DecisionSource(id=state_source_id, kind="document", text=state_text)]
        ),
        questions=native_questions,
    )
    result = DecisionOutput(results=native_results)
    return _versioned_seed(row, task, result)


def _project_multidogo(row: ImportedRecord) -> DecisionSeed:
    source, oracle = _messages(row, include_oracle=True)
    if set(source) != {"utterance"} or not isinstance(oracle, dict):
        raise ValueError("MultiDoGO task shape is invalid")
    utterance = _required_text(source.get("utterance"))
    if set(oracle) != {"intents", "slotLabels"}:
        raise ValueError("MultiDoGO target shape is invalid")
    intents = _required_text_list(oracle.get("intents"))
    slot_labels = _required_text_list(oracle.get("slotLabels"), nonempty=False)
    if len(intents) != len(set(intents)) or not set(intents) <= set(_MULTIDOGO_INTENTS):
        raise ValueError("MultiDoGO intents are invalid")
    if len(slot_labels) != len(utterance.split()):
        raise ValueError("MultiDoGO slot alignment is invalid")

    source_id = "customer-turn"
    task = DecisionInput(
        state=DecisionState(sources=[DecisionSource(id=source_id, kind="message", text=utterance)]),
        questions=[
            MultiselectQuestion(
                id="active-intents",
                type="multiselect",
                prompt="Select every active intent in the finance customer turn.",
                criteria=["Select each independently applicable intent."],
                allowedSourceIds=[source_id],
                options=[
                    DecisionOption(id=intent, description=intent) for intent in _MULTIDOGO_INTENTS
                ],
                minSelections=1,
                maxSelections=len(_MULTIDOGO_INTENTS),
            )
        ],
    )
    result = DecisionOutput(
        results=[
            MultiselectResult(
                questionId="active-intents",
                type="multiselect",
                answerability=_answerability(),
                answer=MultiselectAnswer(optionIds=intents),
                explanation=_explanation(
                    "The source annotation identifies every active intent for this turn.",
                    source_id,
                    utterance,
                ),
            )
        ]
    )
    return _versioned_seed(row, task, result)


def _displayed_decimal(value: str) -> tuple[Decimal, tuple[str | None, bool]] | None:
    match = _DISPLAYED_NUMBER.fullmatch(value)
    if match is None:
        return None
    if match.group("currency") is not None and match.group("percent") is not None:
        return None
    body = match.group("body")
    parenthesized = body.startswith("(")
    number_text = body[1:-1] if parenthesized else body
    if parenthesized and (number_text.startswith("+") or number_text.startswith("-")):
        return None
    if _NUMBER.fullmatch(number_text.lstrip("+-")) is None:
        return None
    try:
        number = Decimal(number_text.replace(",", ""))
    except InvalidOperation:
        return None
    if parenthesized:
        number = -number
    return number, (match.group("currency"), match.group("percent") is not None)


def _tatqa_table(source: dict[str, object]) -> list[list[str]]:
    table = source.get("table")
    if not isinstance(table, list) or len(table) < 2:
        raise ValueError("TAT-QA table is missing")
    if not all(isinstance(row, list) for row in table):
        raise ValueError("TAT-QA table rows are invalid")
    rows = cast(list[list[object]], table)
    width = len(rows[0])
    if width < 3 or any(len(row) != width for row in rows):
        raise ValueError("TAT-QA table is not rectangular")
    if not all(isinstance(cell, str) for row in rows for cell in row):
        raise ValueError("TAT-QA table cells are not text")
    return cast(list[list[str]], rows)


def _tatqa_projection(row: ImportedRecord) -> DecisionSeed:
    source, _ = _messages(row, include_oracle=False)
    table = _tatqa_table(source)
    header = table[0]

    periods: dict[int, int] = {}
    seen_years: set[int] = set()
    for index, cell in enumerate(header[1:], start=1):
        matched_year = _YEAR.fullmatch(cell.strip())
        if matched_year is None:
            continue
        year = int(matched_year.group(1))
        if year in seen_years:
            raise ValueError("TAT-QA header periods are not unique")
        seen_years.add(year)
        periods[index] = year

    adjacent_pairs = [
        (first, first + 1)
        for first in range(1, len(header) - 1)
        if first in periods and first + 1 in periods
    ]
    if not adjacent_pairs:
        raise LookupError("no adjacent TAT-QA periods")

    label_counts = Counter(data_row[0].strip() for data_row in table[1:] if data_row[0].strip())

    selected: tuple[int, int, list[str], Decimal, Decimal] | None = None
    for first, second in adjacent_pairs:
        for data_row in table[1:]:
            if not data_row[0].strip() or label_counts[data_row[0].strip()] != 1:
                continue
            parsed_first = _displayed_decimal(data_row[first])
            parsed_second = _displayed_decimal(data_row[second])
            if parsed_first is None or parsed_second is None:
                continue
            if parsed_first[1] != parsed_second[1]:
                continue
            selected = (first, second, data_row, parsed_first[0], parsed_second[0])
            break
        if selected is not None:
            break
    if selected is None:
        raise LookupError("no eligible TAT-QA comparison")

    first, second, data_row, selected_first, selected_second = selected
    header_quote = _json_text(header)
    data_quote = _json_text(data_row)
    if len(header_quote.encode("utf-8")) > 4096 or len(data_quote.encode("utf-8")) > 4096:
        raise ValueError("TAT-QA citation row is too long")
    table_text = _json_text(table)
    source_id = "table-context"
    task = DecisionInput(
        state=DecisionState(sources=[DecisionSource(id=source_id, kind="table", text=table_text)]),
        questions=[
            PredicateQuestion(
                id="displayed-value-greater",
                type="predicate",
                prompt=(
                    f'Does the displayed signed numeric value for "{data_row[0].strip()}" in '
                    f'"{header[first]}" exceed its displayed value in "{header[second]}"?'
                ),
                criteria=[
                    "Compare the two displayed signed numeric values directly; equality is false "
                    "and no year-over-year growth may be inferred."
                ],
                allowedSourceIds=[source_id],
            )
        ],
    )
    answer: Literal["true", "false"] = "true" if selected_first > selected_second else "false"
    result = DecisionOutput(
        results=[
            PredicateResult(
                questionId="displayed-value-greater",
                type="predicate",
                answerability=_answerability(),
                answer=PredicateAnswer(value=answer),
                explanation=Explanation(
                    summary=(
                        "The first displayed value is greater than the second."
                        if answer == "true"
                        else "The first displayed value is not greater than the second."
                    ),
                    evidence=[
                        Citation(sourceId=source_id, quote=header_quote),
                        Citation(sourceId=source_id, quote=data_quote),
                    ],
                    contraryEvidence=[],
                    missingFacts=[],
                ),
            )
        ]
    )
    return _versioned_seed(row, task, result)


def projection_priority(row: ImportedRecord) -> tuple[int, str]:
    """Sort valid MultiDoGO multi-intent rows before all deterministic row IDs."""

    if row.record.sourceId == "multidogo-finance":
        try:
            _, oracle = _messages(row, include_oracle=True)
            if isinstance(oracle, dict):
                intents = oracle.get("intents")
                if isinstance(intents, list) and len(intents) > 1:
                    return (0, row.record.id)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return (1, row.record.id)


def project_auxiliary(row: ImportedRecord) -> ProjectionResult:
    """Project one supported auxiliary row without model inference or mutation."""

    try:
        if row.record.sourceId == "typed-decisions":
            seed = _project_typed_decisions(row)
        elif row.record.sourceId == "multidogo-finance":
            seed = _project_multidogo(row)
        elif row.record.sourceId == "tatqa":
            seed = _tatqa_projection(row)
        else:
            return ProjectionResult(seed=None, reason="unsupported-source")
    except LookupError:
        return ProjectionResult(seed=None, reason="no-eligible-tatqa-comparison")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ProjectionResult(seed=None, reason=f"malformed-{row.record.sourceId}")
    return ProjectionResult(seed=seed, reason="projected")

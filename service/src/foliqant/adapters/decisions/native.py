"""Project native decisions across the Pydantic adapter boundary."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Never

from foliqant_decisions import (
    ChoiceResult,
    DecisionInput,
    DecisionOutput,
    DecisionResult,
    OrdinalResult,
    PredicateResult,
    validate_decision_output,
)
from pydantic import ValidationError

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import DecisionQuestionPlan, DecisionStepPlan


@dataclass(frozen=True, slots=True)
class ValidatedDecision:
    """Pydantic-free decision facts consumed by the workflow engine."""

    value: FrozenJson
    answerable: bool
    route_key: str | None


def _fail(code: ErrorCode) -> Never:
    raise ServiceError(code) from None


def _question_value(question: DecisionQuestionPlan) -> dict[str, object]:
    value: dict[str, object] = {
        "id": question.id,
        "type": question.type,
        "prompt": question.prompt,
        "criteria": list(question.criteria),
        "allowedSourceIds": list(question.allowed_source_ids),
    }
    options = [{"id": option.id, "description": option.description} for option in question.options]
    if question.type == "choice":
        value["options"] = options
    elif question.type == "multiselect":
        if question.min_selections is None or question.max_selections is None:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        value.update(
            options=options,
            minSelections=question.min_selections,
            maxSelections=question.max_selections,
        )
    elif question.type == "ordinal":
        value["levels"] = options
    elif question.type == "request_units":
        if question.allow_no_match is None:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        value.update(catalog=options, allowNoMatch=question.allow_no_match)
    return value


def build_decision_input(step: DecisionStepPlan, sources: FrozenObject) -> DecisionInput:
    """Build the canonical input from resolved source text and every compiled question.

    The first service authoring slice has no source-kind field. Its resolved
    string sources are therefore canonical native ``document`` sources.
    """

    expected_source_ids = tuple(source_id for source_id, _binding in step.sources)
    if set(sources) != set(expected_source_ids) or len(sources) != len(expected_source_ids):
        _fail(ErrorCode.INVALID_INPUT)

    native_sources: list[dict[str, str]] = []
    for source_id in expected_source_ids:
        text = sources[source_id]
        if not isinstance(text, str) or not text.strip():
            _fail(ErrorCode.INVALID_INPUT)
        native_sources.append({"id": source_id, "kind": "document", "text": text})

    try:
        task = DecisionInput.model_validate(
            {
                "schemaVersion": 1,
                "state": {"sources": native_sources},
                "questions": [_question_value(question) for question in step.questions],
            },
            strict=True,
        )
    except ValidationError:
        _fail(ErrorCode.INVALID_CONFIGURATION)
    return task


def _route_key(item: DecisionResult) -> str | None:
    if isinstance(item, ChoiceResult):
        return None if item.answer is None else item.answer.optionId
    if isinstance(item, OrdinalResult):
        return None if item.answer is None else item.answer.levelId
    if isinstance(item, PredicateResult):
        value = item.answer.value
        return value if value in {"true", "false"} else None
    return None


def _validate_output(raw_output: object) -> DecisionOutput:
    """Revalidate models and reject non-JSON objects at the external boundary."""

    if type(raw_output) is DecisionOutput:
        try:
            serialized = raw_output.model_dump_json(by_alias=True, warnings=False)
            return DecisionOutput.model_validate_json(serialized, strict=True)
        except (TypeError, ValueError):
            _fail(ErrorCode.INVALID_OUTPUT)

    try:
        frozen = freeze_json(raw_output)
    except ServiceError:
        _fail(ErrorCode.INVALID_OUTPUT)
    if not isinstance(frozen, Mapping):
        _fail(ErrorCode.INVALID_OUTPUT)
    try:
        return DecisionOutput.model_validate(thaw_json(frozen), strict=True)
    except (TypeError, ValueError):
        _fail(ErrorCode.INVALID_OUTPUT)


def validate_decision_result(
    step: DecisionStepPlan,
    task: DecisionInput,
    raw_output: object,
) -> ValidatedDecision:
    """Validate native structure and semantics, then return immutable route facts."""

    output = _validate_output(raw_output)

    if validate_decision_output(task, output):
        _fail(ErrorCode.INVALID_OUTPUT)

    statuses = tuple(item.answerability.status for item in output.results)
    answerable = bool(statuses) and all(status == "answerable" for status in statuses)
    if step.question_mode == "single":
        if len(output.results) != 1:
            _fail(ErrorCode.INVALID_OUTPUT)
        item = output.results[0]
        route_key = _route_key(item) if answerable else None
        dumped: object = item.model_dump(mode="json", by_alias=True)
    else:
        route_key = None
        dumped = output.model_dump(mode="json", by_alias=True)

    try:
        value = freeze_json(dumped)
    except ServiceError:
        _fail(ErrorCode.INVALID_OUTPUT)
    if not isinstance(value, Mapping):
        _fail(ErrorCode.INVALID_OUTPUT)
    return ValidatedDecision(value=value, answerable=answerable, route_key=route_key)

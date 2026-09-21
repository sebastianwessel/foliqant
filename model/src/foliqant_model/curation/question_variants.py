"""Deterministic question variants over an unchanged native decision state."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from foliqant.decisions import (
    Answerability,
    ChoiceAnswer,
    ChoiceQuestion,
    ChoiceResult,
    DecisionInput,
    DecisionOutput,
    Explanation,
    MultiselectQuestion,
    MultiselectResult,
    validate_decision_output,
)

from ..contracts.base import canonical_digest
from ..contracts.inputs import ChatMessage, DataRecord

QUESTION_VARIANT_RECIPE_VERSION = "question-variants-v2"

_ISOLATED_MULTISELECT = "isolated-multiselect"
_SINGLE_CHOICE = "single-choice-cardinality"
_LOCALIZED_CHOICE = {
    "en": {
        "prompt": (
            "Determine which options apply under the original question below, then return one "
            "option only if exactly one applies. Treat the original select-all wording as the "
            "applicability policy, not as the output cardinality. Original question: {prompt}"
        ),
        "single": "The allowed source evidence supports exactly one option.",
        "multiple": (
            "The allowed source evidence supports multiple options, so no single option can be "
            "chosen."
        ),
    },
    "de": {
        "prompt": (
            "Bestimmen Sie anhand der folgenden ursprünglichen Frage, welche Optionen zutreffen, "
            "und geben Sie nur dann eine Option zurück, wenn genau eine zutrifft. Die "
            "ursprüngliche Mehrfachauswahl beschreibt die Anwendbarkeitsregeln, nicht die "
            "Ausgabekardinalität. "
            "Ursprüngliche Frage: {prompt}"
        ),
        "single": "Die zulässigen Quellenbelege stützen genau eine Option.",
        "multiple": (
            "Die zulässigen Quellenbelege stützen mehrere Optionen, daher kann keine einzelne "
            "Option ausgewählt werden."
        ),
    },
}


@dataclass(frozen=True)
class QuestionVariant:
    """One deterministic derivative and its explicit parent-sidecar metadata."""

    record: DataRecord
    parentRecordId: str
    questionId: str
    rule: str


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _variant_record(
    parent: DataRecord,
    *,
    task: DecisionInput,
    output: DecisionOutput,
    question_id: str,
    rule: str,
) -> DataRecord:
    identity = {
        "parentRecordId": parent.id,
        "questionId": question_id,
        "recipeVersion": QUESTION_VARIANT_RECIPE_VERSION,
        "rule": rule,
        "task": task.model_dump(mode="json"),
        "output": output.model_dump(mode="json"),
    }
    record_id = f"question-variant.{canonical_digest(identity)[:32]}"
    tags = [
        tag
        for tag in parent.tags
        if not tag.startswith(("answerability:", "question-type:", "question-variant"))
    ]
    question = task.questions[0]
    result = output.results[0]
    tags.extend(
        [
            "question-variant",
            f"question-variant-recipe:{QUESTION_VARIANT_RECIPE_VERSION}",
            f"question-variant-rule:{rule}",
            f"question-type:{question.type}",
            f"answerability:{result.answerability.status}",
        ]
    )
    messages = [
        *parent.messages[:-2],
        ChatMessage(role="user", content=_json_text(task.model_dump(mode="json"))),
        ChatMessage(role="assistant", content=_json_text(output.model_dump(mode="json"))),
    ]
    return parent.model_copy(
        update={
            "id": record_id,
            "messages": messages,
            "tags": tags,
            "reviewed": False,
            "generation": None,
        }
    )


def _choice_result(result: MultiselectResult, *, language: str) -> ChoiceResult:
    assert result.answer is not None
    selected = result.answer.optionIds
    localized = _LOCALIZED_CHOICE[language]
    if len(selected) == 1:
        answerability = Answerability(status="answerable", issues=[])
        answer = ChoiceAnswer(optionId=selected[0])
        summary = localized["single"]
    else:
        answerability = Answerability(status="not_answerable", issues=["multiple_valid_options"])
        answer = None
        summary = localized["multiple"]
    return ChoiceResult(
        questionId=result.questionId,
        type="choice",
        answerability=answerability,
        answer=answer,
        explanation=Explanation(
            summary=summary,
            evidence=result.explanation.evidence,
            contraryEvidence=result.explanation.contraryEvidence,
            missingFacts=result.explanation.missingFacts,
        ),
    )


def derive_question_variants(record: DataRecord) -> list[QuestionVariant]:
    """Derive isolated multiselect and cardinality-one choice tasks without inference."""

    if record.language not in _LOCALIZED_CHOICE or "question-variant" in record.tags:
        return []
    try:
        task = DecisionInput.model_validate_json(record.messages[-2].content, strict=True)
        output = DecisionOutput.model_validate_json(record.messages[-1].content, strict=True)
    except (ValidationError, ValueError, TypeError):
        return []
    if validate_decision_output(task, output):
        return []

    results = {result.questionId: result for result in output.results}
    variants: list[QuestionVariant] = []
    for question in task.questions:
        result = results[question.id]
        if not isinstance(question, MultiselectQuestion) or not isinstance(
            result, MultiselectResult
        ):
            continue
        if (
            len(question.options) < 2
            or result.answerability.status != "answerable"
            or result.answer is None
            or not result.answer.optionIds
        ):
            continue

        if len(task.questions) > 1:
            multiselect_task = DecisionInput(state=task.state, questions=[question])
            multiselect_output = DecisionOutput(results=[result])
            multiselect_record = _variant_record(
                record,
                task=multiselect_task,
                output=multiselect_output,
                question_id=question.id,
                rule=_ISOLATED_MULTISELECT,
            )
            variants.append(
                QuestionVariant(
                    record=multiselect_record,
                    parentRecordId=record.id,
                    questionId=question.id,
                    rule=_ISOLATED_MULTISELECT,
                )
            )

        localized = _LOCALIZED_CHOICE[record.language]
        choice_question = ChoiceQuestion(
            id=question.id,
            type="choice",
            prompt=localized["prompt"].format(prompt=question.prompt),
            criteria=question.criteria,
            allowedSourceIds=question.allowedSourceIds,
            options=question.options,
        )
        choice_task = DecisionInput(state=task.state, questions=[choice_question])
        choice_output = DecisionOutput(results=[_choice_result(result, language=record.language)])
        choice_record = _variant_record(
            record,
            task=choice_task,
            output=choice_output,
            question_id=question.id,
            rule=_SINGLE_CHOICE,
        )
        variants.append(
            QuestionVariant(
                record=choice_record,
                parentRecordId=record.id,
                questionId=question.id,
                rule=_SINGLE_CHOICE,
            )
        )

    return variants

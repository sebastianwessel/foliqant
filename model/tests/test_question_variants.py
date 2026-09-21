from __future__ import annotations

import json

from foliqant_decisions import (
    ChoiceResult,
    DecisionInput,
    DecisionOutput,
    MultiselectResult,
    validate_decision_output,
)

from foliqant_model.contracts.inputs import DataRecord, GenerationProvenance
from foliqant_model.curation.question_variants import (
    QUESTION_VARIANT_RECIPE_VERSION,
    derive_question_variants,
)


def _text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _record(*, language: str = "en", selected: list[str] | None = None) -> DataRecord:
    selected = selected or ["transfer", "statement"]
    source_text = (
        "Bitte überweisen Sie das Geld und senden Sie den Kontoauszug."
        if language == "de"
        else "Please transfer the money and send the statement."
    )
    multiselect_prompt = (
        "Wählen Sie alle zutreffenden Anliegen."
        if language == "de"
        else "Select every applicable request."
    )
    multiselect_summary = (
        "Die Annotation belegt alle ausgewählten Anliegen."
        if language == "de"
        else "The annotation supports all selected requests."
    )
    task = {
        "schemaVersion": 1,
        "state": {"sources": [{"id": "message-1", "kind": "message", "text": source_text}]},
        "questions": [
            {
                "id": "other",
                "type": "predicate",
                "prompt": "Is there a request?",
                "criteria": ["The message explicitly contains a request."],
                "allowedSourceIds": ["message-1"],
            },
            {
                "id": "requests",
                "type": "multiselect",
                "prompt": multiselect_prompt,
                "criteria": [multiselect_prompt],
                "allowedSourceIds": ["message-1"],
                "options": [
                    {"id": "transfer", "description": "Transfer money"},
                    {"id": "statement", "description": "Send a statement"},
                    {"id": "address", "description": "Update an address"},
                ],
                "minSelections": 1,
                "maxSelections": 3,
            },
        ],
    }
    output = {
        "schemaVersion": 1,
        "results": [
            {
                "questionId": "other",
                "type": "predicate",
                "answer": {"value": "true"},
                "answerability": {"status": "answerable", "issues": []},
                "explanation": {
                    "summary": "The request is explicit.",
                    "evidence": [{"sourceId": "message-1", "quote": source_text}],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            },
            {
                "questionId": "requests",
                "type": "multiselect",
                "answer": {"optionIds": selected},
                "answerability": {"status": "answerable", "issues": []},
                "explanation": {
                    "summary": multiselect_summary,
                    "evidence": [{"sourceId": "message-1", "quote": source_text}],
                    "contraryEvidence": [],
                    "missingFacts": [],
                },
            },
        ],
    }
    digest = "a" * 64
    return DataRecord(
        schemaVersion=1,
        id=f"parent-{language}",
        sourceId="multidogo-finance",
        language=language,
        groupKeys=["conversation:1"],
        messages=[
            {"role": "system", "content": "Return the requested decision JSON."},
            {"role": "user", "content": _text(task)},
            {"role": "assistant", "content": _text(output)},
        ],
        tags=["native-decision", "question-type:predicate", "question-type:multiselect"],
        origin="synthetic",
        reviewed=False,
        familyId="conversation-1",
        generation=GenerationProvenance(
            provider="openai-compatible",
            modelId="test-model",
            modelIdentitySha256=digest,
            promptSha256=digest,
            parametersSha256=digest,
            requestSha256=digest,
            parentRecordIds=["source-parent"],
        ),
    )


def _parsed(variant: DataRecord) -> tuple[DecisionInput, DecisionOutput]:
    task = DecisionInput.model_validate_json(variant.messages[-2].content, strict=True)
    output = DecisionOutput.model_validate_json(variant.messages[-1].content, strict=True)
    assert validate_decision_output(task, output) == []
    return task, output


def _single_question_record() -> DataRecord:
    record = _record()
    task = DecisionInput.model_validate_json(record.messages[-2].content, strict=True)
    output = DecisionOutput.model_validate_json(record.messages[-1].content, strict=True)
    question = next(question for question in task.questions if question.id == "requests")
    result = next(result for result in output.results if result.questionId == "requests")
    single_task = DecisionInput(state=task.state, questions=[question])
    single_output = DecisionOutput(results=[result])
    return record.model_copy(
        update={
            "messages": [
                *record.messages[:-2],
                record.messages[-2].model_copy(
                    update={"content": _text(single_task.model_dump(mode="json"))}
                ),
                record.messages[-1].model_copy(
                    update={"content": _text(single_output.model_dump(mode="json"))}
                ),
            ]
        }
    )


def test_multi_label_annotation_yields_isolated_multiselect_and_unanswerable_choice() -> None:
    parent = _record()
    original_task = DecisionInput.model_validate_json(parent.messages[-2].content, strict=True)

    variants = derive_question_variants(parent)

    assert [variant.rule for variant in variants] == [
        "isolated-multiselect",
        "single-choice-cardinality",
    ]
    assert all(variant.parentRecordId == parent.id for variant in variants)
    assert all(variant.questionId == "requests" for variant in variants)
    assert variants == derive_question_variants(parent)
    assert variants[0].record.id != variants[1].record.id

    multiselect_task, multiselect_output = _parsed(variants[0].record)
    choice_task, choice_output = _parsed(variants[1].record)
    assert multiselect_task.state == choice_task.state == original_task.state
    assert len(multiselect_task.questions) == len(choice_task.questions) == 1
    assert isinstance(multiselect_output.results[0], MultiselectResult)
    assert multiselect_output.results[0].answer is not None
    assert multiselect_output.results[0].answer.optionIds == ["transfer", "statement"]
    choice_result = choice_output.results[0]
    assert isinstance(choice_result, ChoiceResult)
    assert choice_result.answer is None
    assert choice_result.answerability.status == "not_answerable"
    assert choice_result.answerability.issues == ["multiple_valid_options"]
    assert choice_result.explanation.evidence == multiselect_output.results[0].explanation.evidence
    assert multiselect_task.questions[0].prompt in choice_task.questions[0].prompt
    assert choice_task.questions[0].criteria == multiselect_task.questions[0].criteria


def test_single_question_parent_is_the_multiselect_half_and_only_choice_is_derived() -> None:
    parent = _single_question_record()

    variants = derive_question_variants(parent)

    assert [variant.rule for variant in variants] == ["single-choice-cardinality"]
    choice_task, choice_output = _parsed(variants[0].record)
    assert (
        choice_task.state
        == DecisionInput.model_validate_json(parent.messages[-2].content, strict=True).state
    )
    assert isinstance(choice_output.results[0], ChoiceResult)


def test_single_label_annotation_yields_answerable_german_choice_with_english_enums() -> None:
    variants = derive_question_variants(_record(language="de", selected=["statement"]))
    choice_task, choice_output = _parsed(variants[1].record)
    choice_result = choice_output.results[0]

    assert "Ursprüngliche Frage: Wählen Sie alle zutreffenden Anliegen." in (
        choice_task.questions[0].prompt
    )
    assert isinstance(choice_result, ChoiceResult) and choice_result.answer is not None
    assert choice_result.answer.optionId == "statement"
    assert choice_result.answerability.status == "answerable"
    assert choice_result.answerability.issues == []
    assert choice_result.explanation.summary == (
        "Die zulässigen Quellenbelege stützen genau eine Option."
    )


def test_derivatives_preserve_source_family_and_rights_keys_but_drop_generation() -> None:
    parent = _record()

    for variant in derive_question_variants(parent):
        derivative = variant.record
        assert derivative.sourceId == parent.sourceId
        assert derivative.familyId == parent.familyId
        assert derivative.groupKeys == parent.groupKeys
        assert derivative.language == parent.language
        assert derivative.origin == parent.origin
        assert derivative.reviewed is False
        assert derivative.generation is None
        assert "question-variant" in derivative.tags
        assert f"question-variant-recipe:{QUESTION_VARIANT_RECIPE_VERSION}" in derivative.tags
        assert sum(tag.startswith("question-type:") for tag in derivative.tags) == 1
        assert sum(tag.startswith("answerability:") for tag in derivative.tags) == 1


def test_invalid_incomplete_or_already_derived_records_are_not_transformed() -> None:
    unsupported = _record().model_copy(update={"language": "fr"})
    already_derived = _record().model_copy(update={"tags": [*_record().tags, "question-variant"]})
    malformed = _record().model_copy(
        update={
            "messages": [
                *_record().messages[:-1],
                _record().messages[-1].model_copy(update={"content": "{}"}),
            ]
        }
    )

    assert derive_question_variants(unsupported) == []
    assert derive_question_variants(already_derived) == []
    assert derive_question_variants(malformed) == []

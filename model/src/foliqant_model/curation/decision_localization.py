"""Exact authored prose localization without changing decision contracts or labels."""

from __future__ import annotations

from collections.abc import Mapping

from foliqant.decisions import (
    ChoiceQuestion,
    Citation,
    DecisionInput,
    DecisionOutput,
    MultiselectQuestion,
    OrdinalQuestion,
    RequestUnitsQuestion,
    RequestUnitsResult,
    validate_decision_output,
)


def localize_case(
    task: DecisionInput, oracle: DecisionOutput, translations: Mapping[str, str]
) -> tuple[DecisionInput, DecisionOutput]:
    """Localize explicit human-facing fields using an exhaustive authored catalog.

    Unknown prose fails closed. IDs, enums and answers are never translated.
    Citation spans are remapped to exact localized evidence; a full-source quote
    is used when there is no separately authored translation of a substring.
    The original case is not mutated.
    """

    if validate_decision_output(task, oracle):
        raise ValueError("Authored decision has invalid evidence or semantics")

    def prose(value: str) -> str:
        translated = translations.get(value)
        if not translated or not translated.strip():
            raise ValueError("Authored decision prose lacks a complete localization")
        return translated

    localized_task = task.model_copy(deep=True)
    localized_oracle = oracle.model_copy(deep=True)
    for source in localized_task.state.sources:
        source.text = prose(source.text)
    sources = {source.id: source.text for source in localized_task.state.sources}
    for question in localized_task.questions:
        question.prompt = prose(question.prompt)
        question.criteria = [prose(value) for value in question.criteria]
        if isinstance(question, (ChoiceQuestion, MultiselectQuestion)):
            options = question.options
        elif isinstance(question, OrdinalQuestion):
            options = question.levels
        elif isinstance(question, RequestUnitsQuestion):
            options = question.catalog
        else:
            options = []
        for option in options:
            option.description = prose(option.description)

    def citation(value: Citation) -> Citation:
        source = sources[value.sourceId]
        if value.quote in translations:
            translated_quote = translations[value.quote]
            if not translated_quote.strip() or translated_quote not in source:
                raise ValueError("Explicit localized quote is not grounded in its source")
            quote = translated_quote
        elif value.quote in source:
            quote = value.quote
        else:
            quote = source
        return Citation(sourceId=value.sourceId, quote=quote)

    for result in localized_oracle.results:
        explanation = result.explanation
        explanation.summary = prose(explanation.summary)
        explanation.missingFacts = [prose(value) for value in explanation.missingFacts]
        explanation.evidence = [citation(value) for value in explanation.evidence]
        explanation.contraryEvidence = [citation(value) for value in explanation.contraryEvidence]
        if isinstance(result, RequestUnitsResult) and result.answer is not None:
            for unit in result.answer.units:
                unit.description = prose(unit.description)
                unit.evidence = [citation(value) for value in unit.evidence]
                if unit.subject is not None:
                    unit.subject = translations.get(unit.subject, unit.subject)

    # Assignment does not rerun Pydantic field bounds; validate the whole result
    # again, then require exactly grounded subjects, citations and relationships.
    localized_task = DecisionInput.model_validate(localized_task.model_dump(mode="python"))
    localized_oracle = DecisionOutput.model_validate(localized_oracle.model_dump(mode="python"))
    if validate_decision_output(localized_task, localized_oracle):
        raise ValueError("Localized authored decision has invalid evidence or semantics")
    return localized_task, localized_oracle

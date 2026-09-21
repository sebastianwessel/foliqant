"""German research cases preserve contracts, evidence, and numeric facts."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from foliqant.decisions import (
    DecisionInput,
    DecisionOutput,
    RequestUnitsResult,
    semantic_signature,
    validate_decision_output,
)
from foliqant_model.curation.decision_adequacy_cases import build_adequacy_case
from foliqant_model.curation.decision_german_research import (
    ADEQUACY_TRANSLATIONS,
    RESEARCH_TRANSLATIONS,
)
from foliqant_model.curation.decision_research_cases import build_research_case

_RESEARCH_SCENARIOS = (
    "mixed-sufficiency",
    "changed-deadline",
    "fund-ratio-answerable",
    "fund-ratio-missing",
    "legal-applicability-missing",
    "irrelevant-evidence",
)
_ADEQUACY_SCENARIOS = (
    "adequacy-complete",
    "adequacy-omitted-request",
    "adequacy-unsupported-evidence",
    "adequacy-correct-unknown",
)
_NUMBER = re.compile(r"(?<![A-Za-z0-9.,])[-+]?\d+(?:[.,]\d+)*(?![A-Za-z0-9]|[.,]\d)")


def _contract_shape(task: DecisionInput) -> object:
    return {
        "schemaVersion": task.schemaVersion,
        "sources": [(source.id, source.kind) for source in task.state.sources],
        "questions": [
            {
                "id": question.id,
                "type": question.type,
                "allowedSourceIds": question.allowedSourceIds,
                "optionIds": [
                    option.id
                    for option in (
                        getattr(question, "options", None)
                        or getattr(question, "catalog", None)
                        or getattr(question, "levels", None)
                        or []
                    )
                ],
            }
            for question in task.questions
        ],
    }


def _prose(task: DecisionInput, output: DecisionOutput) -> list[str]:
    values = [source.text for source in task.state.sources]
    for question in task.questions:
        values.extend([question.prompt, *question.criteria])
        options = (
            getattr(question, "options", None)
            or getattr(question, "catalog", None)
            or getattr(question, "levels", None)
            or []
        )
        values.extend(option.description for option in options)
    for result in output.results:
        values.extend([result.explanation.summary, *result.explanation.missingFacts])
        if isinstance(result, RequestUnitsResult) and result.answer is not None:
            for unit in result.answer.units:
                values.append(unit.description)
                if unit.subject is not None:
                    values.append(unit.subject)
    return values


def _assert_grounded(task: DecisionInput, output: DecisionOutput) -> None:
    assert validate_decision_output(task, output) == []
    sources = {source.id: source.text for source in task.state.sources}
    for result in output.results:
        citations = result.explanation.evidence + result.explanation.contraryEvidence
        for citation in citations:
            assert citation.quote in sources[citation.sourceId]
        if isinstance(result, RequestUnitsResult) and result.answer is not None:
            for unit in result.answer.units:
                for citation in unit.evidence:
                    assert citation.quote in sources[citation.sourceId]


def _assert_localized_pair(
    english: tuple[DecisionInput, DecisionOutput],
    german: tuple[DecisionInput, DecisionOutput],
) -> None:
    english_task, english_output = english
    german_task, german_output = german
    _assert_grounded(german_task, german_output)
    assert _contract_shape(german_task) == _contract_shape(english_task)
    assert semantic_signature(german_output) == semantic_signature(english_output)
    assert len(_prose(german_task, german_output)) == len(_prose(english_task, english_output))
    for english_text, german_text in zip(
        _prose(english_task, english_output),
        _prose(german_task, german_output),
        strict=True,
    ):
        assert german_text != english_text
        assert Counter(_NUMBER.findall(german_text)) == Counter(_NUMBER.findall(english_text))


def test_all_german_research_variants_preserve_contracts_semantics_and_evidence() -> None:
    for scenario in _RESEARCH_SCENARIOS:
        for index in range(4):
            english = build_research_case(scenario, index)
            german = build_research_case(scenario, index, language="de")
            assert english is not None and german is not None
            _assert_localized_pair(english, german)


def test_all_german_adequacy_variants_preserve_contracts_semantics_and_evidence() -> None:
    for scenario in _ADEQUACY_SCENARIOS:
        for index in range(4):
            _assert_localized_pair(
                build_adequacy_case(scenario, index),
                build_adequacy_case(scenario, index, language="de"),
            )


def test_translation_catalogs_exactly_cover_all_authored_prose() -> None:
    research_prose: set[str] = set()
    for scenario in _RESEARCH_SCENARIOS:
        for index in range(4):
            built = build_research_case(scenario, index)
            assert built is not None
            research_prose.update(_prose(*built))
    adequacy_prose = {
        value
        for scenario in _ADEQUACY_SCENARIOS
        for index in range(4)
        for value in _prose(*build_adequacy_case(scenario, index))
    }
    assert set(RESEARCH_TRANSLATIONS) == research_prose
    assert set(ADEQUACY_TRANSLATIONS) == adequacy_prose


def test_german_catalog_contains_natural_domain_and_task_prose() -> None:
    mixed = build_research_case("mixed-sufficiency", 0, language="de")
    deadline = build_research_case("changed-deadline", 0, language="de")
    ratio = build_research_case("fund-ratio-answerable", 0, language="de")
    applicability = build_research_case("legal-applicability-missing", 0, language="de")
    adequacy_task, adequacy_output = build_adequacy_case(
        "adequacy-correct-unknown", 0, language="de"
    )
    assert mixed is not None and "Bitte erstatten" in mixed[0].state.sources[0].text
    assert deadline is not None and "Richtlinienversion" in deadline[0].state.sources[0].text
    assert ratio is not None and "Aufwand" in ratio[0].questions[0].prompt
    assert applicability is not None
    assert "interne Richtlinie" in applicability[0].questions[0].prompt
    adequacy_sources = {source.id: source.text for source in adequacy_task.state.sources}
    assert "Gewünschte Handlungen" in adequacy_sources["proposed-answer"]
    assert "Aufgabenvertrags" in adequacy_task.questions[0].prompt
    assert "Transaktionsreferenz" in adequacy_output.results[0].explanation.summary


def test_explicit_english_matches_the_unchanged_default_catalog() -> None:
    for scenario in _RESEARCH_SCENARIOS:
        for index in range(4):
            assert build_research_case(scenario, index, language="en") == build_research_case(
                scenario, index
            )
    for scenario in _ADEQUACY_SCENARIOS:
        for index in range(4):
            assert build_adequacy_case(scenario, index, language="en") == build_adequacy_case(
                scenario, index
            )


@pytest.mark.parametrize("language", ["", "fr", "de-DE"])
def test_supported_cases_reject_unowned_languages(language: str) -> None:
    with pytest.raises(ValueError, match="English and German"):
        build_research_case("mixed-sufficiency", 0, language=language)
    with pytest.raises(ValueError, match="English and German"):
        build_adequacy_case("adequacy-complete", 0, language=language)

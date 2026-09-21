from __future__ import annotations

import copy
import json

import pytest
from foliqant_decisions import (
    DecisionOutput,
    RequestUnitsResult,
    semantic_signature,
    validate_decision_output,
)

from foliqant_model.contracts import ChatMessage, DataRecord
from foliqant_model.curation.contracts import ImportedRecord
from foliqant_model.curation.decision_contracts import DecisionDataSettings
from foliqant_model.curation.decision_german_cases import GERMAN_TRANSLATIONS
from foliqant_model.curation.decision_seeds import (
    _authored_case,
    build_authored_seeds,
    project_source,
)

_BASE_SCENARIOS = (
    "choice-answerable",
    "choice-ambiguous",
    "choice-no-match",
    "choice-multiple-valid-options",
    "primary-rule",
    "multiselect",
    "predicate-true",
    "predicate-false",
    "predicate-unknown",
    "ordinal-answerable",
    "ordinal-missing",
    "ordinal-conflict",
    "requests-different",
    "requests-same",
    "requests-withdrawn",
    "requests-quoted",
    "requests-partial",
    "conditional-true",
    "conditional-false",
    "conditional-unresolved",
    "requests-dependent",
    "missing-source",
    "prompt-injection",
)


def _options(question: object) -> list[object]:
    for attribute in ("options", "catalog", "levels"):
        values = getattr(question, attribute, None)
        if values is not None:
            return list(values)
    return []


def _german_subject_signature(oracle: DecisionOutput) -> object:
    signature = copy.deepcopy(semantic_signature(oracle))
    assert isinstance(signature, dict)
    for result in signature["results"]:
        answer = result["answer"]
        if not isinstance(answer, dict) or "units" not in answer:
            continue
        for unit in answer["units"]:
            subject = unit["subject"]
            if subject in GERMAN_TRANSLATIONS:
                unit["subject"] = GERMAN_TRANSLATIONS[subject]
    return signature


@pytest.mark.parametrize("scenario", _BASE_SCENARIOS)
@pytest.mark.parametrize("index", range(4))
def test_base_authored_cases_have_exact_valid_german_semantic_twins(
    scenario: str, index: int
) -> None:
    english_task, english_oracle = _authored_case(scenario, index, "en")
    german_task, german_oracle = _authored_case(scenario, index, "de")

    assert validate_decision_output(german_task, german_oracle) == []
    assert semantic_signature(german_oracle) == _german_subject_signature(english_oracle)
    for english, german in zip(english_task.state.sources, german_task.state.sources, strict=True):
        assert german.text == GERMAN_TRANSLATIONS[english.text]
    for english, german in zip(english_task.questions, german_task.questions, strict=True):
        assert german.prompt == GERMAN_TRANSLATIONS[english.prompt]
        assert german.criteria == [GERMAN_TRANSLATIONS[value] for value in english.criteria]
        assert [option.description for option in _options(german)] == [
            GERMAN_TRANSLATIONS[option.description] for option in _options(english)
        ]
    for english, german in zip(english_oracle.results, german_oracle.results, strict=True):
        assert german.explanation.summary == GERMAN_TRANSLATIONS[english.explanation.summary]
        assert german.explanation.missingFacts == [
            GERMAN_TRANSLATIONS[value] for value in english.explanation.missingFacts
        ]
        if isinstance(english, RequestUnitsResult):
            assert isinstance(german, RequestUnitsResult)
            assert english.answer is not None and german.answer is not None
            for english_unit, german_unit in zip(
                english.answer.units, german.answer.units, strict=True
            ):
                assert german_unit.description == GERMAN_TRANSLATIONS[english_unit.description]
                if english_unit.subject in GERMAN_TRANSLATIONS:
                    assert german_unit.subject == GERMAN_TRANSLATIONS[english_unit.subject]
                else:
                    assert german_unit.subject == english_unit.subject


@pytest.mark.parametrize(
    ("scenario", "index", "expected"),
    [
        ("requests-withdrawn", 0, "die Anfrage, meinen Kontoauszug zu senden"),
        ("requests-withdrawn", 1, "die Anfrage, die Gebühr zu erstatten"),
        ("requests-withdrawn", 2, "die Anfrage, meine Adresse zu ändern"),
        ("requests-withdrawn", 3, "die Anfrage, die Gebühr zu erstatten"),
        ("requests-quoted", 0, "Ich bitte Sie nur, meine Adresse zu ändern"),
        ("requests-quoted", 1, "Ich bitte Sie nur, mir meinen Kontostand zu senden"),
        ("requests-quoted", 2, "Ich bitte Sie nur, meinen Kontostand zu senden"),
        ("requests-quoted", 3, "Ich bitte Sie nur, meinen Kontoauszug zu senden"),
        ("requests-partial", 0, "Der fehlende Anhang enthält eine weitere Anfrage"),
        ("requests-partial", 1, "Die fehlende zweite Seite enthält eine weitere Anfrage"),
        ("requests-partial", 2, "Die fehlende referenzierte E-Mail enthält eine weitere Anfrage"),
        (
            "requests-partial",
            3,
            "Das fehlende Sprachnachrichtenprotokoll enthält eine weitere Anfrage",
        ),
        ("requests-dependent", 2, "Die Anfrage, das Konto zu schließen"),
        ("requests-dependent", 3, "Die Anfrage, die Adressänderung zu aktivieren"),
    ],
)
def test_generated_german_request_sources_use_required_grammar(
    scenario: str, index: int, expected: str
) -> None:
    task, oracle = _authored_case(scenario, index, "de")

    assert expected in task.state.sources[0].text
    assert validate_decision_output(task, oracle) == []


def test_german_unresolved_referent_missing_fact_is_idiomatic() -> None:
    _task, oracle = _authored_case("choice-ambiguous", 0, "de")

    assert oracle.results[0].explanation.missingFacts == ["Worauf sich „das“ bezieht."]


def test_bilingual_authored_seeds_share_families_and_set_response_language() -> None:
    seeds = build_authored_seeds(
        DecisionDataSettings(examplesPerScenario=4), seed=47, languages=["en", "de"]
    )
    assert len(seeds) == 33 * 4 * 2
    by_template: dict[tuple[str, tuple[str, ...]], list[object]] = {}
    for seed in seeds:
        key = (seed.scenario, tuple(seed.parent.groupKeys))
        by_template.setdefault(key, []).append(seed)
        instruction = {
            "en": "response prose in English",
            "de": "response prose in German",
        }[seed.parent.language]
        assert instruction in seed.parent.messages[0].content
        assert validate_decision_output(seed.input, seed.oracle) == []

    assert len(by_template) == 33 * 4
    for paired in by_template.values():
        assert {seed.parent.language for seed in paired} == {"en", "de"}
        assert len({seed.parent.familyId for seed in paired}) == 1
        assert len({seed.parent.id for seed in paired}) == 2


def _source_row(
    source_id: str, *, user: object, assistant: object, original_id: str
) -> ImportedRecord:
    return ImportedRecord(
        record=DataRecord(
            schemaVersion=1,
            id=f"{source_id}.record.{original_id}",
            sourceId=source_id,
            language="de",
            groupKeys=[f"{source_id}:group:{original_id}"],
            messages=[
                ChatMessage(role="system", content="Quellaufgabe."),
                ChatMessage(
                    role="user",
                    content=json.dumps(user, sort_keys=True, separators=(",", ":")),
                ),
                ChatMessage(
                    role="assistant",
                    content=json.dumps(assistant, sort_keys=True, separators=(",", ":")),
                ),
            ],
            tags=[source_id],
            origin="human",
            reviewed=False,
            familyId=f"{source_id}.family.{original_id}",
        ),
        originalSplit="train",
        task="classification" if source_id == "banking77" else "entailment",
        originalId=original_id,
    )


def test_german_banking_projection_preserves_imported_request_and_labels() -> None:
    request = "Wie kann ich eine Erstattung beantragen?"
    labels = ["request_refund", "Refund_not_showing_up"]
    seed = project_source(
        _source_row(
            "banking77",
            user={"labels": labels, "request": request},
            assistant={"intent": labels[0]},
            original_id="de-banking",
        )
    )

    assert seed is not None
    assert seed.input.state.sources[0].text == request
    question = seed.input.questions[0]
    assert question.type == "choice"
    assert [option.id for option in question.options] == ["request_refund", "refund_not_showing_up"]
    assert "Eine Erstattung beantragen" in question.options[0].description
    assert "erwartete oder ausgestellte Erstattung" in question.options[1].description
    assert "source-label:request_refund" in seed.parent.tags
    result = seed.oracle.results[0]
    assert result.type == "choice" and result.answer is not None
    assert result.answer.optionId == "request_refund"
    assert "passende Kategorie" in question.prompt
    assert seed.oracle.results[0].explanation.evidence[0].quote == request
    assert "response prose in German" in seed.parent.messages[0].content
    assert validate_decision_output(seed.input, seed.oracle) == []


def test_german_wanli_projection_preserves_imported_claim_and_evidence() -> None:
    claim = "Die Überweisung wurde am Dienstag abgewickelt."
    evidence = "Die Überweisung wurde eingereicht."
    seed = project_source(
        _source_row(
            "wanli",
            user={"claim": claim, "evidence": evidence, "options": []},
            assistant={"label": "insufficient"},
            original_id="de-wanli",
        )
    )

    assert seed is not None
    assert seed.input.state.sources[0].text == evidence
    assert seed.input.state.sources[1].text == claim
    assert [source.id for source in seed.input.state.sources] == ["premise", "hypothesis"]
    question = seed.input.questions[0]
    assert question.type == "choice"
    assert "sprachliche Beziehung" in question.prompt
    assert [option.id for option in question.options] == ["entailment", "contradiction", "neutral"]
    assert "folgt weder" in question.options[2].description
    result = seed.oracle.results[0]
    assert result.type == "choice" and result.answer is not None
    assert result.answer.optionId == "neutral"
    assert result.answerability.status == "answerable"
    assert result.answerability.issues == []
    assert result.explanation.summary == (
        "Die Hypothese folgt weder aus der Prämisse noch widerspricht sie ihr."
    )
    assert result.explanation.missingFacts == []
    assert [(citation.sourceId, citation.quote) for citation in result.explanation.evidence] == [
        ("premise", evidence),
        ("hypothesis", claim),
    ]
    assert "source-label:insufficient" in seed.parent.tags
    assert "response prose in German" in seed.parent.messages[0].content
    assert validate_decision_output(seed.input, seed.oracle) == []

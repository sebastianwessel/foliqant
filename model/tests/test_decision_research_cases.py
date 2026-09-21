from __future__ import annotations

import pytest

from foliqant.decisions import (
    PredicateResult,
    semantic_signature,
    validate_decision_output,
)
from foliqant_model.contracts.base import canonical_digest
from foliqant_model.curation.decision_research_cases import build_research_case

_SCENARIOS = (
    "mixed-sufficiency",
    "changed-deadline",
    "fund-ratio-answerable",
    "fund-ratio-missing",
    "legal-applicability-missing",
    "irrelevant-evidence",
)


def test_research_catalog_has_four_valid_distinct_cases_per_scenario() -> None:
    for scenario in _SCENARIOS:
        tasks: set[str] = set()
        for index in range(4):
            built = build_research_case(scenario, index)
            assert built is not None
            task, output = built
            assert validate_decision_output(task, output) == []
            assert all(source.kind != "metadata" for source in task.state.sources)
            tasks.add(canonical_digest(task.model_dump(mode="json")))
        assert len(tasks) == 4
    assert build_research_case("unknown", 0) is None
    assert build_research_case("mixed-sufficiency", 4) is None


def test_ratio_pairs_share_questions_but_change_available_evidence() -> None:
    for index in range(4):
        answerable = build_research_case("fund-ratio-answerable", index)
        missing = build_research_case("fund-ratio-missing", index)
        assert answerable is not None and missing is not None
        answerable_task, answerable_output = answerable
        missing_task, missing_output = missing
        assert answerable_task.questions == missing_task.questions
        assert semantic_signature(answerable_output) != semantic_signature(missing_output)
        assert answerable_output.results[0].answerability.status == "answerable"
        assert missing_output.results[0].answerability.status == "not_answerable"


def test_mixed_sufficiency_distinguishes_explicit_false_from_unknown() -> None:
    for index in range(4):
        built = build_research_case("mixed-sufficiency", index)
        assert built is not None
        _task, output = built
        assert len(output.results) == 3
        reference = output.results[1]
        unavailable = output.results[2]
        assert isinstance(reference, PredicateResult)
        assert reference.answerability.status == "answerable"
        assert reference.answer.value == "false"
        assert isinstance(unavailable, PredicateResult)
        assert unavailable.answerability.status == "not_answerable"
        assert unavailable.answer.value == "unknown"
        assert unavailable.answerability.issues == ["missing_information"]


@pytest.mark.parametrize(
    ("index", "case_fact", "missing_facts"),
    [
        (
            0,
            "does not identify whether the subject is a firm",
            ["Whether the subject is a firm.", "Case jurisdiction.", "Case event date."],
        ),
        (1, "issued on August 4, 2026", ["Product rate type."]),
        (2, "customer is in Region B", ["Customer classification."]),
        (3, "submitted on October 3, 2026", ["Transfer channel."]),
    ],
)
def test_applicability_oracles_account_for_every_authored_condition(
    index: int, case_fact: str, missing_facts: list[str]
) -> None:
    built = build_research_case("legal-applicability-missing", index)
    assert built is not None
    task, output = built

    assert validate_decision_output(task, output) == []
    assert case_fact in task.state.sources[1].text
    result = output.results[0]
    assert isinstance(result, PredicateResult)
    assert result.answerability.status == "not_answerable"
    assert result.answerability.issues == ["missing_information"]
    assert result.answer.value == "unknown"
    assert result.explanation.missingFacts == missing_facts

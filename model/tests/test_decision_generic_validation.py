"""Domain-independent preservation and cross-field validation regressions."""

from __future__ import annotations

import pytest

from foliqant_model.curation.decision_contracts import (
    DecisionInput,
    DecisionOutput,
    DecisionSource,
    validate_decision_output,
)
from foliqant_model.curation.decision_generation import (
    _source_text_problem,
    validate_decision_rewrite,
)


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("The attachment is unavailable.", "The attachment cannot be accessed."),
        ("The device cannot start.", "The device can not start."),
        ("The motor does not run.", "The motor doesn't run."),
        ("The room is not open.", "The room isn’t open."),
        ("The repair was not completed.", "The repair wasn't completed."),
        ("The process will not finish.", "The process won't finish."),
        ("The file has not arrived.", "The file hasn’t arrived."),
        ("The result should not change.", "The result shouldn't change."),
        ("The package could not arrive.", "The package couldn't arrive."),
        ("The task must not run.", "The task mustn’t run."),
        ("The process need not stop.", "The process needn't stop."),
        ("The scanner cannot read the file.", "The scanner is unable to read the file."),
        ("The team is not able to finish.", "The team is unable to finish."),
    ],
)
def test_negation_spellings_preserve_marker_count(original: str, rewritten: str) -> None:
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None


@pytest.mark.parametrize(
    ("negative", "positive"),
    [
        ("The device cannot start.", "The device can start."),
        ("The room isn't open.", "The room is open."),
        ("The package won’t arrive.", "The package will arrive."),
        ("The attachment is unavailable.", "The attachment is available."),
        ("The result is not missing.", "The result is missing."),
        ("The scanner is unable to read.", "The scanner is able to read."),
        ("The scanner is not unable to read.", "The scanner is unable to read."),
    ],
)
def test_added_or_removed_negation_fails_in_both_directions(negative: str, positive: str) -> None:
    assert _source_text_problem(negative, positive) == "rewrite-negations-changed"
    assert _source_text_problem(positive, negative) == "rewrite-negations-changed"


def test_negation_does_not_match_substrings_of_unrelated_words() -> None:
    assert _source_text_problem("The notice concerns a cannon.", "A cannon is mentioned.") is None


@pytest.mark.parametrize("value", ["3", "11", "200"])
@pytest.mark.parametrize(
    ("prefix", "symbol", "suffix", "strict_prefix"),
    [
        ("at least", ">=", "or higher", "more than"),
        ("at least", ">=", "or more", "more than"),
        ("at least", ">=", "or above", "more than"),
        ("at most", "<=", "or lower", "less than"),
        ("at most", "<=", "or less", "less than"),
        ("at most", "<=", "or fewer", "fewer than"),
        ("at most", "<=", "or below", "less than"),
    ],
)
def test_inclusive_comparison_equivalence_preserves_the_boundary(
    value: str, prefix: str, symbol: str, suffix: str, strict_prefix: str
) -> None:
    original = f"The reading must be {prefix} {value}."
    rewritten = f"The required reading is {value} {suffix}."
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None
    symbolic = f"The required reading is {symbol} {value}."
    assert _source_text_problem(rewritten, symbolic) is None
    assert _source_text_problem(symbolic, rewritten) is None
    strict = f"The reading must be {strict_prefix} {value}."
    assert _source_text_problem(rewritten, strict) == "rewrite-comparisons-changed"
    assert _source_text_problem(strict, rewritten) == "rewrite-comparisons-changed"
    opposite = "or lower" if symbol == ">=" else "or higher"
    opposite_text = f"The required reading is {value} {opposite}."
    assert _source_text_problem(rewritten, opposite_text) == "rewrite-comparisons-changed"
    assert _source_text_problem(opposite_text, rewritten) == "rewrite-comparisons-changed"


@pytest.mark.parametrize(
    ("first", "second", "opposite"),
    [
        ("exceeds", "is greater than", "is less than"),
        ("is above", "is greater than", "is below"),
        ("is below", "is less than", "is above"),
    ],
)
def test_strict_comparison_aliases_preserve_operator_and_polarity(
    first: str, second: str, opposite: str
) -> None:
    original = f"The reading {first} 65 percent."
    rewritten = f"The reading {second} 65 percent."
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None
    opposite_text = f"The reading {opposite} 65 percent."
    assert _source_text_problem(original, opposite_text) == "rewrite-comparisons-changed"
    assert _source_text_problem(opposite_text, original) == "rewrite-comparisons-changed"


@pytest.mark.parametrize(
    ("strict", "inclusive"),
    [
        ("The reading exceeds 65 percent.", "The reading is at least 65 percent."),
        ("The reading is above 65 percent.", "The reading is 65 percent or above."),
        ("The reading is below 65 percent.", "The reading is at most 65 percent."),
        ("The reading is less than 65 percent.", "The reading is 65 percent or below."),
    ],
)
def test_strict_and_inclusive_comparisons_keep_distinct_boundaries(
    strict: str, inclusive: str
) -> None:
    assert _source_text_problem(strict, inclusive) == "rewrite-comparisons-changed"
    assert _source_text_problem(inclusive, strict) == "rewrite-comparisons-changed"


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("The result exceeds expectations.", "The result surpasses expectations."),
        ("Use the aboveboard approach.", "Use the honest approach."),
        ("The note is belowdecks.", "The note is in the lower deck."),
        ("Moreover, the result is ready.", "Also, the result is ready."),
    ],
)
def test_comparison_words_do_not_match_idioms_or_substrings(original: str, rewritten: str) -> None:
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None


def test_comparison_alias_does_not_hide_removed_negation() -> None:
    assert (
        _source_text_problem(
            "The reading does not exceed 65 percent.",
            "The reading is greater than 65 percent.",
        )
        == "rewrite-negations-changed"
    )


@pytest.mark.parametrize("amount", ["0.5", "7", "125"])
@pytest.mark.parametrize("marker", ["%", " %", " percent", " prozent"])
def test_percentage_markers_work_with_and_without_spacing(amount: str, marker: str) -> None:
    original = f"The measured rate is {amount}{marker}."
    assert _source_text_problem(original, f"The measured rate is {amount} percent.") is None
    assert _source_text_problem(original, f"The measured rate is {amount}.") == (
        "rewrite-units-changed"
    )
    assert _source_text_problem(original, f"The measured rate is {amount} bps.") == (
        "rewrite-units-changed"
    )


def _collection_task(kind: str) -> DecisionInput:
    question: dict[str, object] = {
        "id": "items",
        "type": kind,
        "prompt": "Identify all requested work.",
        "criteria": ["Use explicit requests only."],
        "allowedSourceIds": ["ticket"],
    }
    options = [{"id": "repair", "description": "Repair equipment"}]
    if kind == "multiselect":
        question.update(options=options, minSelections=0, maxSelections=1)
    else:
        question.update(catalog=options, allowNoMatch=True)
    return DecisionInput.model_validate(
        {
            "schemaVersion": 1,
            "state": {"sources": [{"id": "ticket", "kind": "message", "text": "Repair the pump."}]},
            "questions": [question],
        },
        strict=True,
    )


def _collection_output(
    kind: str, *, empty: bool = False, evidence: bool = True, partial: bool = True
) -> DecisionOutput:
    citation = {"sourceId": "ticket", "quote": "Repair the pump."}
    answer: dict[str, object]
    if kind == "multiselect":
        answer = {"optionIds": [] if empty else ["repair"]}
    else:
        answer = {
            "units": []
            if empty
            else [
                {
                    "id": "r1",
                    "status": "active",
                    "categoryId": "repair",
                    "subject": None,
                    "description": "Repair the pump.",
                    "evidence": [citation],
                }
            ],
            "relations": [],
        }
    return DecisionOutput.model_validate(
        {
            "schemaVersion": 1,
            "results": [
                {
                    "questionId": "items",
                    "type": kind,
                    "answerability": {
                        "status": "partially_answerable" if partial else "answerable",
                        "issues": ["missing_information"] if partial else [],
                    },
                    "answer": answer,
                    "explanation": {
                        "summary": "Only the visible requested work is extracted.",
                        "evidence": [citation] if evidence else [],
                        "contraryEvidence": [],
                        "missingFacts": ["The remaining requested work."] if partial else [],
                    },
                }
            ],
        },
        strict=True,
    )


@pytest.mark.parametrize("kind", ["multiselect", "request_units"])
def test_partial_collections_require_items_and_grounding(kind: str) -> None:
    task = _collection_task(kind)
    assert validate_decision_output(task, _collection_output(kind)) == []
    assert validate_decision_output(task, _collection_output(kind, empty=True)) == [
        "question:items:partial-answer-empty"
    ]
    assert validate_decision_output(task, _collection_output(kind, evidence=False)) == [
        "question:items:evidence-required"
    ]
    # A completed empty collection is structurally legal; a separate semantic
    # check must establish whether it is justified by the actual task evidence.
    assert validate_decision_output(task, _collection_output(kind, empty=True, partial=False)) == []


@pytest.mark.parametrize(
    "rewritten",
    ["Repair the pump.", "  Repair\n the pump. ", "REPAIR THE PUMP!", "Repair, the pump..."],
)
def test_noop_rewrites_are_rejected_without_a_phrase_blacklist(rewritten: str) -> None:
    original = _collection_task("request_units")
    candidate = original.model_copy(deep=True)
    candidate.state.sources[0].text = rewritten
    assert (
        validate_decision_rewrite(original, candidate, rewrite_source_ids=["ticket"])
        == "rewrite-no-wording-change"
    )


def test_noop_check_supports_unicode_and_one_changed_selected_source() -> None:
    original = _collection_task("request_units")
    original.state.sources[0].text = "Bitte das Gerät prüfen."
    original.state.sources.append(
        DecisionSource(id="other", kind="document", text="Die Prüfung erfolgt morgen.")
    )
    candidate = original.model_copy(deep=True)
    candidate.state.sources[0].text = "BITTE DAS GERÄT PRÜFEN!"
    assert (
        validate_decision_rewrite(original, candidate, rewrite_source_ids=["ticket"])
        == "rewrite-no-wording-change"
    )
    candidate.state.sources[0].text = "Das Gerät bitte prüfen."
    assert (
        validate_decision_rewrite(original, candidate, rewrite_source_ids=["ticket", "other"])
        is None
    )


def test_equal_negation_counts_are_not_a_semantic_guarantee() -> None:
    # These unrelated propositions share the same lexical markers. The guard
    # deliberately cannot prove entailment; the blind solver/oracle boundary is
    # still required. Do not turn this into a dataset-specific phrase rule.
    assert (
        _source_text_problem(
            "The fan cannot start; the pump can start.",
            "The fan can start; the pump cannot start.",
        )
        is None
    )

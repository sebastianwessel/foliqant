"""German lexical preservation regressions for native decision rewrites."""

from __future__ import annotations

import pytest

from foliqant_model.curation.decision_generation import _source_text_problem


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Die Anlage ist nicht verfügbar.", "Die Anlage ist unverfügbar."),
        ("Das ist nicht möglich.", "Das ist unmöglich."),
        ("Die Unterlage fehlte.", "Die Unterlage ist nicht vorhanden."),
        ("Die fehlenden Belege sind genannt.", "Die nicht vorhandenen Belege sind genannt."),
        ("Keinem Konto ist der Zugriff erlaubt.", "Der Zugriff ist für kein Konto erlaubt."),
        ("Das unfähige Gerät bleibt aus.", "Das nicht funktionsfähige Gerät bleibt aus."),
    ],
)
def test_german_negation_inflections_preserve_marker_count(original: str, rewritten: str) -> None:
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None


@pytest.mark.parametrize(
    ("negative", "positive"),
    [
        ("Die Anlage ist nicht verfügbar.", "Die Anlage ist verfügbar."),
        ("Keinem Konto ist der Zugriff erlaubt.", "Einem Konto ist der Zugriff erlaubt."),
        ("Die Unterlage fehlte.", "Die Unterlage lag vor."),
        ("Das Gerät ist unzugänglich.", "Das Gerät ist zugänglich."),
    ],
)
def test_german_removed_or_added_negation_is_rejected(negative: str, positive: str) -> None:
    assert _source_text_problem(negative, positive) == "rewrite-negations-changed"
    assert _source_text_problem(positive, negative) == "rewrite-negations-changed"


@pytest.mark.parametrize(
    ("prefix", "symbol", "suffix", "strict"),
    [
        ("mindestens", ">=", "oder mehr", "mehr als"),
        ("mindestens", ">=", "oder höher", "über"),
        ("mindestens", ">=", "oder darüber", "überschreitet"),
        ("höchstens", "<=", "oder weniger", "weniger als"),
        ("höchstens", "<=", "oder niedriger", "unter"),
        ("höchstens", "<=", "oder darunter", "unterschreitet"),
    ],
)
def test_german_inclusive_comparisons_preserve_equality_boundary(
    prefix: str, symbol: str, suffix: str, strict: str
) -> None:
    original = f"Der Wert beträgt {prefix} 65 Prozent."
    suffixed = f"Der Wert beträgt 65 Prozent {suffix}."
    symbolic = f"Der Wert beträgt {symbol} 65 Prozent."
    strict_text = f"Der Wert {strict} 65 Prozent."

    assert _source_text_problem(original, suffixed) is None
    assert _source_text_problem(suffixed, original) is None
    assert _source_text_problem(suffixed, symbolic) is None
    assert _source_text_problem(symbolic, suffixed) is None
    assert _source_text_problem(original, strict_text) == "rewrite-comparisons-changed"
    assert _source_text_problem(strict_text, original) == "rewrite-comparisons-changed"


@pytest.mark.parametrize(
    ("first", "second", "opposite"),
    [
        ("überschreitet", "ist größer als", "ist kleiner als"),
        ("liegt über", "beträgt mehr als", "liegt unter"),
        ("ist höher als", "ist größer als", "ist niedriger als"),
        ("liegt unter", "ist weniger als", "liegt über"),
        ("unterschreitet", "ist kleiner als", "überschreitet"),
    ],
)
def test_german_strict_comparison_aliases_preserve_operator_and_polarity(
    first: str, second: str, opposite: str
) -> None:
    original = f"Der Wert {first} 65 Prozent."
    rewritten = f"Der Wert {second} 65 Prozent."
    opposite_text = f"Der Wert {opposite} 65 Prozent."

    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None
    assert _source_text_problem(original, opposite_text) == "rewrite-comparisons-changed"
    assert _source_text_problem(opposite_text, original) == "rewrite-comparisons-changed"


def test_german_comparison_alias_does_not_hide_removed_negation() -> None:
    assert (
        _source_text_problem(
            "Der Wert überschreitet 65 Prozent nicht.",
            "Der Wert ist größer als 65 Prozent.",
        )
        == "rewrite-negations-changed"
    )


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Das Ergebnis überschreitet Erwartungen.", "Das Ergebnis übertrifft Erwartungen."),
        ("Die Überweisung ist vorgemerkt.", "Die Zahlung ist vorgemerkt."),
        ("Der Bericht ist unterwegs.", "Der Bericht befindet sich auf dem Weg."),
        ("Wir sprechen übermorgen.", "Unser Gespräch ist übermorgen geplant."),
        ("Der Fehler ist dokumentiert.", "Der Irrtum ist dokumentiert."),
    ],
)
def test_german_comparison_words_do_not_match_idioms_or_substrings(
    original: str, rewritten: str
) -> None:
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Eingang am 28.04.2026.", "Eingang am 28. April 2026."),
        ("Eingang am 01.01.2026.", "Eingang am 1. Jänner 2026."),
        ("Die Quote beträgt 65,5 %.", "Die Quote liegt bei 65,5 Prozent."),
        ("Die Abweichung beträgt 25 Basispunkte.", "Die Abweichung liegt bei 25 bps."),
        ("Die Frist endet in 5 Tagen.", "Binnen 5 Tage endet die Frist."),
        ("Die Gebühr beträgt 20 EUR pro Monat.", "Die monatliche Gebühr beträgt EUR 20."),
    ],
)
def test_german_dates_decimals_and_units_accept_bounded_equivalents(
    original: str, rewritten: str
) -> None:
    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(rewritten, original) is None


@pytest.mark.parametrize(
    ("original", "rewritten", "reason"),
    [
        ("Eingang am 04.05.2026.", "Eingang am 5. April 2026.", "rewrite-dates-changed"),
        (
            "Die Quote beträgt 65,5 Prozent.",
            "Die Quote beträgt 65.5 Prozent.",
            "rewrite-numbers-changed",
        ),
        ("Der Betrag ist 1.000,50 EUR.", "Der Betrag ist 1,000.50 EUR.", "rewrite-numbers-changed"),
        ("Die Frist beträgt 5 Monate.", "Die Frist beträgt 5 Jahre.", "rewrite-units-changed"),
        (
            "Die Prüfung erfolgt in einem Monat.",
            "Die Prüfung erfolgt monatlich.",
            "rewrite-units-changed",
        ),
    ],
)
def test_german_ambiguous_numeric_or_unit_changes_fail_closed(
    original: str, rewritten: str, reason: str
) -> None:
    assert _source_text_problem(original, rewritten) == reason


@pytest.mark.parametrize("left_quote,right_quote", [("„", "“"), ("»", "«")])
def test_german_quoted_anchors_are_preserved(left_quote: str, right_quote: str) -> None:
    original = f"Bitte {left_quote}Konto Müller{right_quote} prüfen."
    rewritten = f"Prüfen Sie {left_quote}Konto Müller{right_quote} bitte."
    changed = f"Prüfen Sie {left_quote}Konto Meier{right_quote} bitte."

    assert _source_text_problem(original, rewritten) is None
    assert _source_text_problem(original, changed) == "rewrite-quotes-changed"

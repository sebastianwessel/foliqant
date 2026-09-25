"""Offline groups preserve authored scalar identity and all recorded attempts."""

from dataclasses import FrozenInstanceError, replace

import pytest
from test_evaluation import result, variant

from foliqant.contracts.envelope import Envelope
from foliqant.evaluation import (
    EvaluationCase,
    EvaluationSuite,
    Expectation,
    MetricSpec,
    RegisteredScorer,
    evaluate,
)
from foliqant.evaluation.groups import group_report


def gold(metadata, *, custom=False):
    return EvaluationSuite(
        "groups",
        "1",
        tuple(
            EvaluationCase(
                str(index),
                Envelope.model_validate({"payload": index, "metadata": item}, strict=True),
                (
                    Expectation(
                        "label",
                        "/payload",
                        "a",
                        "custom" if custom else "exact",
                        "same" if custom else None,
                    ),
                ),
            )
            for index, item in enumerate(metadata)
        ),
    )


async def report_for(metadata, *, repeat=1, details=True):
    async def run(envelope):
        return result("a")

    return await evaluate(
        gold(metadata),
        variant(run),
        repeat=repeat,
        include_details=details,
        metrics=(MetricSpec("label", "/payload", "classification", ("a", "b")),),
    )


async def test_scalar_types_null_missing_and_first_occurrence_order_are_preserved():
    values = [True, 1, 1.0, "1", None, False, 0, 0.0, "true"]
    report = await report_for([{"segment": value} for value in values] + [{}])
    groups = group_report(report, input_pointer="/metadata/segment")
    assert len(groups) == 10
    assert [(type(group.value), group.value) for group in groups[:-1]] == [
        (type(value), value) for value in values
    ]
    assert all(group.value_present for group in groups[:-1])
    assert groups[-1].value is None
    assert groups[-1].value_present is False
    assert groups[4].value is None
    assert groups[4].value_present is True
    assert all(group.source_case_count == group.attempt_count == 1 for group in groups)
    assert sum(group.checks.total for group in groups) == report.checks.total
    assert sum(group.metrics[0].support for group in groups) == report.metrics[0].support
    with pytest.raises(FrozenInstanceError):
        groups[0].value = "mutated"
    encoded = groups[0].to_dict()
    assert encoded["value"] is True
    assert encoded["check_pass_rate"] == encoded["check_coverage"] == 1
    assert "elapsed_seconds" not in encoded
    encoded["metrics"][0]["labels"].append("changed")
    assert groups[0].metrics[0].labels == ("a", "b")


async def test_repeated_groups_keep_failures_unknown_usage_and_source_denominators():
    calls = 0

    async def run(envelope):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private failure")
        return result("a" if envelope.payload != 2 else "b")

    report = await evaluate(
        gold([{"language": "de"}, {"language": "en"}, {"language": "de"}]),
        variant(run),
        repeat=2,
        include_details=True,
        metrics=(MetricSpec("label", "/payload", "classification", ("a", "b")),),
    )
    groups = group_report(report, input_pointer="/metadata/language")
    assert calls == 6  # Grouping performed no new invocations.
    de, en = groups
    assert [group.value for group in groups] == ["de", "en"]
    assert (de.source_case_count, de.attempt_count, en.source_case_count, en.attempt_count) == (
        2,
        4,
        1,
        2,
    )
    assert (de.checks.passed, de.checks.failed, de.checks.errors) == (1, 2, 1)
    assert de.checks.pass_rate == 1 / 4
    assert de.metrics[0].source_support == 2
    assert de.metrics[0].support == 4
    assert de.metrics[0].errors == 1
    assert de.metrics[0].accuracy == 1 / 4
    assert de.metrics[0].coverage == 3 / 4
    assert de.latency.count == 4
    assert de.usage.input_tokens.unknown == 1
    assert de.usage.input_tokens.known_total == 0
    assert de.usage.input_tokens.total is None
    assert en.checks.pass_rate == en.metrics[0].accuracy == 1
    assert en.usage.input_tokens.total == 0


async def test_custom_scorers_are_not_reexecuted_to_group_existing_checks():
    scored = 0

    async def scorer(actual, expected):
        nonlocal scored
        scored += 1
        return actual == expected

    async def run(envelope):
        return result("a")

    report = await evaluate(
        gold([{"segment": "a"}], custom=True),
        variant(run),
        include_details=True,
        scorers=(RegisteredScorer("same", "1", scorer),),
    )
    grouped = group_report(report, input_pointer="/metadata/segment")
    assert grouped[0].checks.passed == 1
    assert scored == 1


async def test_pointer_escaping_and_array_indexing_reuse_runtime_resolution():
    report = await report_for([{"a/b~c": ["de"]}])
    groups = group_report(report, input_pointer="/metadata/a~1b~0c/0")
    assert groups[0].value == "de"
    absent = group_report(report, input_pointer="/metadata/a~1b~0c/01")
    assert absent[0].value_present is False


@pytest.mark.parametrize("pointer", ["metadata/language", "/bad~", "/bad~2", "/x" * 65, None, True])
async def test_malformed_pointers_fail(pointer):
    report = await report_for([{}])
    with pytest.raises(ValueError, match="pointer"):
        group_report(report, input_pointer=pointer)


@pytest.mark.parametrize("value", [{}, {"language": "de"}, [{"language": "de"}], [["de"]]])
async def test_non_scalar_groups_fail(value):
    report = await report_for([{"group": value}])
    with pytest.raises(ValueError, match="scalars"):
        group_report(report, input_pointer="/metadata/group")


async def test_array_values_form_overlapping_member_groups():
    report = await report_for(
        [
            {"tags": ["reply", "german"]},
            {"tags": ["reply", "reply"]},
            {"tags": []},
            {"tags": "reply"},
            {},
        ]
    )
    groups = group_report(report, input_pointer="/metadata/tags")
    summary = [(g.value_present, g.member, g.value, g.source_case_count) for g in groups]
    # Members count each attempt once per distinct element; an empty array joins no
    # group; a scalar with the same text stays a separate, non-member group.
    assert summary == [
        (True, True, "reply", 2),
        (True, True, "german", 1),
        (True, False, "reply", 1),
        (False, False, None, 1),
    ]
    assert groups[0].checks.total == 2
    assert (groups[0].case_pass_rate, groups[0].failure_rate) == (1.0, 0.0)
    assert groups[0].to_dict()["member"] is True
    assert groups[0].metrics[0].support == 2


async def test_grouping_requires_details_for_every_attempt():
    report = await report_for([{}], details=False)
    with pytest.raises(ValueError, match="detailed"):
        group_report(report, input_pointer="/metadata/language")
    detailed = await report_for([{}, {}])
    mixed = replace(detailed, cases=(detailed.cases[0], replace(detailed.cases[1], details=None)))
    with pytest.raises(ValueError, match="detailed"):
        group_report(mixed, input_pointer="/metadata/language")


async def test_inconsistent_repeated_source_groups_and_identities_are_rejected():
    report = await report_for([{"language": "de"}, {"language": "en"}])
    inconsistent = replace(
        report,
        repeat=2,
        source_case_count=1,
        cases=(report.cases[0], replace(report.cases[1], id=report.cases[0].id, repetition=2)),
    )
    with pytest.raises(ValueError, match="groups differ"):
        group_report(inconsistent, input_pointer="/metadata/language")
    repeated = await report_for([{"language": "de"}], repeat=2)
    with pytest.raises(ValueError, match="complete"):
        group_report(
            replace(repeated, cases=repeated.cases[:1]), input_pointer="/metadata/language"
        )
    with pytest.raises(ValueError, match="identity"):
        group_report(
            replace(repeated, cases=(repeated.cases[0], repeated.cases[0])),
            input_pointer="/metadata/language",
        )


async def test_group_without_matching_metric_gold_reports_zero_support():
    suite = EvaluationSuite(
        "excluded",
        "1",
        (
            EvaluationCase(
                "a",
                Envelope.model_validate({"payload": 0, "metadata": {"group": "matched"}}),
                (Expectation("label", "/payload", "a"),),
            ),
            EvaluationCase(
                "b",
                Envelope.model_validate({"payload": 1, "metadata": {"group": "excluded"}}),
                (Expectation("status", "/execution/status", "completed"),),
            ),
        ),
    )

    async def run(envelope):
        return result("a")

    report = await evaluate(
        suite,
        variant(run),
        include_details=True,
        repeat=2,
        metrics=(MetricSpec("label", "/payload", "classification", ("a",)),),
    )
    _, excluded = group_report(report, input_pointer="/metadata/group")
    assert excluded.metrics[0].support == excluded.metrics[0].source_support == 0
    assert excluded.metrics[0].excluded == 2
    assert excluded.metrics[0].source_excluded == 1
    assert excluded.metrics[0].accuracy is None
    assert excluded.metrics[0].coverage is None
    assert excluded.metrics[0].micro.f1 is None

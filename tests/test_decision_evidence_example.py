"""Focused decision gold is offline, family-separated, and shared by diagnostic tasks."""

from examples.decision_evidence import evaluate as example
from examples.decision_evidence.gold import DEVELOPMENT, VALIDATION

from foliqant import prepare_application
from foliqant.core.plan import DecisionStepPlan
from foliqant.evaluation.dataset import metric_specs, validate_targets
from foliqant.evaluation.metrics import validate_metrics


async def test_default_evidence_example_never_opens_a_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline checks must not initialize a model")

    monkeypatch.setattr(example, "open_application", forbidden)
    assert await example.run_evaluations() == {
        "ok": True,
        "mode": "offline_check",
        "cases": 17,
    }
    assert (await example.run_evaluations(validation=True))["cases"] == 4


def test_evidence_gold_has_valid_targets_and_family_isolation():
    prepared = prepare_application(example.CONFIG_PATH)
    step = prepared.plans["decision_evidence"].step("assess")
    assert isinstance(step, DecisionStepPlan)
    assert {question.type for question in step.questions} == {
        "choice",
        "multiselect",
        "predicate",
        "ordinal",
        "request_units",
    }
    development = example.dataset()
    validation = example.dataset(validation=True)
    for gold in (development, validation):
        validate_targets(gold, prepared)
        for spec in gold.suites:
            validate_metrics(gold.to_suite(spec), metric_specs(spec))
            assert all(metric.labels == ["limited", "strong", None] for metric in spec.metrics)
    assert {case.family for case in DEVELOPMENT}.isdisjoint(case.family for case in VALIDATION)
    assert {case.message for case in DEVELOPMENT}.isdisjoint(case.message for case in VALIDATION)
    assert len(development.suites[0].gold_cases) == 15
    assert len(validation.suites) == 1


def test_assessment_gold_distinguishes_support_completeness_and_actionability():
    gold = example.dataset()
    cases = {
        case.id: {check.name: check.expected for check in case.expectations}
        for case in gold.suites[0].gold_cases
    }
    empty = cases["no_action"]
    assert empty["selected_labels"] == []
    assert empty["no_requests"] == []
    assert empty["dispute_answer"] == {"value": "false"}
    assert empty["triage_answer"] is None
    assert all(empty[f"{name}_strength"] == "strong" for name in example.QUESTION_IDS)
    missing = cases["explicit_missing"]
    assert all(missing[f"{name}_strength"] == "strong" for name in example.QUESTION_IDS)
    assert missing["dispute_answer"] == {"value": "unknown"}
    assert missing["dispute_status"] == "not_answerable"
    partial = cases["partial_conflict"]
    assert partial["labels_status"] == partial["requests_status"] == "partially_answerable"
    assert partial["labels_strength"] == partial["requests_strength"] == "strong"
    assert cases["suggested_purpose"]["triage_strength"] == "strong"
    assert cases["suggested_purpose"]["requests_answer"] is None
    mixed = cases["mixed_purposes"]
    assert mixed["labels_strength"] == "strong"
    assert mixed["requests_strength"] == "strong"
    assert mixed["request_0_categoryId"] == "freeze_card"
    tentative = cases["tentative_document"]
    assert tentative["triage_strength"] == tentative["labels_strength"] == "limited"
    assert tentative["requests_answer"] is None
    assert tentative["requests_strength"] == "strong"
    assert tentative == cases["tentative_document_de"]
    tentative_mixed = cases["mixed_tentative_document"]
    assert tentative_mixed["triage_strength"] == tentative_mixed["labels_strength"] == "limited"
    assert tentative_mixed["requests_strength"] == "strong"
    assert tentative_mixed["request_0_categoryId"] == "freeze_card"
    assert cases["policy_inference"]["request_0_subject"] == "C-42"
    assert cases["direct"] == cases["direct_noise"] == cases["direct_repeated"]


def test_isolated_predicate_reuses_grouped_criterion_source_and_gold():
    prepared = prepare_application(example.CONFIG_PATH)
    grouped = prepared.plans["decision_evidence"].step("assess")
    isolated = prepared.plans["decision_predicate"].step("assess")
    assert isinstance(grouped, DecisionStepPlan)
    assert isinstance(isolated, DecisionStepPlan)
    assert isolated.questions[0].criteria == grouped.questions[2].criteria
    assert isolated.questions[0].type == grouped.questions[2].type == "predicate"
    gold = example.dataset()
    grouped_cases = {case.id: case for case in gold.suites[0].gold_cases}
    for case in gold.suites[1].gold_cases:
        source = grouped_cases[case.id]
        assert case.input == source.input
        expected = {check.name: check.expected for check in source.expectations}
        for check in case.expectations:
            if check.name == "dispute_identity":
                assert check.expected == isolated.questions[0].id
            elif check.name == "review":
                assert check.expected == (
                    "needs_review" if expected["dispute_status"] != "answerable" else "completed"
                )
            else:
                assert check.expected == expected[check.name]
        assert all("/results/" not in check.path for check in case.expectations)


async def test_request_count_scorer_rejects_additional_units_without_generated_id_gold():
    gold = example.dataset()
    suite = example.build_suite(gold, gold.suites[0])
    case = next(case for case in suite.cases if case.id == "direct")
    count = next(check for check in case.expectations if check.name == "request_unit_count")
    assert count.expected == 1
    scorer = next(scorer for scorer in example.SCORERS if scorer.name == count.scorer)
    assert await scorer.score(({},), count.expected)
    assert not await scorer.score(({}, {}), count.expected)
    assert not await scorer.score((), count.expected)
    assert not await scorer.score(None, count.expected)
    assert not any(
        check.comparison == "custom"
        for case in gold.suites[0].gold_cases
        for check in case.expectations
    )
    assert example.build_suite(gold, gold.suites[1]) == gold.to_suite(gold.suites[1])

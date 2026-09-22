"""Focused decision example has offline gold checks and explicit live opt-in."""

from examples.decision_evidence import evaluate as example

from foliqant import prepare_application
from foliqant.core.plan import DecisionStepPlan
from foliqant.evaluation.dataset import metric_specs
from foliqant.evaluation.metrics import validate_metrics


async def test_default_evidence_example_never_opens_a_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline checks must not initialize a model")

    monkeypatch.setattr(example, "open_application", forbidden)
    assert await example.run_evaluations() == {
        "ok": True,
        "mode": "offline_check",
        "cases": 4,
    }
    assert (await example.run_evaluations(validation=True))["cases"] == 2


def test_evidence_gold_covers_typed_questions_and_keeps_validation_separate():
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
        spec = gold.suites[0]
        validate_metrics(gold.to_suite(spec), metric_specs(spec))
        assert all(metric.labels == ["limited", "strong", None] for metric in spec.metrics)
    assert {case.input.payload["message"] for case in development.suites[0].gold_cases}.isdisjoint(
        {case.input.payload["message"] for case in validation.suites[0].gold_cases}
    )
    strength_gold = {
        check.expected
        for case in development.suites[0].gold_cases
        for check in case.expectations
        if check.path.endswith("/evidence_strength")
    }
    assert strength_gold == {None, "limited", "strong"}
    none_case = next(case for case in development.suites[0].gold_cases if case.id == "no_action")
    expected = {check.name: check.expected for check in none_case.expectations}
    assert expected["selected_labels"] == []
    assert expected["labels_strength"] == "strong"
    assert expected["dispute_answer"] == {"value": "false"}
    assert expected["dispute_strength"] == "strong"
    assert expected["triage_answer"] is None
    assert expected["triage_strength"] is None

"""Policy-origin measurements survive grouping with honest attempt denominators."""

import pytest
from examples.support_triage import offline
from examples.support_triage.evaluate import CONFIG_PATH, dataset, suite
from examples.support_triage.run import open_example

from foliqant import RuntimePlugins, prepare_application
from foliqant.evaluation import EvaluationVariant, evaluate
from foliqant.evaluation.dataset import metric_specs
from foliqant.evaluation.groups import group_report


async def test_grouped_origins_keep_repetition_missing_selection_and_execution_errors():
    prepared = prepare_application(CONFIG_PATH)
    invocations = 0
    async with open_example(
        prepared=prepared,
        environment=offline.ENVIRONMENT,
        plugins=RuntimePlugins(model_factory=offline.model_factory),
    ) as app:

        async def invoke(envelope):
            nonlocal invocations
            invocations += 1
            if invocations == 2:
                # The second English answerable attempt fails before returning
                # any records. It remains an error in metric support, but is
                # not an observed record in the policy-usage denominator.
                raise RuntimeError("synthetic caller failure")
            return await app.run("support_triage", envelope)

        report = await evaluate(
            suite(),
            EvaluationVariant(
                "offline",
                "1",
                invoke,
                prepared.configuration_digest,
                workflow="support_triage",
            ),
            metrics=metric_specs(dataset().suites[0]),
            repeat=2,
            include_details=True,
        )
    assert invocations == 32
    classify = next(
        step for step in report.steps if (step.flow, step.name) == ("triage", "classify")
    )
    assert classify.observed_cases == 31
    assert classify.model_selected_cases == 11
    assert classify.fallback_selected_cases == 16
    assert classify.review_cases == 20
    assert classify.fallback_rate == pytest.approx(16 / 31)

    groups = {
        group.value: group for group in group_report(report, input_pointer="/metadata/language")
    }
    assert invocations == 32  # Grouping reuses outcomes after application shutdown.
    english, german = groups["en"], groups["de"]
    assert (english.source_case_count, english.attempt_count) == (11, 22)
    assert (german.source_case_count, german.attempt_count) == (5, 10)
    english_origin = next(metric for metric in english.metrics if metric.name == "selection_origin")
    german_origin = next(metric for metric in german.metrics if metric.name == "selection_origin")
    assert english_origin.support == 18
    assert english_origin.excluded == 4  # Conflict/multiple intents have no selected-origin gold.
    assert english_origin.errors == 1
    assert english_origin.observed == 17
    assert english_origin.confusion_matrix == ((7, 0), (0, 10))
    assert english_origin.accuracy == pytest.approx(17 / 18)
    assert german_origin.support == german_origin.observed == 10
    assert german_origin.errors == german_origin.excluded == 0
    assert german_origin.confusion_matrix == ((4, 0), (0, 6))
    assert german_origin.accuracy == 1

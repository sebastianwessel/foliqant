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
    assert invocations == 24
    classify = next(step for step in report.steps if step.name == "classify")
    assert classify.observed_cases == 23
    assert classify.model_selected_cases == 11
    assert classify.fallback_selected_cases == 8
    assert classify.review_cases == 12
    assert classify.fallback_rate == pytest.approx(8 / 23)

    groups = {
        group.value: group for group in group_report(report, input_pointer="/metadata/language")
    }
    assert invocations == 24  # Grouping reuses outcomes after application shutdown.
    english, german = groups["en"], groups["de"]
    assert (english.source_case_count, english.attempt_count) == (8, 16)
    assert (german.source_case_count, german.attempt_count) == (4, 8)
    english_origin = next(metric for metric in english.metrics if metric.name == "selection_origin")
    german_origin = next(metric for metric in german.metrics if metric.name == "selection_origin")
    assert english_origin.support == 12
    assert english_origin.excluded == 4  # Conflict/multiple intents have no selected-origin gold.
    assert english_origin.errors == 1
    assert english_origin.observed == 11
    assert english_origin.confusion_matrix == ((7, 0), (0, 4))
    assert english_origin.accuracy == pytest.approx(11 / 12)
    assert german_origin.support == german_origin.observed == 8
    assert german_origin.errors == german_origin.excluded == 0
    assert german_origin.confusion_matrix == ((4, 0), (0, 4))
    assert german_origin.accuracy == 1

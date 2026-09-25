"""Seeded bootstrap intervals and cost summaries keep unknown values unknown."""

import pytest

from foliqant.contracts.execution import Usage
from foliqant.evaluation import paired_difference_interval, ratio_interval
from foliqant.evaluation.summaries import summarize_cost, summarize_usage

COUNTS = {
    "model_requests": 1,
    "tool_calls": 0,
    "output_retries": 0,
    "input_tokens": 10,
    "output_tokens": 2,
    "cache_read_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "reasoning_output_tokens": 0,
}
MODEL = {
    "requests": 1,
    "input_tokens": 10,
    "cached_input_tokens": 0,
    "output_tokens": 2,
    "reasoning_tokens": 0,
}


def priced(cost: float | None) -> Usage:
    estimate = {"cost": cost, "cost_complete": cost is not None, "currency": "USD"}
    return Usage.model_validate(
        {**COUNTS, **estimate, "by_model": {"m": {**MODEL, **estimate}}}, strict=True
    )


def test_ratio_interval_is_seeded_bounded_and_needs_two_cases() -> None:
    samples = [(1, 1)] * 30 + [(0, 1)] * 10
    first = ratio_interval(samples, resamples=400, seed=3)
    assert first == ratio_interval(samples, resamples=400, seed=3)
    assert first is not None and 0.5 < first.low < 0.75 < first.high < 0.95
    assert ratio_interval([(1, 1)]) is None
    assert ratio_interval([(0, 0), (0, 0)], resamples=100) is None  # never a defined rate


def test_paired_interval_describes_the_difference_on_the_same_cases() -> None:
    baseline = [(1, 1)] * 10 + [(0, 1)] * 10
    candidate = [(1, 1)] * 10 + [(1, 1)] * 5 + [(0, 1)] * 5
    interval = paired_difference_interval(baseline, candidate, resamples=500)
    assert interval is not None and 0 < interval.low <= 0.25 <= interval.high < 0.5
    assert interval.excludes_zero
    with pytest.raises(ValueError, match="same cases"):
        paired_difference_interval(baseline, candidate[:-1])


@pytest.mark.parametrize(
    "kwargs", [{"resamples": 10}, {"resamples": True}, {"level": 1.0}, {"level": 95}, {"seed": 1.5}]
)
def test_interval_bounds_are_validated(kwargs) -> None:
    with pytest.raises(ValueError):
        ratio_interval([(1, 1), (0, 1)], **kwargs)


def test_cost_summary_sums_known_estimates_and_keeps_unknown_visible() -> None:
    no_model = Usage.model_validate(
        {**COUNTS, "model_requests": 0, "input_tokens": 0, "output_tokens": 0}, strict=True
    )
    unpriced = Usage.model_validate({**COUNTS, "by_model": {"m": MODEL}}, strict=True)
    complete = summarize_cost([priced(0.1), priced(0.2), no_model])
    assert (complete.observed, complete.unknown, complete.currency) == (3, 0, "USD")
    assert complete.total == complete.known_total == pytest.approx(0.3)
    partial = summarize_cost([priced(0.1), priced(None), unpriced, None])
    assert (partial.observed, partial.unknown) == (1, 3)
    assert partial.known_total == pytest.approx(0.1) and partial.total is None
    assert summarize_cost([]).known_total is None
    assert summarize_usage([priced(0.1)]).cost.total == pytest.approx(0.1)

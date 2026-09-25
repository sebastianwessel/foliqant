"""Seeded percentile bootstrap intervals over authored source cases.

Each source case contributes one ``(numerator, denominator)`` pair (its repeated
attempts pooled), so a rate is ``sum(numerators) / sum(denominators)``. The
interval resamples source cases with replacement; a paired interval resamples the
same cases for baseline and candidate and describes the difference of their rates.
Intervals describe sampling variation of the authored cases only: they are not a
release threshold, a population estimate or a significance test, and they do not
account for label errors.
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

type Sample = tuple[float, float]

DEFAULT_RESAMPLES = 2000
DEFAULT_LEVEL = 0.95
DEFAULT_SEED = 0


@dataclass(frozen=True, slots=True)
class Interval:
    """Percentile interval of a bootstrapped rate or rate difference."""

    low: float
    high: float
    level: float
    resamples: int
    method: str = "percentile_bootstrap"

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


def _bounds(resamples: int, level: float, seed: int) -> None:
    if type(resamples) is not int or not 100 <= resamples <= 100_000:
        raise ValueError("resamples must be an integer from 100 to 100000")
    if type(level) is not float or not 0.5 <= level < 1:
        raise ValueError("level must be a float from 0.5 to below 1")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")


def _percentile(values: Sequence[float], q: float) -> float:
    position = q * (len(values) - 1)
    low = math.floor(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _rate(samples: Sequence[Sample], picks: Sequence[int]) -> float | None:
    denominator = sum(samples[i][1] for i in picks)
    return sum(samples[i][0] for i in picks) / denominator if denominator else None


def _interval(statistics: list[float], level: float, resamples: int) -> Interval | None:
    if len(statistics) < 2:
        return None
    statistics.sort()
    tail = (1 - level) / 2
    return Interval(
        _percentile(statistics, tail), _percentile(statistics, 1 - tail), level, resamples
    )


def ratio_interval(
    samples: Sequence[Sample],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = DEFAULT_LEVEL,
    seed: int = DEFAULT_SEED,
) -> Interval | None:
    """Interval of ``sum(num) / sum(den)`` over cases; None with fewer than two cases."""
    _bounds(resamples, level, seed)
    if len(samples) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not security
    statistics = []
    for _ in range(resamples):
        picks = [rng.randrange(len(samples)) for _ in samples]
        value = _rate(samples, picks)
        if value is not None:
            statistics.append(value)
    return _interval(statistics, level, resamples)


def paired_difference_interval(
    baseline: Sequence[Sample],
    candidate: Sequence[Sample],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = DEFAULT_LEVEL,
    seed: int = DEFAULT_SEED,
) -> Interval | None:
    """Interval of ``candidate rate - baseline rate`` resampling the same cases."""
    _bounds(resamples, level, seed)
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must describe the same cases")
    if len(baseline) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not security
    statistics = []
    for _ in range(resamples):
        picks = [rng.randrange(len(baseline)) for _ in baseline]
        before, after = _rate(baseline, picks), _rate(candidate, picks)
        if before is not None and after is not None:
            statistics.append(after - before)
    return _interval(statistics, level, resamples)


def interval_json(interval: Interval | None) -> dict[str, float | int | str | bool] | None:
    if interval is None:
        return None
    return {
        "low": interval.low,
        "high": interval.high,
        "level": interval.level,
        "resamples": interval.resamples,
        "method": interval.method,
        "excludes_zero": interval.excludes_zero,
    }

"""Pure summaries of measured attempts, without filling missing observations."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil, isfinite
from statistics import median

from foliqant.contracts.execution import Usage


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Seconds from available measurements, including measured failures.

    ``p95`` uses nearest rank: sorted sample at ceil(0.95 * count), one-based.
    Median is the midpoint for even sample counts. Empty samples yield None;
    missing/skipped measurements remain in ``unavailable`` rather than zero.
    """

    count: int
    unavailable: int
    minimum: float | None
    median: float | None
    p95: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class UsageCountSummary:
    """One usage field across attempts; a partial sum is never a complete total.

    ``known_total`` sums available values (None when none are available).
    ``total`` is None whenever any measurement is unavailable. Counts cover all
    attempts, so zero observed usage remains distinguishable from unknown usage.
    """

    observed: int
    unknown: int
    known_total: int | None
    total: int | None


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Independent field coverage avoids guessing unreported token subsets."""

    model_requests: UsageCountSummary
    tool_calls: UsageCountSummary
    input_tokens: UsageCountSummary
    output_tokens: UsageCountSummary
    cache_read_input_tokens: UsageCountSummary
    cache_write_input_tokens: UsageCountSummary
    reasoning_output_tokens: UsageCountSummary


def summarize_latency(values: Iterable[float | None]) -> LatencySummary:
    samples = tuple(values)
    if any(
        value is not None and (type(value) not in (int, float) or not isfinite(value) or value < 0)
        for value in samples
    ):
        raise ValueError("latency measurements must be finite nonnegative seconds or None")
    known = sorted(value for value in samples if value is not None)
    count = len(known)
    return LatencySummary(
        count,
        len(samples) - count,
        known[0] if count else None,
        median(known) if count else None,
        known[ceil(0.95 * count) - 1] if count else None,
        known[-1] if count else None,
    )


def summarize_usage(values: Iterable[Usage | None]) -> UsageSummary:
    samples = tuple(values)

    def field(name: str) -> UsageCountSummary:
        counts = [getattr(value, name) if value is not None else None for value in samples]
        known = [value for value in counts if value is not None]
        total = sum(known) if known else None
        return UsageCountSummary(
            len(known),
            len(counts) - len(known),
            total,
            total if len(known) == len(counts) else None,
        )

    return UsageSummary(
        field("model_requests"),
        field("tool_calls"),
        field("input_tokens"),
        field("output_tokens"),
        field("cache_read_input_tokens"),
        field("cache_write_input_tokens"),
        field("reasoning_output_tokens"),
    )

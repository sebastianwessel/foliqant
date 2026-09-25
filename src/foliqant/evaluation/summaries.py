"""Pure summaries of measured attempts, without filling missing observations."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, isfinite
from statistics import median
from types import MappingProxyType

from foliqant.contracts.execution import Usage

FAILED_STATUSES = frozenset({"failed", "cancelled", "error"})


def count_failures(outcomes: Iterable[tuple[str, str | None]]) -> Mapping[str, int]:
    """Count failed ``(status, error_code)`` observations by their safe code.

    Codes are ordered by descending count, then name. Completed, review and
    skipped observations are not failures and are not counted.
    """
    counts = Counter(code for status, code in outcomes if status in FAILED_STATUSES and code)
    return MappingProxyType(dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))))


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
class CostSummary:
    """The cost estimate across attempts; a partial sum is never a complete total.

    An attempt's cost is known when its usage carries a priced estimate, or when it
    made no model request (zero model cost). It is unknown for an attempt without
    usage (an execution error), an unpriced profile or an incomplete estimate.
    ``known_total`` sums the known estimates (six decimals; None when none is
    known); ``total`` is None whenever any attempt's cost is unknown.
    """

    observed: int
    unknown: int
    known_total: float | None
    total: float | None
    currency: str | None


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
    cost: CostSummary = CostSummary(0, 0, None, None, None)


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
        summarize_cost(samples),
    )


def summarize_cost(values: Iterable[Usage | None]) -> CostSummary:
    """Sum the known cost estimates of attempts (see ``CostSummary``)."""
    known: list[Decimal] = []
    unknown = 0
    currencies: set[str] = set()
    for usage in values:
        if usage is not None and usage.currency is not None and usage.cost is not None:
            known.append(Decimal(str(usage.cost)))
            currencies.add(usage.currency)
        elif usage is not None and usage.model_requests == 0:
            known.append(Decimal(0))
        else:
            unknown += 1
    total = float(sum(known, Decimal(0)).quantize(Decimal("0.000001"))) if known else None
    return CostSummary(
        len(known),
        unknown,
        total,
        total if not unknown else None,
        currencies.pop() if len(currencies) == 1 else None,
    )

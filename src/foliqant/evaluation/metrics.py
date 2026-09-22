"""Explicit-catalog classification measurements with visible unavailable outputs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson
from foliqant.core.plan import BindingPlan

from .records import document_path_status

if TYPE_CHECKING:
    from .contracts import EvaluationCase, EvaluationSuite

type MetricKind = Literal["classification", "multilabel"]
type ObservationState = Literal[
    "observed", "excluded", "abstained", "missing", "skipped", "error", "invalid"
]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """Measure a result pointer against matching authored gold and ordered labels.

    Each included case must have exactly one expectation at ``path``. Cases with
    none are excluded explicitly. Classification gold is a catalog string or
    explicitly declared null;
    multilabel gold is an array of catalog strings (duplicates have set semantics).
    Invalid or ambiguous gold fails validation before any pipeline is called.
    """

    name: str
    path: str
    kind: MetricKind
    labels: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 512:
            raise ValueError("metric names must be nonblank strings up to 512 characters")
        if (
            not isinstance(self.path, str)
            or (self.path and not self.path.startswith("/"))
            or re.search(r"~(?:[^01]|$)", self.path)
            or len(self.path.split("/")) > MAX_JSON_DEPTH + 1
        ):
            raise ValueError("metric path must be an RFC 6901 JSON pointer")
        if self.kind not in {"classification", "multilabel"}:
            raise ValueError("unknown metric kind")
        if isinstance(self.labels, str):
            raise ValueError("metric labels require a sequence of catalog strings")
        labels = tuple(self.labels)
        if (
            not labels
            or any(
                not (label is None and self.kind == "classification")
                and (type(label) is not str or not label.strip())
                for label in labels
            )
            or len(set(labels)) != len(labels)
        ):
            raise ValueError(
                "metric labels must be unique nonblank strings, with null only for classification"
            )
        object.__setattr__(self, "labels", labels)


@dataclass(frozen=True, slots=True)
class RateSummary:
    """Observed-only precision/recall/F1; None denotes an undefined denominator.

    F1 is 2TP / (2TP + FP + FN), so a missed positive gives zero even when
    precision is undefined. These rates never replace all-gold coverage/accuracy.
    Micro pools label counts. Macro averages the entire declared catalog, with
    each rate None if any label's corresponding rate is undefined.
    """

    precision: float | None
    recall: float | None
    f1: float | None


def _rates(tp: int, fp: int, fn: int) -> RateSummary:
    return RateSummary(
        tp / (tp + fp) if tp + fp else None,
        tp / (tp + fn) if tp + fn else None,
        2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    )


@dataclass(frozen=True, slots=True)
class LabelCounts:
    """One-vs-rest counts among valid observed predictions only.

    Unavailable predictions are not fabricated negative labels: each label's
    TP + FP + FN + TN equals the metric's ``observed`` count, not its support.
    """

    label: str | None
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float | None = field(init=False)
    recall: float | None = field(init=False)
    f1: float | None = field(init=False)

    def __post_init__(self) -> None:
        rates = _rates(self.true_positive, self.false_positive, self.false_negative)
        object.__setattr__(self, "precision", rates.precision)
        object.__setattr__(self, "recall", rates.recall)
        object.__setattr__(self, "f1", rates.f1)


@dataclass(frozen=True, slots=True)
class MetricReport:
    """Catalog-ordered counts with support including unavailable observations.

    ``support + excluded`` equals attempted case count (source count * repeat).
    ``observed`` plus abstained, missing, skipped, errors and invalid equals support.
    Accuracy is correct / support (exact-set accuracy for multilabel); coverage
    is observed / support.
    Both are None when support is zero. Confusion rows are expected labels and
    columns predicted labels; their sum is observed. ``source_support`` and
    ``source_excluded`` count distinct authored cases, independent of repeat.
    No raw case values appear.
    """

    name: str
    path: str
    kind: MetricKind
    labels: tuple[str | None, ...]
    support: int
    excluded: int
    observed: int
    abstained: int
    missing: int
    skipped: int
    errors: int
    invalid: int
    correct: int
    accuracy: float | None
    coverage: float | None
    confusion_matrix: tuple[tuple[int, ...], ...] = ()
    per_label: tuple[LabelCounts, ...] = ()
    micro: RateSummary = RateSummary(None, None, None)
    macro: RateSummary = RateSummary(None, None, None)
    source_support: int = 0
    source_excluded: int = 0
    repeat: int = 1


@dataclass(frozen=True, slots=True)
class MetricObservation:
    state: ObservationState
    expected: frozenset[str | None] = frozenset()
    actual: frozenset[str | None] = frozenset()


def _labels(spec: MetricSpec, value: FrozenJson) -> frozenset[str | None] | None:
    if spec.kind == "classification":
        if (isinstance(value, str) or value is None) and value in spec.labels:
            return frozenset((value,))
        return None
    if isinstance(value, tuple) and all(
        type(label) is str and label in spec.labels for label in value
    ):
        return frozenset(label for label in value if isinstance(label, str))
    return None


def validate_metrics(suite: EvaluationSuite, specs: tuple[MetricSpec, ...]) -> None:
    if not all(isinstance(spec, MetricSpec) for spec in specs):
        raise ValueError("metrics require typed specifications")
    if len({spec.name for spec in specs}) != len(specs):
        raise ValueError("metric names must be unique")
    for spec in specs:
        for case in suite.cases:
            matches = [check for check in case.expectations if check.path == spec.path]
            if len(matches) > 1 or (matches and _labels(spec, matches[0].expected) is None):
                raise ValueError("metric gold must be unambiguous and match its label catalog")


def observe_metrics(
    specs: tuple[MetricSpec, ...], case: EvaluationCase, document: FrozenJson, status: str
) -> tuple[MetricObservation, ...]:
    observations = []
    for spec in specs:
        matches = [check for check in case.expectations if check.path == spec.path]
        if not matches:
            observations.append(MetricObservation("excluded"))
            continue
        expected = _labels(spec, matches[0].expected)
        assert expected is not None  # Validated before execution.
        owner_status = document_path_status(spec.path, document)
        if owner_status == "skipped":
            observations.append(MetricObservation("skipped", expected))
            continue
        if status in {"failed", "cancelled", "error"} or owner_status in {"failed", "cancelled"}:
            observations.append(MetricObservation("error", expected))
            continue
        try:
            actual = resolve_binding(BindingPlan(kind="pointer", pointer=spec.path), document)
        except ServiceError:
            state: ObservationState = "missing"
            observations.append(MetricObservation(state, expected))
            continue
        parsed = _labels(spec, actual)
        if actual is None and parsed is None:
            observations.append(MetricObservation("abstained", expected))
        elif parsed is None:
            observations.append(MetricObservation("invalid", expected))
        else:
            observations.append(MetricObservation("observed", expected, parsed))
    return tuple(observations)


def summarize_metrics(
    specs: tuple[MetricSpec, ...],
    observations: Sequence[tuple[MetricObservation, ...]],
    *,
    repeat: int = 1,
) -> tuple[MetricReport, ...]:
    reports = []
    for index, spec in enumerate(specs):
        values = [case[index] for case in observations]
        observed = [value for value in values if value.state == "observed"]
        excluded = sum(value.state == "excluded" for value in values)
        support = len(values) - excluded
        correct = sum(value.actual == value.expected for value in observed)
        matrix: tuple[tuple[int, ...], ...] = ()
        if spec.kind == "classification":
            pairs = Counter(
                (next(iter(value.expected)), next(iter(value.actual))) for value in observed
            )
            matrix = tuple(tuple(pairs[gold, pred] for pred in spec.labels) for gold in spec.labels)
        per_label = tuple(
            LabelCounts(
                label,
                sum(label in value.actual and label in value.expected for value in observed),
                sum(label in value.actual and label not in value.expected for value in observed),
                sum(label not in value.actual and label in value.expected for value in observed),
                sum(
                    label not in value.actual and label not in value.expected for value in observed
                ),
            )
            for label in spec.labels
        )
        micro = _rates(
            sum(label.true_positive for label in per_label),
            sum(label.false_positive for label in per_label),
            sum(label.false_negative for label in per_label),
        )

        def macro_rate(values: Sequence[float | None]) -> float | None:
            known = [value for value in values if value is not None]
            return sum(known) / len(values) if len(known) == len(values) else None

        macro = RateSummary(
            macro_rate([label.precision for label in per_label]),
            macro_rate([label.recall for label in per_label]),
            macro_rate([label.f1 for label in per_label]),
        )
        reports.append(
            MetricReport(
                spec.name,
                spec.path,
                spec.kind,
                spec.labels,
                support,
                excluded,
                len(observed),
                sum(value.state == "abstained" for value in values),
                sum(value.state == "missing" for value in values),
                sum(value.state == "skipped" for value in values),
                sum(value.state == "error" for value in values),
                sum(value.state == "invalid" for value in values),
                correct,
                correct / support if support else None,
                len(observed) / support if support else None,
                matrix,
                per_label,
                micro,
                macro,
                support // repeat,
                excluded // repeat,
                repeat,
            )
        )
    return tuple(reports)

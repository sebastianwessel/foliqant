"""Explicit-catalog classification measurements with visible unavailable outputs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson
from foliqant.core.plan import BindingPlan

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
    none are excluded explicitly. Classification gold is a catalog string;
    multilabel gold is an array of catalog strings (duplicates have set semantics).
    Invalid or ambiguous gold fails validation before any pipeline is called.
    """

    name: str
    path: str
    kind: MetricKind
    labels: tuple[str, ...]

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
            or any(type(label) is not str or not label.strip() for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError("metric labels must be a nonempty unique string catalog")
        object.__setattr__(self, "labels", labels)


@dataclass(frozen=True, slots=True)
class LabelCounts:
    """One-vs-rest counts among valid observed predictions only.

    Unavailable predictions are not fabricated negative labels: each label's
    TP + FP + FN + TN equals the metric's ``observed`` count, not its support.
    """

    label: str
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int


@dataclass(frozen=True, slots=True)
class MetricReport:
    """Catalog-ordered counts with support including unavailable observations.

    ``support + excluded`` equals suite case count. ``observed`` plus abstained,
    missing, skipped, errors and invalid equals support. Accuracy is correct /
    support (exact-set accuracy for multilabel); coverage is observed / support.
    Both are None when support is zero. Confusion rows are expected labels and
    columns predicted labels; their sum is observed. No raw case values appear.
    """

    name: str
    path: str
    kind: MetricKind
    labels: tuple[str, ...]
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


@dataclass(frozen=True, slots=True)
class MetricObservation:
    state: ObservationState
    expected: frozenset[str] = frozenset()
    actual: frozenset[str] = frozenset()


def _labels(spec: MetricSpec, value: FrozenJson) -> frozenset[str] | None:
    if spec.kind == "classification":
        return frozenset((value,)) if type(value) is str and value in spec.labels else None
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
        if status in {"failed", "cancelled", "error"}:
            observations.append(MetricObservation("error", expected))
            continue
        try:
            actual = resolve_binding(BindingPlan(kind="pointer", pointer=spec.path), document)
        except ServiceError:
            state: ObservationState = "missing"
            parts = spec.path.split("/")
            if len(parts) >= 3 and parts[1] == "decisions":
                try:
                    step_status = resolve_binding(
                        BindingPlan(kind="pointer", pointer=f"/decisions/{parts[2]}/status"),
                        document,
                    )
                    if step_status == "skipped":
                        state = "skipped"
                    elif step_status in ("failed", "cancelled"):
                        state = "error"
                except ServiceError:
                    pass
            observations.append(MetricObservation(state, expected))
            continue
        parsed = _labels(spec, actual)
        if actual is None:
            observations.append(MetricObservation("abstained", expected))
        elif parsed is None:
            observations.append(MetricObservation("invalid", expected))
        else:
            observations.append(MetricObservation("observed", expected, parsed))
    return tuple(observations)


def summarize_metrics(
    specs: tuple[MetricSpec, ...], observations: Sequence[tuple[MetricObservation, ...]]
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
            )
        )
    return tuple(reports)

"""Explicit-catalog classification, multilabel and per-field measurements with visible
unavailable outputs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ServiceError
from foliqant.core.json import MAX_JSON_DEPTH, FrozenJson
from foliqant.core.plan import BindingPlan

from .comparisons import EXECUTED_STATUSES, ProjectionError, matches, project
from .records import document_path_status

if TYPE_CHECKING:
    from .contracts import EvaluationCase, EvaluationSuite, Expectation

type MetricKind = Literal["classification", "multilabel", "fields"]
type ObservationState = Literal[
    "observed", "excluded", "abstained", "missing", "skipped", "error", "invalid"
]
type FieldOutcome = Literal[
    "correct_value", "correct_null", "hallucinated", "missed", "wrong_value", "unavailable"
]
FIELD_OUTCOMES: tuple[FieldOutcome, ...] = (
    "correct_value",
    "correct_null",
    "hallucinated",
    "missed",
    "wrong_value",
    "unavailable",
)


def _is_pointer(value: object) -> bool:
    return (
        isinstance(value, str)
        and (not value or value.startswith("/"))
        and not re.search(r"~(?:[^01]|$)", value)
        and len(value.split("/")) <= MAX_JSON_DEPTH + 1
    )


def field_path(path: str, field: str) -> str:
    """The pointer of one field below a ``fields`` metric path."""
    return f"{path}/{field.replace('~', '~0').replace('/', '~1')}"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """Measure a result pointer against matching authored gold and ordered labels.

    Each included case must have exactly one expectation at ``path`` (with the
    same ``each`` projection). Cases with none are excluded explicitly.
    Classification gold is a catalog string or explicitly declared null;
    multilabel gold is an array of catalog strings (duplicates have set semantics).
    A ``fields`` metric measures an object at ``path``: its labels are field names
    and its gold is the expectation at ``path/<field>`` of each labelled field
    (null gold means the field must be absent or null). ``expectation`` names the
    one assertion to measure when a case has several at the same path (e.g. an
    unordered set check next to an ordered one). Invalid or ambiguous gold fails
    validation before any pipeline is called.
    """

    name: str
    path: str
    kind: MetricKind
    labels: tuple[str | None, ...]
    each: str | None = None
    expectation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 512:
            raise ValueError("metric names must be nonblank strings up to 512 characters")
        if not _is_pointer(self.path):
            raise ValueError("metric path must be an RFC 6901 JSON pointer")
        if self.kind not in {"classification", "multilabel", "fields"}:
            raise ValueError("unknown metric kind")
        if self.each is not None and (
            not self.each or not _is_pointer(self.each) or self.kind == "fields"
        ):
            raise ValueError("metric each must be a nonempty relative JSON pointer")
        if self.expectation is not None and (
            not isinstance(self.expectation, str)
            or not self.expectation.strip()
            or self.kind == "fields"
        ):
            raise ValueError("metric expectation must name a path assertion")
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
class FieldCounts:
    """Outcomes of one field (or all fields) of a ``fields`` metric over attempts.

    ``value_support`` counts gold with a value, ``null_support`` gold null (the
    field must be absent or null). Unavailable observations (skipped or failed
    owners, failed runs) stay in support and are never correct. Accuracy is
    (correct values + correct nulls) / support; the hallucination rate is
    hallucinated / null support and the miss rate missed / value support, each
    None on a zero denominator.
    """

    label: str | None
    value_support: int
    null_support: int
    correct_value: int
    correct_null: int
    hallucinated: int
    missed: int
    wrong_value: int
    unavailable: int
    support: int = field(init=False)
    accuracy: float | None = field(init=False)
    hallucination_rate: float | None = field(init=False)
    miss_rate: float | None = field(init=False)

    def __post_init__(self) -> None:
        support = self.value_support + self.null_support
        correct = self.correct_value + self.correct_null
        object.__setattr__(self, "support", support)
        object.__setattr__(self, "accuracy", correct / support if support else None)
        object.__setattr__(
            self,
            "hallucination_rate",
            self.hallucinated / self.null_support if self.null_support else None,
        )
        object.__setattr__(
            self, "miss_rate", self.missed / self.value_support if self.value_support else None
        )


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
    For a ``fields`` metric, ``correct`` counts attempts whose every gold field is
    correct; ``per_field`` and ``field_totals`` count field outcomes, with
    ``field_accuracy`` (pooled over fields) and ``macro_field_accuracy`` (mean of
    the per-field accuracies of fields with support).
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
    each: str | None = None
    expectation: str | None = None
    per_field: tuple[FieldCounts, ...] = ()
    field_totals: FieldCounts | None = None
    field_accuracy: float | None = None
    macro_field_accuracy: float | None = None


@dataclass(frozen=True, slots=True)
class MetricObservation:
    state: ObservationState
    expected: frozenset[str | None] = frozenset()
    actual: frozenset[str | None] = frozenset()
    fields: tuple[tuple[str, FieldOutcome, bool], ...] = ()  # (field, outcome, gold is null)


def _gold(spec: MetricSpec, check: Expectation) -> frozenset[str | None] | None:
    """The gold labels of a metric assertion; ``one_of`` lists acceptable labels."""
    if check.comparison != "one_of":
        return _labels(spec, check.expected)
    if spec.kind != "classification" or not isinstance(check.expected, tuple):
        return None
    labels = [_labels(spec, item) for item in check.expected]
    if any(label is None for label in labels):
        return None
    return frozenset(item for label in labels if label is not None for item in label)


def _primary(check: Expectation) -> str | None:
    value = check.expected[0] if isinstance(check.expected, tuple) else check.expected
    assert value is None or isinstance(value, str)  # Validated classification gold.
    return value


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


def _matching(spec: MetricSpec, case: EvaluationCase) -> list[Expectation]:
    return [
        check
        for check in case.expectations
        if check.path == spec.path
        and check.each == spec.each
        and (spec.expectation is None or check.name == spec.expectation)
    ]


def _field_gold(spec: MetricSpec, case: EvaluationCase) -> list[tuple[str, Expectation]]:
    gold = []
    for label in spec.labels:
        assert label is not None  # Field catalogs are validated string-only.
        path = field_path(spec.path, label)
        matches_ = [check for check in case.expectations if check.path == path]
        if matches_:
            gold.append((label, matches_[0]))
    return gold


def validate_metrics(suite: EvaluationSuite, specs: tuple[MetricSpec, ...]) -> None:
    if not all(isinstance(spec, MetricSpec) for spec in specs):
        raise ValueError("metrics require typed specifications")
    if len({spec.name for spec in specs}) != len(specs):
        raise ValueError("metric names must be unique")
    for spec in specs:
        if spec.kind == "fields":
            if any(label is None for label in spec.labels):
                raise ValueError("fields metric labels must be field names")
            for case in suite.cases:
                for label in spec.labels:
                    path = field_path(spec.path, cast(str, label))
                    checks = [check for check in case.expectations if check.path == path]
                    if len(checks) > 1 or any(
                        check.comparison in {"custom", "source_span"} or check.each is not None
                        for check in checks
                    ):
                        raise ValueError(
                            "fields metric gold must be one built-in, unprojected field assertion"
                        )
            continue
        for case in suite.cases:
            matches_ = _matching(spec, case)
            if len(matches_) > 1 or (matches_ and _gold(spec, matches_[0]) is None):
                raise ValueError("metric gold must be unambiguous and match its label catalog")


def _resolve(
    path: str, document: FrozenJson, *, absent_as_null: bool, owner_status: str | None, status: str
) -> tuple[bool, FrozenJson]:
    """(present, value); an absent value is null only inside an executed owner."""
    try:
        return True, resolve_binding(BindingPlan(kind="pointer", pointer=path), document)
    except ServiceError:
        executed = (
            owner_status in EXECUTED_STATUSES
            if owner_status is not None or path.startswith("/flows/")
            else status in EXECUTED_STATUSES
        )
        if absent_as_null and executed:
            return True, None
        return False, None


def _observe_fields(
    spec: MetricSpec, case: EvaluationCase, document: FrozenJson, status: str
) -> MetricObservation:
    gold = _field_gold(spec, case)
    if not gold:
        return MetricObservation("excluded")
    owner_status = document_path_status(spec.path, document)
    unavailable: ObservationState | None = (
        "skipped"
        if owner_status == "skipped"
        else "error"
        if status in {"failed", "cancelled", "error"} or owner_status in {"failed", "cancelled"}
        else None
    )
    outcomes: list[tuple[str, FieldOutcome, bool]] = []
    for label, check in gold:
        null_gold = check.expected is None
        if unavailable is not None:
            outcomes.append((label, "unavailable", null_gold))
            continue
        present, actual = _resolve(
            field_path(spec.path, label),
            document,
            absent_as_null=True,  # A field the result omits is a null field.
            owner_status=owner_status,
            status=status,
        )
        if not present:
            outcomes.append((label, "unavailable", null_gold))
        elif null_gold:
            outcomes.append((label, "correct_null" if actual is None else "hallucinated", True))
        elif actual is None:
            outcomes.append((label, "missed", False))
        else:
            outcome: FieldOutcome = (
                "correct_value" if matches(check, actual, None) else "wrong_value"
            )
            outcomes.append((label, outcome, False))
    state: ObservationState = (
        unavailable
        if unavailable is not None
        else "missing"
        if all(outcome == "unavailable" for _, outcome, _ in outcomes)
        else "observed"
    )
    return MetricObservation(state, fields=tuple(outcomes))


def observe_metrics(
    specs: tuple[MetricSpec, ...], case: EvaluationCase, document: FrozenJson, status: str
) -> tuple[MetricObservation, ...]:
    observations = []
    for spec in specs:
        if spec.kind == "fields":
            observations.append(_observe_fields(spec, case, document, status))
            continue
        matches_ = _matching(spec, case)
        if not matches_:
            observations.append(MetricObservation("excluded"))
            continue
        acceptable = _gold(spec, matches_[0])
        assert acceptable is not None  # Validated before execution.
        # An accepted alternative counts as that label; otherwise the primary gold.
        expected = (
            frozenset((_primary(matches_[0]),))
            if matches_[0].comparison == "one_of"
            else acceptable
        )
        owner_status = document_path_status(spec.path, document)
        if owner_status == "skipped":
            observations.append(MetricObservation("skipped", expected))
            continue
        if status in {"failed", "cancelled", "error"} or owner_status in {"failed", "cancelled"}:
            observations.append(MetricObservation("error", expected))
            continue
        present, actual = _resolve(
            spec.path,
            document,
            absent_as_null=matches_[0].absent_as_null,
            owner_status=owner_status,
            status=status,
        )
        if not present:
            observations.append(MetricObservation("missing", expected))
            continue
        if spec.each is not None:
            try:
                actual = project(actual, spec.each)
            except ProjectionError:
                observations.append(MetricObservation("invalid", expected))
                continue
        parsed = _labels(spec, actual)
        if parsed is not None and matches_[0].comparison == "one_of" and parsed <= acceptable:
            expected = parsed
        if actual is None and parsed is None:
            observations.append(MetricObservation("abstained", expected))
        elif parsed is None:
            observations.append(MetricObservation("invalid", expected))
        else:
            observations.append(MetricObservation("observed", expected, parsed))
    return tuple(observations)


def _field_counts(label: str | None, outcomes: Sequence[tuple[FieldOutcome, bool]]) -> FieldCounts:
    counts = Counter(outcome for outcome, _ in outcomes)
    return FieldCounts(
        label,
        sum(not null_gold for _, null_gold in outcomes),
        sum(null_gold for _, null_gold in outcomes),
        counts["correct_value"],
        counts["correct_null"],
        counts["hallucinated"],
        counts["missed"],
        counts["wrong_value"],
        counts["unavailable"],
    )


def _summarize_fields(
    spec: MetricSpec, values: Sequence[MetricObservation], repeat: int
) -> MetricReport:
    excluded = sum(value.state == "excluded" for value in values)
    support = len(values) - excluded
    included = [value for value in values if value.state != "excluded"]
    per_field = tuple(
        _field_counts(
            label,
            [
                (outcome, null_gold)
                for value in included
                for name, outcome, null_gold in value.fields
                if name == label
            ],
        )
        for label in spec.labels
    )
    totals = _field_counts(
        None, [(outcome, null_gold) for value in included for _, outcome, null_gold in value.fields]
    )
    correct = sum(
        value.state == "observed"
        and all(outcome in {"correct_value", "correct_null"} for _, outcome, _ in value.fields)
        for value in included
    )
    observed = sum(value.state == "observed" for value in included)
    field_accuracies = [item.accuracy for item in per_field if item.accuracy is not None]
    return MetricReport(
        spec.name,
        spec.path,
        spec.kind,
        spec.labels,
        support,
        excluded,
        observed,
        0,
        sum(value.state == "missing" for value in values),
        sum(value.state == "skipped" for value in values),
        sum(value.state == "error" for value in values),
        0,
        correct,
        correct / support if support else None,
        observed / support if support else None,
        source_support=support // repeat,
        source_excluded=excluded // repeat,
        repeat=repeat,
        per_field=per_field,
        field_totals=totals,
        field_accuracy=totals.accuracy,
        macro_field_accuracy=(
            sum(field_accuracies) / len(field_accuracies) if field_accuracies else None
        ),
    )


def summarize_metrics(
    specs: tuple[MetricSpec, ...],
    observations: Sequence[tuple[MetricObservation, ...]],
    *,
    repeat: int = 1,
) -> tuple[MetricReport, ...]:
    reports = []
    for index, spec in enumerate(specs):
        values = [case[index] for case in observations]
        if spec.kind == "fields":
            reports.append(_summarize_fields(spec, values, repeat))
            continue
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
                each=spec.each,
                expectation=spec.expectation,
            )
        )
    return tuple(reports)

"""Group detailed evaluation attempts by an explicitly selected input scalar or array."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from foliqant.contracts.envelope import Envelope
from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, JsonValue, thaw_json
from foliqant.core.plan import BindingPlan

from .comparisons import canonical
from .contracts import (
    CaseReport,
    CheckSummary,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    _json,
    _no_failures,
    expectation_identity,
)
from .metrics import MetricReport, MetricSpec, observe_metrics, summarize_metrics, validate_metrics
from .runner import _summary
from .summaries import (
    FAILED_STATUSES,
    LatencySummary,
    UsageSummary,
    count_failures,
    summarize_latency,
    summarize_usage,
)

type GroupValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class EvaluationGroupReport:
    """Private descriptive subset, with the same attempt denominators as its source.

    ``value_present=False`` identifies a missing input pointer; present JSON null
    is a separate group. Value types stay distinct, including bool/int/float.
    ``member=True`` marks a group of one element of array input values (e.g. tags):
    an attempt belongs to the group of every distinct element, so member groups
    overlap and an empty array joins no group. ``case_pass_rate`` and
    ``failure_rate`` cover the group's attempts like the report's own rates.
    Latency summarizes recorded invocation measurements, never a fabricated subset
    wall-clock duration. ``failures_by_code`` counts the group's failed attempts
    by safe error code. The group value is caller data; keep these reports private.
    """

    input_pointer: str
    value_present: bool
    value: GroupValue
    source_case_count: int
    attempt_count: int
    checks: CheckSummary
    metrics: tuple[MetricReport, ...]
    latency: LatencySummary
    usage: UsageSummary
    member: bool = False
    case_pass_rate: float = 0.0
    failure_rate: float = 0.0
    failures_by_code: Mapping[str, int] = field(default_factory=_no_failures)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible group, including check rates."""
        result = cast(dict[str, JsonValue], _json(self))
        result["check_pass_rate"] = self.checks.pass_rate
        result["check_coverage"] = self.checks.coverage
        return result


def _scalar(value: FrozenJson) -> GroupValue:
    if value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("group values must be JSON scalars or arrays of scalars")
    return cast(GroupValue, value)


def _value(
    binding: BindingPlan, document: FrozenJson
) -> tuple[bool, list[tuple[bool, GroupValue]]]:
    """(present, [(member, value), ...]): one scalar, or the distinct array elements."""
    try:
        value = resolve_binding(binding, document)
    except ServiceError as error:
        if error.code == ErrorCode.MISSING_BINDING:
            return False, [(False, None)]
        raise ValueError("input_pointer must be an RFC 6901 JSON pointer") from None
    if isinstance(value, tuple):
        members: dict[str, GroupValue] = {}
        for item in value:
            members.setdefault(canonical(item), _scalar(item))
        return True, [(True, item) for item in members.values()]
    return True, [(False, _scalar(value))]


def group_report(
    report: EvaluationReport, *, input_pointer: str
) -> tuple[EvaluationGroupReport, ...]:
    """Summarize saved observations by an input pointer without calling a pipeline.

    For example, ``group_report(report, input_pointer="/metadata/language")``
    uses the caller's authored metadata. It does not infer language or business
    meaning. The report must have been evaluated with ``include_details=True``.
    Groups retain first-occurrence order; missing and present-null inputs differ.
    An array value (e.g. ``/metadata/tags``) puts the attempt into one member group
    per distinct element; member groups overlap.
    Repeated sources must have identical inputs/gold and complete repetition IDs,
    so each source retains equal weight. Existing assertion outcomes are counted;
    custom scorers are never called again. Metrics reuse saved results and gold.
    """
    if not isinstance(input_pointer, str):
        raise ValueError("input_pointer must be an RFC 6901 JSON pointer")
    binding = BindingPlan(kind="pointer", pointer=input_pointer)
    _value(binding, None)  # Validate syntax even for an empty/malformed report.
    if type(report.repeat) is not int or report.repeat < 1 or not report.cases:
        raise ValueError("report requires complete repeated source cases")
    specs = tuple(
        MetricSpec(item.name, item.path, item.kind, item.labels, item.each, item.expectation)
        for item in report.metrics
    )
    grouped: dict[tuple[bool, bool, str], list[tuple[CaseReport, EvaluationCase]]] = {}
    values: dict[tuple[bool, bool, str], tuple[bool, bool, GroupValue]] = {}
    sources: dict[str, tuple[tuple[tuple[bool, bool, str], ...], str, tuple[str, ...]]] = {}
    repetitions: dict[str, set[int]] = {}
    for attempt in report.cases:
        details = attempt.details
        if details is None:
            raise ValueError("grouping requires a detailed private report")
        present, members = _value(binding, details.input)
        keys = tuple((present, member, canonical(value)) for member, value in members)
        signature = (
            keys,
            canonical(details.input),
            tuple(
                json.dumps(expectation_identity(check), sort_keys=True)
                for check in details.expectations
            ),
        )
        if attempt.id in sources and sources[attempt.id] != signature:
            raise ValueError("repeated source inputs, gold or groups differ")
        sources[attempt.id] = signature
        seen = repetitions.setdefault(attempt.id, set())
        if (
            type(attempt.repetition) is not int
            or not 1 <= attempt.repetition <= report.repeat
            or attempt.repetition in seen
        ):
            raise ValueError("report repetition identity is invalid")
        seen.add(attempt.repetition)
        case = EvaluationCase(
            attempt.id,
            Envelope.model_validate(thaw_json(details.input), strict=True),
            details.expectations,
        )
        for key, (member, value) in zip(keys, members, strict=True):
            grouped.setdefault(key, []).append((attempt, case))
            values[key] = present, member, value
    if any(len(seen) != report.repeat for seen in repetitions.values()):
        raise ValueError("report requires complete repeated source cases")
    result = []
    for key, attempts in grouped.items():
        unique = {case.id: case for _, case in attempts}
        validate_metrics(
            EvaluationSuite(report.suite_name, report.suite_revision, tuple(unique.values())), specs
        )
        observations = []
        for attempt, case in attempts:
            assert attempt.details is not None  # Required above for every attempt.
            observations.append(
                observe_metrics(specs, case, attempt.details.result, attempt.status)
            )
        present, member, value = values[key]
        result.append(
            EvaluationGroupReport(
                input_pointer,
                present,
                value,
                len(unique),
                len(attempts),
                _summary(check for attempt, _ in attempts for check in attempt.checks),
                summarize_metrics(specs, observations, repeat=report.repeat),
                summarize_latency(attempt.elapsed_seconds for attempt, _ in attempts),
                summarize_usage(attempt.usage for attempt, _ in attempts),
                member,
                sum(attempt.passed for attempt, _ in attempts) / len(attempts),
                sum(attempt.status in FAILED_STATUSES for attempt, _ in attempts) / len(attempts),
                count_failures((attempt.status, attempt.error_code) for attempt, _ in attempts),
            )
        )
    return tuple(result)

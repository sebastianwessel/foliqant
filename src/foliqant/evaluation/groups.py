"""Group detailed evaluation attempts by an explicitly selected input scalar."""

from dataclasses import dataclass
from typing import cast

from foliqant.contracts.envelope import Envelope
from foliqant.core.bindings import resolve_binding
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, JsonValue, thaw_json
from foliqant.core.plan import BindingPlan

from .contracts import (
    CaseReport,
    CheckSummary,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    _json,
    canonical,
)
from .metrics import MetricReport, MetricSpec, observe_metrics, summarize_metrics, validate_metrics
from .runner import _summary
from .summaries import LatencySummary, UsageSummary, summarize_latency, summarize_usage

type GroupValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class EvaluationGroupReport:
    """Private descriptive subset, with the same attempt denominators as its source.

    ``value_present=False`` identifies a missing input pointer; present JSON null
    is a separate group. Value types stay distinct, including bool/int/float.
    Latency summarizes recorded invocation measurements, never a fabricated subset
    wall-clock duration. The group value is caller data; keep these reports private.
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

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible group, including check rates."""
        result = cast(dict[str, JsonValue], _json(self))
        result["check_pass_rate"] = self.checks.pass_rate
        result["check_coverage"] = self.checks.coverage
        return result


def _value(binding: BindingPlan, document: FrozenJson) -> tuple[bool, GroupValue]:
    try:
        value = resolve_binding(binding, document)
    except ServiceError as error:
        if error.code == ErrorCode.MISSING_BINDING:
            return False, None
        raise ValueError("input_pointer must be an RFC 6901 JSON pointer") from None
    if value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("group values must be JSON scalars")
    return True, cast(GroupValue, value)


def group_report(
    report: EvaluationReport, *, input_pointer: str
) -> tuple[EvaluationGroupReport, ...]:
    """Summarize saved observations by an input pointer without calling a pipeline.

    For example, ``group_report(report, input_pointer="/metadata/language")``
    uses the caller's authored metadata. It does not infer language or business
    meaning. The report must have been evaluated with ``include_details=True``.
    Groups retain first-occurrence order; missing and present-null inputs differ.
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
        MetricSpec(item.name, item.path, item.kind, item.labels) for item in report.metrics
    )
    grouped: dict[tuple[bool, str], list[tuple[CaseReport, EvaluationCase]]] = {}
    values: dict[tuple[bool, str], tuple[bool, GroupValue]] = {}
    sources: dict[
        str, tuple[tuple[bool, str], str, tuple[tuple[str, str, str, str, str | None], ...]]
    ] = {}
    repetitions: dict[str, set[int]] = {}
    for attempt in report.cases:
        details = attempt.details
        if details is None:
            raise ValueError("grouping requires a detailed private report")
        present, value = _value(binding, details.input)
        key = present, canonical(value)
        signature = (
            key,
            canonical(details.input),
            tuple(
                (check.name, check.path, canonical(check.expected), check.comparison, check.scorer)
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
        grouped.setdefault(key, []).append((attempt, case))
        values[key] = present, value
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
        present, value = values[key]
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
            )
        )
    return tuple(result)

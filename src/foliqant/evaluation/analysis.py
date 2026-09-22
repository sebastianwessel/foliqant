"""Strict offline comparison of persisted private evaluation reports."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.json import JsonValue

from .artifact import MAX_REPORT_BYTES, report_document
from .contracts import EvaluationCase, Expectation
from .dataset import read_json
from .metrics import MetricObservation, MetricSpec, observe_metrics, summarize_metrics
from .records import snapshot_execution

type CaseChange = Literal["improved", "regressed", "mixed", "unchanged"]

_OUTCOME_RANK = {"passed": 2, "failed": 1, "missing": 0, "skipped": 0, "error": 0}
# Review is a nonfailed business outcome, not an inherent regression. Authored
# status checks decide whether review or completion is correct for a case.
_STATUS_RANK = {"completed": 1, "needs_review": 1, "failed": 0, "cancelled": 0, "error": 0}
_USAGE_FIELDS = (
    "model_requests",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)


@dataclass(frozen=True, slots=True)
class ReportComparison:
    """Derived comparison of two immutable report artifacts.

    Values are descriptive differences over the recorded attempts. They do not
    establish statistical significance, model quality, or production reliability.
    """

    document: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible comparison document."""
        return cast(dict[str, JsonValue], _copy_json(self.document))


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    return value


def _mapping(value: object, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(reason)
    return cast(dict[str, Any], value)


def _sequence(value: object, reason: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(reason)
    return value


def _artifact(path: Path) -> dict[str, Any]:
    raw = report_document(read_json(path, max_bytes=MAX_REPORT_BYTES))
    dataset = _mapping(raw.get("dataset"), "evaluation artifact dataset is missing")
    if not isinstance(dataset.get("name"), str) or not isinstance(dataset.get("revision"), str):
        raise ValueError("evaluation artifact dataset identity is invalid")
    reports = _sequence(raw.get("reports"), "evaluation artifact reports are missing")
    if not reports:
        raise ValueError("evaluation artifact has no reports")
    raw["reports"] = [
        _mapping(report, "evaluation artifact contains an invalid report") for report in reports
    ]
    names = [report.get("suite_name") for report in raw["reports"]]
    if not all(isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("evaluation artifact suite identities are invalid")
    return raw


def _same(left: object, right: object, reason: str) -> None:
    # Python equality aliases JSON booleans and numbers (True == 1) and integer
    # and floating representations (1 == 1.0). Evaluation gold is type-sensitive.
    left_json = json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    right_json = json.dumps(right, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if left_json != right_json:
        raise ValueError(reason)


def _identity(
    report: dict[str, Any],
) -> tuple[str, str, str | None, str | None, str | None]:
    values = (
        report.get("suite_name"),
        report.get("suite_fingerprint"),
        report.get("target_workflow"),
        report.get("target_flow"),
        report.get("target_step"),
    )
    if not isinstance(values[0], str) or not isinstance(values[1], str):
        raise ValueError("report suite identity is invalid")
    if values[2] is not None and not isinstance(values[2], str):
        raise ValueError("report workflow target is invalid")
    if values[3] is not None and not isinstance(values[3], str):
        raise ValueError("report flow target is invalid")
    if values[4] is not None and not isinstance(values[4], str):
        raise ValueError("report step target is invalid")
    if values[4] is not None and values[3] is None:
        raise ValueError("report step target requires a flow target")
    return cast(tuple[str, str, str | None, str | None, str | None], values)


def _repetition(case: dict[str, Any]) -> int:
    value = case.get("repetition", 1)
    if type(value) is not int or value < 1:
        raise ValueError("report repetition is invalid")
    return value


def _case_semantics(case: dict[str, Any]) -> tuple[Any, Any]:
    details = _mapping(case.get("details"), "comparison requires detailed private reports")
    expectations = _sequence(details.get("expectations"), "case expectations are missing")
    if "input" not in details:
        raise ValueError("case input is missing")
    return details["input"], expectations


def _validated_result(case: dict[str, Any]) -> ExecutionResult | None:
    status = case.get("status")
    if status not in _STATUS_RANK:
        raise ValueError("case status is invalid")
    details = _mapping(case.get("details"), "comparison requires detailed private reports")
    raw = details.get("result")
    if raw is None:
        if status != "error" or case.get("usage") is not None:
            raise ValueError("case result and status are inconsistent")
        return None
    result = ExecutionResult.model_validate(raw, strict=True)
    if (
        status != result.execution.status
        or case.get("workflow") != result.execution.workflow
        or case.get("workflow_revision") != result.execution.revision
    ):
        raise ValueError("case result and status are inconsistent")
    _same(case.get("usage"), result.execution.usage.model_dump(mode="json"), "case usage differs")
    return result


def _attempt_contract(report: dict[str, Any], cases: list[dict[str, Any]]) -> tuple[int, int]:
    repeat = report.get("repeat", 1)
    if type(repeat) is not int or repeat < 1 or len(cases) % repeat:
        raise ValueError("report repeat is invalid")
    source_count = len(cases) // repeat
    declared_source_count = report.get("source_case_count", source_count)
    if type(declared_source_count) is not int or declared_source_count != source_count:
        raise ValueError("report source case count differs")
    identifiers: set[str] = set()
    for offset in range(0, len(cases), repeat):
        group = cases[offset : offset + repeat]
        case_id = group[0].get("id")
        if not isinstance(case_id, str) or case_id in identifiers:
            raise ValueError("report case identity or order is invalid")
        identifiers.add(case_id)
        if any(case.get("id") != case_id for case in group) or [
            _repetition(case) for case in group
        ] != list(range(1, repeat + 1)):
            raise ValueError("report repetition order is invalid")
    return repeat, source_count


def _check_semantics(case: dict[str, Any]) -> list[tuple[Any, Any, Any, Any, Any]]:
    checks = _sequence(case.get("checks"), "case checks are missing")
    values: list[tuple[Any, Any, Any, Any, Any]] = []
    for raw in checks:
        check = _mapping(raw, "case check is invalid")
        outcome = check.get("outcome")
        if outcome not in _OUTCOME_RANK:
            raise ValueError("case check outcome is invalid")
        values.append(
            (check.get("name"), check.get("path"), check.get("flow"), check.get("step"), outcome)
        )
    return values


def _metric_specs(report: dict[str, Any]) -> list[MetricSpec]:
    specifications: list[MetricSpec] = []
    for raw in _sequence(report.get("metrics", []), "report metrics are invalid"):
        metric = _mapping(raw, "report metric is invalid")
        labels = metric.get("labels")
        if (
            not isinstance(metric.get("name"), str)
            or not isinstance(metric.get("path"), str)
            or metric.get("kind") not in {"classification", "multilabel"}
            or not isinstance(labels, list)
            or not labels
            or not all(type(label) is str or label is None for label in labels)
            or len(set(labels)) != len(labels)
        ):
            raise ValueError("report metric contract is invalid")
        specifications.append(
            MetricSpec(
                metric["name"],
                metric["path"],
                metric["kind"],
                tuple(cast(list[str | None], labels)),
            )
        )
    if len({item.name for item in specifications}) != len(specifications):
        raise ValueError("report metric names are not unique")
    return specifications


def _evaluation_case(raw: dict[str, Any]) -> EvaluationCase:
    details = _mapping(raw.get("details"), "comparison requires detailed private reports")
    envelope = Envelope.model_validate(details.get("input"), strict=True)
    expectations = []
    for value in _sequence(details.get("expectations"), "case expectations are missing"):
        expected = _mapping(value, "case expectation is invalid")
        name = expected.get("name")
        path = expected.get("path")
        case_id = raw.get("id")
        if "expected" not in expected or not isinstance(name, str) or not isinstance(path, str):
            raise ValueError("case expectation has no explicit gold")
        expectations.append(
            Expectation(
                name,
                path,
                expected["expected"],
                expected.get("comparison", "exact"),
                expected.get("scorer"),
            )
        )
    if not isinstance(case_id, str):
        raise ValueError("case identity is invalid")
    return EvaluationCase(case_id, envelope, tuple(expectations))


def _metric_summary(cases: list[dict[str, Any]], specification: MetricSpec) -> dict[str, JsonValue]:
    observations: list[tuple[MetricObservation, ...]] = []
    for case in cases:
        typed_case = _evaluation_case(case)
        result = _validated_result(case)
        if result is None:
            document = None
        else:
            document = snapshot_execution(result)[0]
        status = cast(str, case["status"])
        observations.append(observe_metrics((specification,), typed_case, document, status))
    report = summarize_metrics((specification,), observations)[0]
    return {
        "name": report.name,
        "support": report.support,
        "excluded": report.excluded,
        "observed": report.observed,
        "correct": report.correct,
        "accuracy": report.accuracy,
        "coverage": report.coverage,
    }


def _delta(candidate: int | float | None, baseline: int | float | None) -> int | float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _numeric(value: JsonValue) -> int | float | None:
    if value is None or type(value) in {int, float}:
        return cast(int | float | None, value)
    raise ValueError("derived report value is invalid")


def _usage(cases: list[dict[str, Any]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for field in _USAGE_FIELDS:
        values = []
        for case in cases:
            execution_result = _validated_result(case)
            raw = (
                execution_result.execution.usage.model_dump(mode="json")
                if execution_result is not None
                else None
            )
            value = raw.get(field) if raw is not None else None
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("case usage is invalid")
            values.append(value)
        known = [value for value in values if value is not None]
        result[field] = {
            "observed": len(known),
            "unknown": len(values) - len(known),
            "total": sum(known) if len(known) == len(values) else None,
            "known_total": sum(known) if known else None,
        }
    return result


def _usage_comparison(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> dict[str, JsonValue]:
    before = _usage(baseline)
    after = _usage(candidate)
    result: dict[str, JsonValue] = {}
    for field in _USAGE_FIELDS:
        baseline_field = cast(dict[str, JsonValue], before[field])
        candidate_field = cast(dict[str, JsonValue], after[field])
        result[field] = {
            "baseline": baseline_field,
            "candidate": candidate_field,
            "total_delta": _delta(
                _numeric(candidate_field["total"]), _numeric(baseline_field["total"])
            ),
        }
    return result


def _latency(
    baseline_mode: str,
    candidate_mode: str,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> dict[str, JsonValue]:
    if baseline_mode == "replay" or candidate_mode == "replay":
        return {"available": False, "reason": "replay_timing_not_comparable"}
    if baseline_mode != candidate_mode:
        return {"available": False, "reason": "artifact_modes_differ"}
    before = [case.get("elapsed_seconds") for case in baseline]
    after = [case.get("elapsed_seconds") for case in candidate]
    if not all(
        type(value) in {int, float}
        and math.isfinite(cast(float, value))
        and cast(float, value) >= 0
        for value in before + after
    ):
        raise ValueError("case timing is invalid")
    baseline_mean = sum(cast(list[float], before)) / len(before)
    candidate_mean = sum(cast(list[float], after)) / len(after)
    return {
        "available": True,
        "baseline_mean_seconds": baseline_mean,
        "candidate_mean_seconds": candidate_mean,
        "mean_delta_seconds": candidate_mean - baseline_mean,
    }


def _compare_suite(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    baseline_mode: str,
    candidate_mode: str,
) -> dict[str, JsonValue]:
    _same(_identity(candidate), _identity(baseline), "report suite or target differs")
    _same(candidate.get("suite_revision"), baseline.get("suite_revision"), "suite revision differs")
    _same(candidate.get("scorer_revisions"), baseline.get("scorer_revisions"), "scorers differ")
    metric_specs = _metric_specs(baseline)
    if _metric_specs(candidate) != metric_specs:
        raise ValueError("metric catalog differs")
    before_cases = [
        _mapping(value, "baseline case is invalid")
        for value in _sequence(baseline.get("cases"), "baseline cases are missing")
    ]
    after_cases = [
        _mapping(value, "candidate case is invalid")
        for value in _sequence(candidate.get("cases"), "candidate cases are missing")
    ]
    if not before_cases or len(before_cases) != len(after_cases):
        raise ValueError("report attempt count differs")
    if _attempt_contract(baseline, before_cases) != _attempt_contract(candidate, after_cases):
        raise ValueError("report repeat or source case count differs")

    compared_cases: list[JsonValue] = []
    totals = {"improved": 0, "regressed": 0, "mixed": 0, "unchanged": 0}
    before_covered = after_covered = before_passed = after_passed = total_checks = 0
    for before, after in zip(before_cases, after_cases, strict=True):
        if not isinstance(before.get("id"), str) or not isinstance(after.get("id"), str):
            raise ValueError("case identity is invalid")
        _same(
            (after["id"], _repetition(after)),
            (before["id"], _repetition(before)),
            "case identity or repetition differs",
        )
        _same(_case_semantics(after), _case_semantics(before), "case input or gold differs")
        # Reuse public validators for finite/depth-bounded input and typed gold,
        # including suites that do not declare aggregate metrics.
        _evaluation_case(before)
        _evaluation_case(after)
        _validated_result(before)
        _validated_result(after)
        before_checks = _check_semantics(before)
        after_checks = _check_semantics(after)
        _same(
            [(name, path) for name, path, _, _, _ in after_checks],
            [(name, path) for name, path, _, _, _ in before_checks],
            "case check semantics differ",
        )
        if not before_checks:
            raise ValueError("case has no checks")
        check_changes = [
            _OUTCOME_RANK[cast(str, candidate_check[4])]
            - _OUTCOME_RANK[cast(str, baseline_check[4])]
            for baseline_check, candidate_check in zip(before_checks, after_checks, strict=True)
        ]
        baseline_status = cast(str, before["status"])
        candidate_status = cast(str, after["status"])
        status_change = _STATUS_RANK[candidate_status] - _STATUS_RANK[baseline_status]
        changes = [*check_changes, status_change] if status_change else check_changes
        outcome: CaseChange = (
            "mixed"
            if any(change > 0 for change in changes) and any(change < 0 for change in changes)
            else "improved"
            if any(change > 0 for change in changes)
            else "regressed"
            if any(change < 0 for change in changes)
            else "unchanged"
        )
        totals[outcome] += 1
        before_covered_case = sum(check[4] in {"passed", "failed"} for check in before_checks)
        after_covered_case = sum(check[4] in {"passed", "failed"} for check in after_checks)
        before_passed_case = sum(check[4] == "passed" for check in before_checks)
        after_passed_case = sum(check[4] == "passed" for check in after_checks)
        total_checks += len(before_checks)
        before_covered += before_covered_case
        after_covered += after_covered_case
        before_passed += before_passed_case
        after_passed += after_passed_case
        compared_cases.append(
            {
                "id": before["id"],
                "repetition": _repetition(before),
                "outcome": outcome,
                "check_outcome": (
                    "mixed"
                    if any(change > 0 for change in check_changes)
                    and any(change < 0 for change in check_changes)
                    else "improved"
                    if any(change > 0 for change in check_changes)
                    else "regressed"
                    if any(change < 0 for change in check_changes)
                    else "unchanged"
                ),
                "baseline_status": baseline_status,
                "candidate_status": candidate_status,
                "checks": {
                    "total": len(before_checks),
                    "improved": sum(change > 0 for change in check_changes),
                    "regressed": sum(change < 0 for change in check_changes),
                    "unchanged": sum(change == 0 for change in check_changes),
                    "baseline_covered": before_covered_case,
                    "candidate_covered": after_covered_case,
                    "baseline_passed": before_passed_case,
                    "candidate_passed": after_passed_case,
                    "baseline_outcomes": {
                        check_outcome: sum(check[4] == check_outcome for check in before_checks)
                        for check_outcome in _OUTCOME_RANK
                    },
                    "candidate_outcomes": {
                        check_outcome: sum(check[4] == check_outcome for check in after_checks)
                        for check_outcome in _OUTCOME_RANK
                    },
                },
            }
        )

    metrics: list[JsonValue] = []
    for specification in metric_specs:
        before_metric = _metric_summary(before_cases, specification)
        after_metric = _metric_summary(after_cases, specification)
        metrics.append(
            {
                "name": specification.name,
                "baseline": before_metric,
                "candidate": after_metric,
                "accuracy_delta": _delta(
                    _numeric(after_metric["accuracy"]), _numeric(before_metric["accuracy"])
                ),
                "coverage_delta": _delta(
                    _numeric(after_metric["coverage"]), _numeric(before_metric["coverage"])
                ),
            }
        )
    return {
        "suite_name": baseline["suite_name"],
        "suite_fingerprint": baseline["suite_fingerprint"],
        "target_workflow": baseline.get("target_workflow"),
        "target_flow": baseline.get("target_flow"),
        "target_step": baseline.get("target_step"),
        "baseline_variant": {
            "name": baseline.get("variant_name"),
            "revision": baseline.get("variant_revision"),
            "configuration_revision": baseline.get("configuration_revision"),
        },
        "candidate_variant": {
            "name": candidate.get("variant_name"),
            "revision": candidate.get("variant_revision"),
            "configuration_revision": candidate.get("configuration_revision"),
        },
        "attempt_count": len(before_cases),
        "case_changes": totals,
        "checks": {
            "total": total_checks,
            "baseline_covered": before_covered,
            "candidate_covered": after_covered,
            "coverage_delta": (after_covered - before_covered) / total_checks,
            "baseline_passed": before_passed,
            "candidate_passed": after_passed,
            "pass_rate_delta": (after_passed - before_passed) / total_checks,
        },
        "execution": {
            "baseline": {
                status: sum(case["status"] == status for case in before_cases)
                for status in _STATUS_RANK
            },
            "candidate": {
                status: sum(case["status"] == status for case in after_cases)
                for status in _STATUS_RANK
            },
            "failure_rate_delta": (
                sum(case["status"] in {"failed", "cancelled", "error"} for case in after_cases)
                - sum(case["status"] in {"failed", "cancelled", "error"} for case in before_cases)
            )
            / len(before_cases),
            "review_rate_delta": (
                sum(case["status"] == "needs_review" for case in after_cases)
                - sum(case["status"] == "needs_review" for case in before_cases)
            )
            / len(before_cases),
        },
        "metrics": metrics,
        "latency": _latency(baseline_mode, candidate_mode, before_cases, after_cases),
        "usage": _usage_comparison(before_cases, after_cases),
        "cases": compared_cases,
    }


def compare_reports(candidate: Path, baseline: Path) -> ReportComparison:
    """Compare two saved full reports without clients, model calls, or mutation.

    Dataset, suite, target, case/input/gold, scorer, and metric semantics must
    match. Variant and configuration revisions may differ because those are the
    intended subjects of comparison. Every count is derived from case records;
    saved aggregate summaries are not trusted as comparison input.
    """
    candidate_raw = _artifact(candidate)
    baseline_raw = _artifact(baseline)
    _same(candidate_raw["dataset"], baseline_raw["dataset"], "dataset identity differs")
    candidate_reports = cast(list[dict[str, Any]], candidate_raw["reports"])
    baseline_reports = cast(list[dict[str, Any]], baseline_raw["reports"])
    if len(candidate_reports) != len(baseline_reports):
        raise ValueError("report suite count differs")
    suites = [
        _compare_suite(
            before,
            after,
            baseline_mode=baseline_raw["mode"],
            candidate_mode=candidate_raw["mode"],
        )
        for before, after in zip(baseline_reports, candidate_reports, strict=True)
    ]
    case_changes = {
        outcome: sum(cast(dict[str, int], suite["case_changes"])[outcome] for suite in suites)
        for outcome in ("improved", "regressed", "mixed", "unchanged")
    }
    document: dict[str, JsonValue] = {
        "kind": "evaluation_report_comparison",
        "dataset": cast(JsonValue, baseline_raw["dataset"]),
        "baseline": {"path": str(baseline.absolute()), "mode": baseline_raw["mode"]},
        "candidate": {"path": str(candidate.absolute()), "mode": candidate_raw["mode"]},
        "suite_count": len(suites),
        "attempt_count": sum(cast(int, suite["attempt_count"]) for suite in suites),
        "case_changes": cast(JsonValue, case_changes),
        "suites": cast(JsonValue, suites),
        "interpretation": "descriptive_only_no_statistical_significance",
    }
    return ReportComparison(document)


__all__ = ["ReportComparison", "compare_reports"]

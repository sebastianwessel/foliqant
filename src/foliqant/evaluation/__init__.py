"""Golden-case evaluation of in-memory pipelines and isolated steps."""

from .analysis import ReportComparison, compare_reports
from .contracts import (
    CaseDetails,
    CaseReport,
    CheckDetails,
    CheckReport,
    CheckSummary,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    EvaluationVariant,
    Expectation,
    FlowReport,
    FlowSummary,
    RegisteredScorer,
    StepReport,
    StepSummary,
)
from .groups import EvaluationGroupReport, group_report
from .metrics import LabelCounts, MetricReport, MetricSpec, RateSummary
from .runner import compare_variants, evaluate
from .summaries import LatencySummary, UsageCountSummary, UsageSummary

__all__ = [
    "CaseDetails",
    "CaseReport",
    "CheckDetails",
    "CheckReport",
    "CheckSummary",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationGroupReport",
    "EvaluationSuite",
    "EvaluationVariant",
    "Expectation",
    "FlowReport",
    "FlowSummary",
    "LabelCounts",
    "LatencySummary",
    "MetricReport",
    "MetricSpec",
    "RateSummary",
    "RegisteredScorer",
    "ReportComparison",
    "StepReport",
    "StepSummary",
    "UsageCountSummary",
    "UsageSummary",
    "compare_reports",
    "compare_variants",
    "evaluate",
    "group_report",
]

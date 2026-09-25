"""Golden-case evaluation of in-memory pipelines and isolated steps."""

from .analysis import ReportComparison, compare_reports
from .checkpoint import CheckpointMismatchError, EvaluationCheckpoint, EvaluationProgress
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
from .intervals import Interval, paired_difference_interval, ratio_interval
from .metrics import FieldCounts, LabelCounts, MetricReport, MetricSpec, RateSummary
from .runner import compare_variants, evaluate
from .scoped import flow_input
from .summaries import CostSummary, LatencySummary, UsageCountSummary, UsageSummary

__all__ = [
    "CaseDetails",
    "CaseReport",
    "CheckDetails",
    "CheckReport",
    "CheckSummary",
    "CheckpointMismatchError",
    "CostSummary",
    "EvaluationCase",
    "EvaluationCheckpoint",
    "EvaluationGroupReport",
    "EvaluationProgress",
    "EvaluationReport",
    "EvaluationSuite",
    "EvaluationVariant",
    "Expectation",
    "FieldCounts",
    "FlowReport",
    "FlowSummary",
    "Interval",
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
    "flow_input",
    "group_report",
    "paired_difference_interval",
    "ratio_interval",
]

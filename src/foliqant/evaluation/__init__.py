"""Golden-case evaluation of in-memory pipelines and isolated steps."""

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
    RegisteredScorer,
    StepReport,
    StepSummary,
)
from .metrics import LabelCounts, MetricReport, MetricSpec
from .runner import compare_variants, evaluate

__all__ = [
    "CaseDetails",
    "CaseReport",
    "CheckDetails",
    "CheckReport",
    "CheckSummary",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationSuite",
    "EvaluationVariant",
    "Expectation",
    "LabelCounts",
    "MetricReport",
    "MetricSpec",
    "RegisteredScorer",
    "StepReport",
    "StepSummary",
    "compare_variants",
    "evaluate",
]

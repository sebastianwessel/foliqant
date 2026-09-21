"""Golden-case evaluation of in-memory pipelines and isolated steps."""

from .contracts import (
    CaseReport,
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
from .runner import compare_variants, evaluate

__all__ = [
    "CaseReport",
    "CheckReport",
    "CheckSummary",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationSuite",
    "EvaluationVariant",
    "Expectation",
    "RegisteredScorer",
    "StepReport",
    "StepSummary",
    "compare_variants",
    "evaluate",
]

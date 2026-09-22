"""Runtime decision projection over native inputs and strict output validation."""

from .native import ValidatedDecision, build_decision_input, validate_decision_result

__all__ = ["ValidatedDecision", "build_decision_input", "validate_decision_result"]

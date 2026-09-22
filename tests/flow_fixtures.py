"""Direct constructors for new sequential flow plans used by adapter tests."""

from foliqant.core.plan import CompiledStep, FlowPlan, SourceLocation, TransitionTargetPlan


def operation_flow(*steps: CompiledStep, name: str = "main") -> FlowPlan:
    """Wrap authored operations in one explicitly completed flow."""
    return FlowPlan(
        name=name,
        input=(),
        steps=steps,
        input_schema_path=None,
        input_schema=None,
        output=None,
        transition=TransitionTargetPlan(outcome="completed"),
        on_unresolved=None,
        location=SourceLocation("workflow.yaml", 1, 1),
    )

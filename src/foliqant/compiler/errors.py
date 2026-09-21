"""Content-free compiler diagnostics safe for logs and boundary adapters."""

from foliqant.core.plan import SourceLocation

_HINTS = {
    "invalid_contract": "Check the field type and supported fields in the authoring schema.",
    "invalid_yaml": "Use unique mapping keys and plain YAML without aliases or custom tags.",
    "mixed_step_sources": "Keep steps inline in workflow.yaml or in step files, not both.",
    "ambiguous_step_name": "Use the inline mapping key as the step name; remove the name field.",
    "ambiguous_instructions": "Use either the Markdown body or the instructions field.",
    "missing_transition": "Set next or exhaustive on_answer routes to an explicit finish step.",
    "invalid_model_options": "Use options supported by the selected profile and compatible values.",
    "unknown_model": "Declare the selected model alias or configure a workflow default.",
    "unsupported_model_capability": "Select a profile supporting this output and tool policy.",
    "dangling_pointer": "Select a declared payload or step-result path supported by its schema.",
    "incompatible_binding_type": "Bind a value whose JSON type matches the destination field.",
    "invalid_input_bindings": "Supply required input fields and remove forbidden fields.",
    "unavailable_step_reference": "Use a dominating earlier step or an explicit optional default.",
    "incompatible_output_binding": "Use output available on every terminal path or set a default.",
    "invalid_json_schema": "Use a bounded valid JSON Schema object.",
    "invalid_schema_path": "Use a bundle-local schema path without external identifiers.",
    "invalid_schema_reference": "Point each reference to an existing local schema node.",
    "missing_start": "Set start to the name of a declared step.",
    "missing_step": "Declare the referenced step in the selected step source.",
    "workflow_cycle": "Remove cycles so every reachable route terminates.",
    "unreachable_step": "Connect the step from start or remove it.",
    "incomplete_answer_routes": "Provide exactly one route for every possible answer.",
}


class CompilationError(Exception):
    """Safe source coordinate, field, stable reason and corrective hint.

    ``field`` contains only compiler-owned field names and wildcard placeholders,
    never rejected values, arbitrary mapping keys or Pydantic exception text.
    """

    def __init__(self, reason: str, location: SourceLocation, *, field: str | None = None) -> None:
        self.reason = reason
        self.location = location
        self.field = field
        self.hint = _HINTS.get(
            reason, "Check the referenced configuration against its authoring schema."
        )
        super().__init__("The workflow configuration is invalid.")

"""Content-free compiler diagnostics safe for logs and boundary adapters."""

from foliqant.core.plan import SourceLocation

_HINTS = {
    "invalid_contract": "Check the field type and supported fields in the authoring schema.",
    "invalid_yaml": "Use unique mapping keys and plain YAML without aliases or custom tags.",
    "ambiguous_instructions": "Use either the Markdown body or the instructions field.",
    "invalid_definition_file": "Check the explicitly referenced UTF-8 definition file.",
    "missing_step_file": "Add <id>.step.yaml/.md or <id>/step.yaml/.md beside flow.yaml.",
    "ambiguous_step_file": "Keep one conventional file per step or select an explicit definition.",
    "invalid_step_file": "Check the explicitly referenced YAML or Markdown step file.",
    "invalid_flow_path": "Reference a YAML flow definition inside the configuration directory.",
    "invalid_step_path": "Reference a YAML or Markdown step inside its flow definition directory.",
    "invalid_prompt": "Use declared input names in balanced placeholders; escape literal braces.",
    "invalid_model_options": "Use options supported by the selected profile and compatible values.",
    "unknown_model": "Declare the selected model alias or configure a workflow default.",
    "unsupported_model_capability": "Select a profile supporting this output and tool policy.",
    "dangling_pointer": "Select a declared payload or scoped result path supported by its schema.",
    "incompatible_binding_type": "Bind a value whose JSON type matches the destination field.",
    "incompatible_route_type": "Route on a string or null with an explicit default target.",
    "invalid_input_bindings": "Supply required input fields and remove forbidden fields.",
    "unavailable_step_reference": "Use an earlier local step or an explicit optional default.",
    "invalid_flow_reference": "Select a declared flow through /flows/<instance>/result.",
    "unavailable_flow_reference": "Use a flow available on every incoming path or set a default.",
    "incompatible_output_binding": "Use output available on early review or set a default.",
    "invalid_json_schema": "Use a bounded valid JSON Schema object.",
    "invalid_schema_path": "Use a flow-local schema path without external identifiers.",
    "invalid_schema_reference": "Point each reference to an existing local schema node.",
    "missing_start": "Set start to the ID of a declared flow instance.",
    "missing_flow": "Declare every flow targeted by a transition or unresolved route.",
    "workflow_cycle": "Remove cycles so every reachable flow route terminates.",
    "unreachable_flow": "Connect the flow from start or remove it.",
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

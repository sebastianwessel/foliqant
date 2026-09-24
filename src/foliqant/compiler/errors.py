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
    "invalid_routed_flow": "Route only to noncallable flows; invoke callable flows in collections.",
    "invalid_callable_flow": "Allowlist only declared callable flows in a collection step.",
    "invalid_collection_items": "Use bounded unique item IDs, allowed flows and object inputs.",
    "invalid_collection_input": "Match each collection item's input to its callable flow schema.",
    "collection_depth_exceeded": "Keep callable-flow nesting within sixteen collection levels.",
    "workflow_cycle": "Remove route and callable-flow cycles so every invocation terminates.",
    "unreachable_flow": "Connect the flow from start or remove it.",
    "unknown_field": "Remove the field; bindings with `default` are optional (no `optional`).",
    "unknown_handler": "Declare the handler under `handlers` in settings.yaml and register it.",
    "invalid_condition": "Use one source (binding or literal) and one operator, or all/any/not.",
    "unsafe_pattern": "Avoid unbounded quantifiers around ambiguous groups; use possessive `++`.",
    "condition_type_mismatch": "Compare with an operand the bound field's type can satisfy.",
    "route_without_otherwise": "End a route with one entry without `when` (the otherwise target).",
    "misplaced_otherwise": "Only the last route entry may omit `when`.",
    "unmatched_case": "Use case keys from the routed field's allowed values (enum or const).",
    "default_covers_mismatch": "List exactly the allowed values without a case in default_covers.",
    "invalid_repeat": "Use a callable retry flow once, and retry_input keys declared in input.",
    "repeat_budget": "Lower max_attempts or raise execution.max_steps for the worst case.",
    "invalid_default_review_route": "Target a flow the inheriting flow can reach without a cycle.",
    "review_route_to_self": "Route review to another flow or an outcome, never to the same flow.",
    "handler_contract_mismatch": "Register the handler with its declared schemas and effect.",
    "missing_handler_registration": "Register every handler declared in settings.yaml.",
    "invalid_handler_schema": "Declare a self-contained JSON Schema object or a local file.",
    "uncovered_value": "Add a case or list the value in default_covers.",
    "condition_always_false": "Compare with a value the field can hold, or remove the condition.",
    "condition_always_true": "Use `present` for presence tests, or remove the condition.",
    "route_unreachable_entry": "Remove or reorder the route entry that can never be selected.",
    "repeat_without_retry": "Add a retry flow that changes the repeated flow's input.",
    "unused_llm_input": "Reference every declared input in the prompt or remove it.",
    "collection_budget": "Lower max_items or raise execution.max_steps.",
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

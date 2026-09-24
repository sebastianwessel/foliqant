"""Content-free compiler problems safe for logs, terminals and boundary adapters.

Every problem renders as ``<file>:<line>:<column>: <code> at <field>: <message>
(hint: <hint>)``. Messages name configured identifiers and authored values only,
never runtime data, credentials, prompt text or raw exception content.
"""

from collections.abc import Iterable

from foliqant.core.plan import Diagnostic, DiagnosticLevel, SourceLocation

_HINTS = {
    "invalid_contract": "Check the field type and supported fields in the authoring schema.",
    "invalid_yaml": "Use unique mapping keys and plain YAML without aliases or custom tags.",
    "ambiguous_instructions": "Use either the Markdown body or the instructions field.",
    "invalid_definition_file": "Check the explicitly referenced UTF-8 definition file.",
    "missing_step_file": "Add <id>.step.yaml/.md or <id>/step.yaml/.md beside flow.yaml.",
    "ambiguous_step_file": "Keep one conventional file per step or select an explicit definition.",
    "invalid_step_file": "Check the explicitly referenced YAML or Markdown step file.",
    "missing_frontmatter": "Start a Markdown step with a `---` YAML frontmatter block.",
    "unexpected_step_body": "Only `llm` and `decision` Markdown steps may have a body.",
    "invalid_flow_path": "Reference a YAML flow definition inside the configuration directory.",
    "invalid_step_path": "Reference a YAML or Markdown step inside its flow definition directory.",
    "invalid_prompt": "Use declared input names in balanced placeholders; escape literal braces.",
    "invalid_question": "Check the question fields against the decision question types.",
    "unknown_question_source": "Allow only source IDs declared under `sources`.",
    "unknown_tool_server": "Declare the MCP server under `mcp` in settings.yaml.",
    "unknown_tool": "Use a tool declared in the server's catalog.",
    "invalid_model_options": "Use options supported by the selected profile and compatible values.",
    "unknown_model": "Declare the selected model alias or configure a workflow default.",
    "unsupported_model_capability": "Select a profile supporting this output and tool policy.",
    "dangling_pointer": "Select a declared payload or scoped result path supported by its schema.",
    "incompatible_binding_type": "Bind a value whose JSON type matches the destination field.",
    "incompatible_route_type": "Route `cases` on a string or null field; use `route` otherwise.",
    "invalid_input_bindings": "Supply required input fields and remove forbidden fields.",
    "unavailable_step_reference": "Use an earlier local step or an explicit optional default.",
    "invalid_flow_reference": "Select a declared flow through /flows/<instance>/result.",
    "unavailable_flow_reference": "Use a flow available on every incoming path or set a default.",
    "unavailable_value": "Set a `default`: the value is missing on some path.",
    "incompatible_output_binding": "Use output available on early review or set a default.",
    "invalid_json_schema": "Use a bounded valid JSON Schema object.",
    "invalid_schema_path": "Use a flow-local schema path without external identifiers.",
    "invalid_schema_reference": "Point each reference to an existing local schema node.",
    "invalid_registry": "Use lowercase snake_case IDs for models, handlers and servers.",
    "invalid_tool_schema": "Declare self-contained JSON Schema objects in the tool catalog.",
    "missing_workflow": "Add workflow.yaml to the workflow directory.",
    "missing_start": "Set start to the ID of a declared routed flow instance.",
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
    "review_completes_run": "Route review to `outcome: needs_review` or to a review flow.",
    "handler_contract_mismatch": "Register the handler with its declared schemas and effect.",
    "missing_handler_registration": "Register every handler declared in settings.yaml.",
    "invalid_handler_schema": "Declare a self-contained JSON Schema object or a local file.",
    "invalid_deployment": "Check settings.yaml against the deployment schema and its paths.",
    "uncovered_value": "Add a case or list the value in default_covers.",
    "condition_always_false": "Compare with a value the field can hold, or remove the condition.",
    "condition_always_true": "Use `present` for presence tests, or remove the condition.",
    "route_unreachable_entry": "Remove or reorder the route entry that can never be selected.",
    "repeat_without_retry": "Add a retry flow that changes the repeated flow's input.",
    "unused_llm_input": "Reference every declared input in the prompt or remove it.",
    "collection_budget": "Lower max_items or raise execution.max_steps.",
    "run_budget": "Shorten the path, lower repeat/collection bounds or raise execution.max_steps.",
    "case_on_unknown_type": "Declare an enum or const for the routed field to check its cases.",
    "review_ends_run": "Declare `on_unresolved` (a review flow or `outcome: needs_review`).",
    "empty_text_source": "Require a nonempty string, add a default or use `format: json`.",
}

_MESSAGES = {
    "invalid_contract": "The value does not match the authoring schema of this field.",
    "invalid_yaml": "The file is not valid strict YAML.",
    "ambiguous_instructions": "The step has both a Markdown body and an `instructions` field.",
    "invalid_definition_file": "The referenced definition file cannot be read as UTF-8.",
    "missing_step_file": "No step definition file exists for this step ID.",
    "ambiguous_step_file": "Several conventional step files exist for this step ID.",
    "invalid_step_file": "The step file cannot be read.",
    "missing_frontmatter": "The Markdown step has no YAML frontmatter.",
    "unexpected_step_body": "This step type cannot take a Markdown body.",
    "invalid_flow_path": "The flow definition path is not a YAML file inside the configuration.",
    "invalid_step_path": "The step definition path is not a YAML or Markdown file in scope.",
    "invalid_prompt": "The prompt template has an unknown or malformed placeholder.",
    "invalid_question": "The decision question is invalid.",
    "unknown_question_source": "A question allows a source that the step does not declare.",
    "unknown_tool_server": "The MCP server is not declared in settings.yaml.",
    "unknown_tool": "The tool is not in the declared catalog of its server.",
    "invalid_model_options": "The model options do not fit the selected profile.",
    "unknown_model": "The step selects no declared model profile.",
    "unsupported_model_capability": "The selected model profile does not support this step.",
    "dangling_pointer": "The pointer can never resolve: the path is not in the known schema.",
    "incompatible_binding_type": "The bound value's JSON type cannot match the destination.",
    "incompatible_route_type": "A `cases` binding must be a string or null on every path.",
    "invalid_input_bindings": "The bindings do not match the declared input schema.",
    "unavailable_step_reference": "The pointer reads a step that may not have run at this point.",
    "invalid_flow_reference": "The pointer does not select a readable flow record.",
    "unavailable_flow_reference": "The pointer reads a flow that does not run on every path here.",
    "unavailable_value": "The required pointer can be missing at run time on some path.",
    "incompatible_output_binding": "The flow output reads a value that may be missing on review.",
    "invalid_json_schema": "The JSON Schema is invalid or unbounded.",
    "invalid_schema_path": "The schema path is remote, absolute or outside its bundle.",
    "invalid_schema_reference": "A schema reference points to no local schema node.",
    "invalid_registry": "A registry entry has an invalid ID.",
    "invalid_tool_schema": "A tool catalog schema is invalid.",
    "missing_workflow": "The workflow directory has no readable workflow.yaml.",
    "missing_start": "The workflow has no valid start flow.",
    "missing_flow": "A route targets a flow that is not declared.",
    "invalid_routed_flow": "A route targets a callable flow.",
    "invalid_callable_flow": "A collection allowlists a flow that is not callable.",
    "invalid_collection_items": "The literal collection items are invalid.",
    "invalid_collection_input": "A literal collection item input violates its flow schema.",
    "collection_depth_exceeded": "Callable flows nest deeper than sixteen collection levels.",
    "workflow_cycle": "The flow graph contains a cycle, so a run might not terminate.",
    "unreachable_flow": "The flow can never run from any start candidate.",
    "unknown_field": "The field is not supported here.",
    "unknown_handler": "The handler is not declared under `handlers` in settings.yaml.",
    "invalid_condition": "The condition is malformed.",
    "unsafe_pattern": "The `matches` pattern can backtrack without bound.",
    "condition_type_mismatch": "The operator can never hold for the bound field's type.",
    "route_without_otherwise": "The last route entry has `when`; a run could find no target.",
    "misplaced_otherwise": "A route entry without `when` is not the last entry.",
    "unmatched_case": "A case key is not an allowed value of the routed field.",
    "default_covers_mismatch": "`default_covers` differs from the values that reach `default`.",
    "invalid_repeat": "The repeat configuration is invalid.",
    "repeat_budget": "The repeat's worst case exceeds execution.max_steps.",
    "invalid_default_review_route": "The inherited review route creates a cycle.",
    "review_route_to_self": "The review route targets its own flow.",
    "review_completes_run": "A review route completes the run directly.",
    "handler_contract_mismatch": "The handler registration differs from its declaration.",
    "missing_handler_registration": "A declared handler has no host registration.",
    "invalid_handler_schema": "The declared handler schema is invalid.",
    "invalid_deployment": "The deployment settings are invalid.",
}


def hint_for(code: str) -> str:
    """The corrective hint of a stable problem code."""
    return _HINTS.get(code, "Check the referenced configuration against its authoring schema.")


def message_for(code: str) -> str:
    """The default description of an error code."""
    return _MESSAGES.get(code, "The workflow configuration is invalid.")


def render_problem(problem: Diagnostic) -> str:
    """``<file>:<line>:<column>: <code> at <field>: <message> (hint: <hint>)``."""
    where = f"{problem.location.path}:{problem.location.line}:{problem.location.column}"
    field = f" at {problem.field}" if problem.field else ""
    hint = problem.hint or hint_for(problem.code)
    return f"{where}: {problem.code}{field}: {problem.message} (hint: {hint})"


def render_problems(problems: Iterable[Diagnostic]) -> str:
    """One rendered line per problem."""
    return "\n".join(render_problem(problem) for problem in problems)


class CompilationError(Exception):
    """An invalid configuration with every problem that makes it invalid.

    ``problems`` are the findings that fail preparation: the compiler error, or
    every warning under ``strict``. ``diagnostics`` holds every finding known
    when the error was raised, problems included, for structured logging.
    ``reason``, ``location``, ``field``, ``hint`` and ``message`` describe the
    first problem. ``str(error)`` renders one line per problem. Fields contain
    only compiler-owned names, validated identifiers and wildcards, never
    rejected values or Pydantic exception text.
    """

    def __init__(
        self,
        reason: str,
        location: SourceLocation,
        *,
        field: str | None = None,
        message: str | None = None,
        level: DiagnosticLevel = "error",
    ) -> None:
        problem = Diagnostic(
            reason, level, location, message or message_for(reason), field, hint_for(reason)
        )
        self.problems: tuple[Diagnostic, ...] = (problem,)
        self.diagnostics: tuple[Diagnostic, ...] = (problem,)
        super().__init__(render_problem(problem))

    @classmethod
    def from_problems(
        cls, problems: Iterable[Diagnostic], diagnostics: Iterable[Diagnostic] = ()
    ) -> "CompilationError":
        """Fail with several findings, e.g. every warning of a strict preparation."""
        selected = tuple(item if item.hint is not None else _with_hint(item) for item in problems)
        if not selected:
            raise ValueError("a compilation error needs at least one problem")
        error = cls.__new__(cls)
        error.problems = selected
        known = tuple(item if item.hint is not None else _with_hint(item) for item in diagnostics)
        error.diagnostics = known or selected
        Exception.__init__(error, render_problems(selected))
        return error

    @property
    def reason(self) -> str:
        return self.problems[0].code

    @property
    def location(self) -> SourceLocation:
        return self.problems[0].location

    @property
    def field(self) -> str | None:
        return self.problems[0].field

    @property
    def hint(self) -> str:
        return self.problems[0].hint or hint_for(self.reason)

    @property
    def message(self) -> str:
        return self.problems[0].message

    def __str__(self) -> str:
        return render_problems(self.problems)


def _with_hint(item: Diagnostic) -> Diagnostic:
    return Diagnostic(
        item.code, item.level, item.location, item.message, item.field, hint_for(item.code)
    )


__all__ = [
    "CompilationError",
    "hint_for",
    "message_for",
    "render_problem",
    "render_problems",
]

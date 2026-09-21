"""Compile one local workflow bundle into an immutable execution plan."""

import hashlib
import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Literal, Never, Protocol, cast

from jsonschema import Draft202012Validator, SchemaError
from pydantic import TypeAdapter, ValidationError

from foliqant.contracts.models import ModelConfig
from foliqant.contracts.workflow import (
    Binding,
    ChoiceQuestionShorthand,
    DecisionStepAuthoring,
    DeclaredToolCatalog,
    FinishStepAuthoring,
    HandlerStepAuthoring,
    LiteralBinding,
    LlmStepAuthoring,
    McpStepAuthoring,
    MultiselectQuestionShorthand,
    OrdinalQuestionShorthand,
    PredicateQuestionShorthand,
    RequestUnitsQuestionShorthand,
    StepAuthoring,
    UnresolvedRouting,
    WorkflowAuthoring,
)
from foliqant.core.errors import ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import (
    BindingPlan,
    CategoryPlan,
    CompiledStep,
    DecisionIssue,
    DecisionOptionPlan,
    DecisionQuestionPlan,
    DecisionStepPlan,
    FallbackPlan,
    FinishStepPlan,
    HandlerStepPlan,
    LlmStepPlan,
    McpStepPlan,
    SchemaResourcePlan,
    SourceLocation,
    ToolPolicyPlan,
    UnresolvedRoutingPlan,
    WorkflowPlan,
)
from foliqant.decisions import (
    ChoiceQuestion,
    MultiselectQuestion,
    OrdinalQuestion,
    PredicateQuestion,
    RequestUnitsQuestion,
)

from ._loader import load_step, load_yaml
from .errors import CompilationError
from .models import ModelRegistry
from .schema_helpers import (
    resolve_schema_fragment as _resolve_confined_schema_fragment,
)
from .schema_helpers import (
    validate_confined_tool_schema,
)
from .schema_helpers import (
    walk_schema_nodes as _walk_schema_nodes,
)
from .static_schema import SchemaView, incompatible_types, json_type, schema_types

_WORKFLOW_ADAPTER = TypeAdapter(WorkflowAuthoring)
_STEP_ADAPTER: TypeAdapter[StepAuthoring] = TypeAdapter(StepAuthoring)
_TOOL_CATALOG_ADAPTER = TypeAdapter(DeclaredToolCatalog)
type NativeQuestion = (
    ChoiceQuestion
    | MultiselectQuestion
    | PredicateQuestion
    | OrdinalQuestion
    | RequestUnitsQuestion
)


class HandlerSchemas(Protocol):
    """Structural schema interface implemented by trusted handler registrations."""

    @property
    def input_schema(self) -> FrozenObject: ...

    @property
    def output_schema(self) -> FrozenObject: ...


def _fail(reason: str, location: SourceLocation, field: str | None = None) -> Never:
    raise CompilationError(reason, location, field=field) from None


def _validate[T](adapter: TypeAdapter[T], value: object, location: SourceLocation) -> T:
    try:
        return adapter.validate_python(value, strict=True)
    except ValidationError as error:
        issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
        allowed = {
            "version",
            "name",
            "start",
            "defaults",
            "model",
            "input_schema",
            "output",
            "steps",
            "type",
            "next",
            "on_unresolved",
            "fallback",
            "category",
            "on",
            "sources",
            "question",
            "questions",
            "instructions",
            "on_answer",
            "input",
            "tools",
            "server",
            "tool",
            "arguments",
            "handler",
            "outcome",
            "schema",
            "pointer",
            "optional",
            "default",
            "literal",
        }
        parts = [
            str(part) if isinstance(part, int) or part in allowed else "*" for part in issue["loc"]
        ]
        _fail("invalid_contract", location, ".".join(parts) or None)


def _binding(value: Binding, location: SourceLocation) -> BindingPlan:
    try:
        if isinstance(value, LiteralBinding):
            return BindingPlan(kind="literal", literal=freeze_json(value.literal))
        return BindingPlan(
            kind="pointer",
            pointer=value.pointer,
            optional=value.optional,
            has_default="default" in value.model_fields_set,
            default=(freeze_json(value.default) if "default" in value.model_fields_set else None),
        )
    except ServiceError:
        _fail("invalid_contract", location)


def _question(
    authored: object,
    *,
    step_id: str,
    prompt: str,
    source_ids: tuple[str, ...],
) -> DecisionQuestionPlan:
    native: NativeQuestion
    if isinstance(authored, ChoiceQuestionShorthand):
        native = ChoiceQuestion(
            id=step_id,
            prompt=prompt,
            criteria=authored.criteria,
            allowedSourceIds=list(source_ids),
            type="choice",
            options=authored.catalog.decision_options(),
        )
    elif isinstance(authored, MultiselectQuestionShorthand):
        native = MultiselectQuestion(
            id=step_id,
            prompt=prompt,
            criteria=authored.criteria,
            allowedSourceIds=list(source_ids),
            type="multiselect",
            options=authored.catalog.decision_options(),
            minSelections=authored.minSelections,
            maxSelections=authored.maxSelections,
        )
    elif isinstance(authored, PredicateQuestionShorthand):
        native = PredicateQuestion(
            id=step_id,
            prompt=prompt,
            criteria=authored.criteria,
            allowedSourceIds=list(source_ids),
            type="predicate",
        )
    elif isinstance(authored, OrdinalQuestionShorthand):
        native = OrdinalQuestion(
            id=step_id,
            prompt=prompt,
            criteria=authored.criteria,
            allowedSourceIds=list(source_ids),
            type="ordinal",
            levels=authored.levels,
        )
    elif isinstance(authored, RequestUnitsQuestionShorthand):
        native = RequestUnitsQuestion(
            id=step_id,
            prompt=prompt,
            criteria=authored.criteria,
            allowedSourceIds=list(source_ids),
            type="request_units",
            catalog=authored.catalog.decision_options(),
            allowNoMatch=authored.allowNoMatch,
        )
    else:
        native = cast(NativeQuestion, authored)
    options = getattr(native, "options", getattr(native, "levels", getattr(native, "catalog", [])))
    return DecisionQuestionPlan(
        id=native.id,
        type=native.type,
        prompt=native.prompt,
        criteria=tuple(native.criteria),
        allowed_source_ids=tuple(native.allowedSourceIds),
        options=tuple(
            DecisionOptionPlan(id=item.id, description=item.description) for item in options
        ),
        min_selections=getattr(native, "minSelections", None),
        max_selections=getattr(native, "maxSelections", None),
        allow_no_match=getattr(native, "allowNoMatch", None),
    )


def _confined_path(bundle: Path, authored: str, location: SourceLocation) -> Path:
    if "://" in authored or Path(authored).is_absolute():
        _fail("invalid_schema_path", location)
    try:
        candidate = (bundle / authored).resolve(strict=True)
    except OSError:
        _fail("invalid_schema_path", location)
    if not candidate.is_file() or not candidate.is_relative_to(bundle):
        _fail("invalid_schema_path", location)
    return candidate


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(_: str) -> object:
    raise ValueError


def _load_schema(
    bundle: Path,
    authored: str | dict[str, object],
    location: SourceLocation,
    loaded: dict[str, dict[str, object]],
    sources: dict[str, bytes],
    validated_nodes: set[tuple[str, int]],
    *,
    inline_name: str = "input",
) -> tuple[str, FrozenObject]:
    if isinstance(authored, dict):
        # A reserved virtual resource gives inline schemas the same local-ref base
        # and frozen registry treatment as file schemas without writing a file.
        relative = f".foliqant-inline-{inline_name}.json"
        if relative in loaded or (bundle / relative).exists():
            _fail("invalid_schema_path", location)
        try:
            return _accept_schema(
                bundle,
                bundle / relative,
                relative,
                authored,
                location,
                loaded,
                sources,
                validated_nodes,
            )
        except CompilationError as error:
            if error.location.path == relative:
                _fail(
                    error.reason,
                    location,
                    "input_schema" if inline_name == "input" else "output.schema",
                )
            raise

    path = _confined_path(bundle, authored, location)
    relative = path.relative_to(bundle).as_posix()
    if relative in loaded:
        return relative, cast(FrozenObject, freeze_json(loaded[relative]))
    try:
        source = path.read_bytes()
        text = source.decode("utf-8")
        if path.suffix == ".json":
            raw = json.loads(
                text,
                object_pairs_hook=_json_pairs,
                parse_constant=_reject_json_constant,
            )
        else:
            raw = load_yaml(text, relative_path=relative)
        if not isinstance(raw, dict):
            raise ValueError
    except (OSError, UnicodeError, ValueError, RecursionError, SchemaError):
        _fail("invalid_json_schema", SourceLocation(relative, 1, 1))
    sources[relative] = source
    return _accept_schema(
        bundle,
        path,
        relative,
        raw,
        SourceLocation(relative, 1, 1),
        loaded,
        sources,
        validated_nodes,
    )


def _accept_schema(
    bundle: Path,
    path: Path,
    relative: str,
    raw: dict[str, object],
    location: SourceLocation,
    loaded: dict[str, dict[str, object]],
    sources: dict[str, bytes],
    validated_nodes: set[tuple[str, int]],
) -> tuple[str, FrozenObject]:
    try:
        Draft202012Validator.check_schema(raw)
        frozen = cast(FrozenObject, freeze_json(raw))
    except (ValueError, RecursionError, SchemaError, ServiceError):
        _fail("invalid_json_schema", location)
    loaded[relative] = raw
    try:
        _validate_schema_object(bundle, path, relative, raw, raw, loaded, sources, validated_nodes)
    except RecursionError:
        _fail("invalid_json_schema", location)
    return relative, frozen


def _resolve_schema_fragment(resource: dict[str, object], fragment: str, relative: str) -> object:
    try:
        return _resolve_confined_schema_fragment(resource, fragment)
    except ValueError:
        _fail("invalid_schema_reference", SourceLocation(relative, 1, 1))


def _validate_schema_object(
    bundle: Path,
    resource_path: Path,
    relative: str,
    resource: dict[str, object],
    start: dict[str, object],
    loaded: dict[str, dict[str, object]],
    sources: dict[str, bytes],
    validated_nodes: set[tuple[str, int]],
) -> None:
    try:
        nodes = _walk_schema_nodes(start)
    except ValueError:
        _fail("invalid_json_schema", SourceLocation(relative, 1, 1))
    for node in nodes:
        identity = (relative, id(node))
        if identity in validated_nodes:
            continue
        validated_nodes.add(identity)
        if "$id" in node:
            _fail("invalid_schema_path", SourceLocation(relative, 1, 1))
        for keyword in ("$ref", "$dynamicRef"):
            reference = node.get(keyword)
            if not isinstance(reference, str):
                continue
            if reference.startswith("#"):
                target_relative = relative
                target_path = resource_path
                target_resource = resource
                target = _resolve_schema_fragment(resource, reference[1:], relative)
            else:
                ref_path, _, fragment = reference.partition("#")
                if not ref_path or "://" in ref_path:
                    _fail("invalid_schema_path", SourceLocation(relative, 1, 1))
                target_path = (resource_path.parent / ref_path).resolve()
                try:
                    target_relative = target_path.relative_to(bundle).as_posix()
                except ValueError:
                    _fail("invalid_schema_path", SourceLocation(relative, 1, 1))
                _load_schema(
                    bundle,
                    target_relative,
                    SourceLocation(relative, 1, 1),
                    loaded,
                    sources,
                    validated_nodes,
                )
                target_resource = loaded[target_relative]
                target = (
                    _resolve_schema_fragment(target_resource, fragment, target_relative)
                    if fragment
                    else target_resource
                )
            if isinstance(target, bool):
                continue
            if not isinstance(target, dict):
                _fail("invalid_schema_reference", SourceLocation(target_relative, 1, 1))
            _validate_schema_object(
                bundle,
                target_path,
                target_relative,
                target_resource,
                cast(dict[str, object], target),
                loaded,
                sources,
                validated_nodes,
            )


def _model(
    alias: str | None, default: str | None, aliases: Mapping[str, str], location: SourceLocation
) -> str:
    selected = alias or default
    if selected is None or selected not in aliases:
        _fail("unknown_model", location)
    return selected


def _answer_keys(question: DecisionQuestionPlan) -> set[str] | None:
    if question.type in {"choice", "ordinal"}:
        return {option.id for option in question.options}
    if question.type == "predicate":
        return {"true", "false"}
    return None


def _unresolved(value: str | UnresolvedRouting | None) -> str | UnresolvedRoutingPlan | None:
    if not isinstance(value, UnresolvedRouting):
        return value
    return UnresolvedRoutingPlan(
        value.default,
        tuple(
            (cast(DecisionIssue, key), target)
            for key, target in value.model_dump(exclude_none=True).items()
            if key != "default"
        ),
    )


def _compile_step(
    authored: StepAuthoring,
    *,
    step_id: str,
    location: SourceLocation,
    default_model: str | None,
    model_aliases: Mapping[str, str],
    catalogs: Mapping[str, DeclaredToolCatalog],
    handler_names: Collection[str],
    bundle: Path,
    schemas: dict[str, dict[str, object]],
    schema_sources: dict[str, bytes],
    validated_schema_nodes: set[tuple[str, int]],
) -> CompiledStep:
    if isinstance(authored, DecisionStepAuthoring):
        source_ids = tuple(authored.sources)
        if authored.questions is not None:
            raw_questions: list[object] = list(authored.questions)
        else:
            assert authored.question is not None
            raw_questions = [authored.question]
        try:
            questions = tuple(
                _question(
                    item, step_id=step_id, prompt=authored.instructions, source_ids=source_ids
                )
                for item in raw_questions
            )
        except ValidationError:
            _fail("invalid_question", location)
        for question in questions:
            if not set(question.allowed_source_ids).issubset(authored.sources):
                _fail("unknown_question_source", location)
        routes = authored.on_answer or {}
        if routes:
            expected = _answer_keys(questions[0]) if len(questions) == 1 else None
            if expected is None or set(routes) != expected:
                _fail("incomplete_answer_routes", location)
        return DecisionStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            next=authored.next,
            on_unresolved=_unresolved(authored.on_unresolved),
            unresolved_before_transition=True,
            model=_model(cast(str | None, authored.model), default_model, model_aliases, location),
            sources=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.sources.items())
            ),
            questions=questions,
            question_mode="single" if authored.question is not None else "multiple",
            instructions=authored.instructions,
            on_answer=tuple(sorted(routes.items())),
            fallback=(
                FallbackPlan(
                    CategoryPlan(
                        authored.fallback.category.id, authored.fallback.category.description
                    ),
                    tuple(authored.fallback.on),
                )
                if authored.fallback is not None
                else None
            ),
        )
    if isinstance(authored, LlmStepAuthoring):
        policy = None
        if authored.tools is not None:
            catalog = catalogs.get(authored.tools.server)
            if catalog is None:
                _fail("unknown_tool_server", location)
            if any(name not in catalog.tools for name in authored.tools.allow):
                _fail("unknown_tool", location)
            choice = authored.tools.choice
            if isinstance(choice, str):
                policy = ToolPolicyPlan(
                    authored.tools.server,
                    tuple(authored.tools.allow),
                    choice_mode=choice,
                )
            else:
                if choice.name not in authored.tools.allow or choice.name not in catalog.tools:
                    _fail("unknown_tool", location)
                policy = ToolPolicyPlan(
                    authored.tools.server,
                    tuple(authored.tools.allow),
                    choice_mode="named",
                    choice_name=choice.name,
                )
        if isinstance(authored.output, str):
            output_kind: Literal["text", "schema"] = "text"
            output_schema_path = None
            output_schema = None
        else:
            output_kind = "schema"
            output_schema_path, output_schema = _load_schema(
                bundle,
                cast(str | dict[str, object], authored.output.schema_value),
                location,
                schemas,
                schema_sources,
                validated_schema_nodes,
                inline_name=f"step-{step_id}",
            )
        return LlmStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            next=authored.next,
            on_unresolved=_unresolved(authored.on_unresolved),
            model=_model(cast(str | None, authored.model), default_model, model_aliases, location),
            input=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.input.items())
            ),
            instructions=authored.instructions,
            output_kind=output_kind,
            output_schema_path=output_schema_path,
            output_schema=output_schema,
            tools=policy,
        )
    if isinstance(authored, McpStepAuthoring):
        catalog = catalogs.get(authored.server)
        if catalog is None:
            _fail("unknown_tool_server", location)
        if authored.tool not in catalog.tools:
            _fail("unknown_tool", location)
        return McpStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            next=authored.next,
            on_unresolved=_unresolved(authored.on_unresolved),
            server=authored.server,
            tool=authored.tool,
            arguments=tuple(
                (key, _binding(value, location))
                for key, value in sorted(authored.arguments.items())
            ),
        )
    if isinstance(authored, HandlerStepAuthoring):
        if authored.handler not in handler_names:
            _fail("unknown_handler", location)
        return HandlerStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            next=authored.next,
            on_unresolved=_unresolved(authored.on_unresolved),
            handler=authored.handler,
            input=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.input.items())
            ),
        )
    if isinstance(authored, FinishStepAuthoring):
        return FinishStepPlan(
            name=step_id, type="finish", location=location, outcome=authored.outcome
        )
    raise AssertionError("closed step union")


def _edges(step: CompiledStep) -> tuple[str, ...]:
    targets: list[str] = []
    if step.next is not None:
        targets.append(step.next)
    if isinstance(step.on_unresolved, UnresolvedRoutingPlan):
        targets.append(step.on_unresolved.default)
        targets.extend(target for _, target in step.on_unresolved.issues)
    elif step.on_unresolved is not None:
        targets.append(step.on_unresolved)
    if isinstance(step, DecisionStepPlan):
        targets.extend(target for _, target in step.on_answer)
    return tuple(dict.fromkeys(targets))


def _validate_graph(plan_steps: Mapping[str, CompiledStep], start: str) -> dict[str, set[str]]:
    location = next(iter(plan_steps.values())).location
    if start not in plan_steps:
        _fail("missing_start", location)
    graph = {name: _edges(step) for name, step in plan_steps.items()}
    for name, targets in graph.items():
        for target in targets:
            if target not in plan_steps:
                _fail("missing_step", plan_steps[name].location)
    visiting: set[str] = set()
    visited: set[str] = set()

    pending: list[tuple[str, bool]] = [(start, False)]
    while pending:
        name, exiting = pending.pop()
        if exiting:
            visiting.remove(name)
            visited.add(name)
            continue
        if name in visiting:
            _fail("workflow_cycle", plan_steps[name].location)
        if name in visited:
            continue
        visiting.add(name)
        pending.append((name, True))
        pending.extend((target, False) for target in reversed(graph[name]))

    if visited != set(plan_steps):
        missing = sorted(set(plan_steps) - visited)[0]
        _fail("unreachable_step", plan_steps[missing].location)
    predecessors = {name: set[str]() for name in graph}
    for name, targets in graph.items():
        for target in targets:
            predecessors[target].add(name)
    dominators = {name: ({name} if name == start else set(graph)) for name in graph}
    changed = True
    while changed:
        changed = False
        for name in graph:
            if name == start:
                continue
            parents = predecessors[name]
            common = (
                set.intersection(*(dominators[parent] for parent in parents)) if parents else set()
            )
            updated = {name} | common
            if updated != dominators[name]:
                dominators[name] = updated
                changed = True
    return dominators


def _bindings(step: CompiledStep) -> tuple[BindingPlan, ...]:
    if isinstance(step, DecisionStepPlan):
        return tuple(value for _, value in step.sources)
    if isinstance(step, LlmStepPlan):
        return tuple(value for _, value in step.input)
    if isinstance(step, McpStepPlan):
        return tuple(value for _, value in step.arguments)
    if isinstance(step, HandlerStepPlan):
        return tuple(value for _, value in step.input)
    return ()


def _step_reference(pointer: str) -> str | None:
    parts = pointer.split("/")
    if len(parts) < 3 or parts[1] != "steps":
        return None
    return parts[2].replace("~1", "/").replace("~0", "~")


def _validate_bindings(
    steps: Mapping[str, CompiledStep], dominators: Mapping[str, set[str]]
) -> None:
    for name, step in steps.items():
        for binding in _bindings(step):
            if binding.kind != "pointer" or binding.pointer is None:
                continue
            parts = binding.pointer.split("/")
            if binding.pointer and (
                len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}
            ):
                _fail("dangling_pointer", step.location)
            reference = _step_reference(binding.pointer)
            if reference is None:
                continue
            if reference not in steps:
                _fail("dangling_pointer", step.location)
            if (reference == name or reference not in dominators[name]) and not binding.optional:
                _fail("unavailable_step_reference", step.location)


def _validate_output(
    output: BindingPlan | None,
    steps: Mapping[str, CompiledStep],
    dominators: Mapping[str, set[str]],
    location: SourceLocation,
) -> None:
    if output is None or output.kind != "pointer" or output.pointer is None:
        return
    parts = output.pointer.split("/")
    if output.pointer and (len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}):
        _fail("incompatible_output_binding", location)
    reference = _step_reference(output.pointer)
    if reference is None:
        return
    if reference not in steps:
        _fail("dangling_pointer", location)
    terminal_steps = [
        name
        for name, step in steps.items()
        if isinstance(step, FinishStepPlan) or step.on_unresolved is None
    ]
    if not output.optional and any(
        reference != terminal and reference not in dominators[terminal]
        for terminal in terminal_steps
    ):
        _fail("incompatible_output_binding", location)


def _validate_model_capabilities(
    steps: Mapping[str, CompiledStep],
    profiles: Mapping[str, ModelConfig] | None,
    *,
    allow_missing: bool = False,
) -> None:
    if profiles is None:
        return
    for step in steps.values():
        if not isinstance(step, (DecisionStepPlan, LlmStepPlan)):
            continue
        profile = profiles.get(step.model)
        if profile is None:
            if allow_missing:
                continue
            _fail("unknown_model", step.location, "model")
        schema_output = isinstance(step, DecisionStepPlan) or step.output_kind == "schema"
        tools = isinstance(step, LlmStepPlan) and step.tools is not None
        if (
            (schema_output and not profile.supports_json_schema)
            or (not schema_output and not profile.supports_text)
            or (tools and not profile.supports_tools)
            or (schema_output and profile.output_mode == "tool" and not profile.supports_tools)
        ):
            _fail("unsupported_model_capability", step.location, "model")


def _validate_schema_bindings(
    steps: Mapping[str, CompiledStep],
    input_path: str | None,
    schemas: Mapping[str, dict[str, object]],
    catalogs: Mapping[str, DeclaredToolCatalog],
    handlers: Mapping[str, HandlerSchemas],
    output: BindingPlan | None,
    location: SourceLocation,
) -> None:
    def view(schema: dict[str, object], path: str = "") -> SchemaView:
        return SchemaView(schema, schema, path, schemas)

    def frozen_view(schema: FrozenObject) -> SchemaView:
        return view(cast(dict[str, object], thaw_json(schema)))

    def result_schema(step: CompiledStep) -> SchemaView:
        if isinstance(step, LlmStepPlan):
            if step.output_schema_path is not None:
                return view(schemas[step.output_schema_path], step.output_schema_path)
            return view({"type": "string"})
        if isinstance(step, McpStepPlan):
            return view(
                cast(dict[str, object], catalogs[step.server].tools[step.tool].output_schema or {})
            )
        if isinstance(step, HandlerStepPlan) and step.handler in handlers:
            return frozen_view(handlers[step.handler].output_schema)
        return view({})

    def source(binding: BindingPlan, at: SourceLocation, field: str) -> SchemaView:
        if binding.kind == "literal":
            return view({"type": json_type(binding.literal)})
        tokens = [
            (part.replace("~1", "/").replace("~0", "~"))
            for part in (binding.pointer or "").split("/")[1:]
        ]
        schema = view({})
        if not tokens:
            return schema
        if tokens[0] == "payload":
            if input_path is not None:
                schema = view(schemas[input_path], input_path)
            tokens = tokens[1:]
        elif tokens[0] == "metadata":
            schema = view({"type": "object"})
            tokens = tokens[1:]
        elif tokens[0] == "steps" and len(tokens) >= 2 and tokens[1] in steps:
            referenced = steps[tokens[1]]
            if len(tokens) >= 3 and tokens[2] == "result":
                schema = result_schema(referenced)
                tokens = tokens[3:]
            else:
                schema = view(
                    {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "result": {},
                            "selection": {
                                "type": "object",
                                "properties": {
                                    "origin": {"type": "string"},
                                    "category": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "additionalProperties": False,
                            },
                            "error": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "string"},
                                    "message": {"type": "string"},
                                    "retryable": {"type": "boolean"},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    }
                )
                tokens = tokens[2:]
        else:
            return schema
        resolved = schema.pointer(tokens)
        if resolved is None:
            if binding.optional:
                return view({"type": json_type(binding.default)})
            _fail("dangling_pointer", at, field)
        return resolved

    for step in steps.values():
        pairs: tuple[tuple[str, BindingPlan], ...] = ()
        expected: SchemaView | None = None
        field = "input"
        if isinstance(step, DecisionStepPlan):
            pairs, field = step.sources, "sources"
        elif isinstance(step, LlmStepPlan):
            pairs = step.input
        elif isinstance(step, McpStepPlan):
            pairs, field = step.arguments, "arguments"
            expected = view(
                cast(dict[str, object], catalogs[step.server].tools[step.tool].input_schema)
            )
        elif isinstance(step, HandlerStepPlan):
            pairs = step.input
            if step.handler in handlers:
                expected = frozen_view(handlers[step.handler].input_schema)
        if expected is not None:
            node = expected.resolved().node
            if isinstance(node, dict):
                required = node.get("required", [])
                if isinstance(required, list) and not set(required).issubset(dict(pairs)):
                    _fail("invalid_input_bindings", step.location, field)
                if incompatible_types({"object"}, schema_types(expected)):
                    _fail("incompatible_binding_type", step.location, field)
        for key, binding in pairs:
            actual = source(binding, step.location, field + ".*.pointer")
            if expected is None:
                continue
            target = expected.child(key)
            if target is None:
                _fail("invalid_input_bindings", step.location, field)
            if incompatible_types(schema_types(actual), schema_types(target)):
                _fail("incompatible_binding_type", step.location, field + ".*")
            if binding.optional and incompatible_types(
                {json_type(binding.default)}, schema_types(target)
            ):
                _fail("incompatible_binding_type", step.location, field + ".*.default")
    if output is not None:
        source(output, location, "output.pointer")


def _catalogs(
    values: Mapping[str, DeclaredToolCatalog | Mapping[str, object]], location: SourceLocation
) -> dict[str, DeclaredToolCatalog]:
    result: dict[str, DeclaredToolCatalog] = {}
    for name, value in values.items():
        if not _is_id(name):
            _fail("invalid_registry", location)
        catalog = _validate(_TOOL_CATALOG_ADAPTER, value, location)
        try:
            for tool in catalog.tools.values():
                validate_confined_tool_schema(tool.input_schema)
                if tool.output_schema is not None:
                    validate_confined_tool_schema(tool.output_schema)
        except (ValueError, RecursionError, SchemaError, CompilationError):
            _fail("invalid_tool_schema", location)
        result[name] = catalog
    return result


def _is_id(value: object) -> bool:
    if not isinstance(value, str) or not value or not value[0].islower():
        return False
    return all(
        part and part.isascii() and part.isalnum() and part.lower() == part
        for part in value.split("_")
    )


def _validate_registries(
    model_aliases: Mapping[str, str], handler_names: Collection[str], location: SourceLocation
) -> None:
    if any(
        not _is_id(alias) or not isinstance(model_id, str) or not model_id.strip()
        for alias, model_id in model_aliases.items()
    ):
        _fail("invalid_registry", location)
    if any(not _is_id(name) for name in handler_names):
        _fail("invalid_registry", location)


def _revision(
    sources: Mapping[str, bytes],
    model_aliases: Mapping[str, str],
    catalogs: Mapping[str, DeclaredToolCatalog],
    handler_names: Collection[str],
    model_profiles: Mapping[str, ModelConfig] | None,
    handler_schemas: Mapping[str, HandlerSchemas],
) -> str:
    files = {
        relative: hashlib.sha256(source).hexdigest() for relative, source in sorted(sources.items())
    }
    payload = {
        "files": files,
        "models": dict(sorted(model_aliases.items())),
        "tools": {
            name: catalog.model_dump(mode="json") for name, catalog in sorted(catalogs.items())
        },
        "handlers": sorted(handler_names),
        "handler_schemas": {
            name: {
                "input": thaw_json(schema.input_schema),
                "output": thaw_json(schema.output_schema),
            }
            for name, schema in sorted(handler_schemas.items())
        },
        "model_profiles": {
            name: profile.model_dump(mode="json")
            for name, profile in sorted((model_profiles or {}).items())
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compile_workflow(
    directory: Path,
    *,
    model_aliases: Mapping[str, str],
    tool_catalogs: Mapping[str, DeclaredToolCatalog | Mapping[str, object]],
    handler_names: Collection[str],
    model_profiles: Mapping[str, ModelConfig] | None = None,
    handler_schemas: Mapping[str, HandlerSchemas] | None = None,
    _model_registry: ModelRegistry | None = None,
) -> WorkflowPlan:
    """Compile a local bundle using only the supplied offline registries.

    No provider or MCP endpoint is contacted. Tool discovery remains a separate
    runtime/doctor responsibility that compares against the declared catalog.
    Optional profiles and handler schemas enable declared capability and conservative
    static type checks; runtime validators remain authoritative for actual values.
    """

    try:
        bundle = directory.resolve(strict=True)
    except OSError:
        _fail("missing_workflow", SourceLocation("workflow.yaml", 1, 1))
    workflow_location = SourceLocation("workflow.yaml", 1, 1)
    workflow_path = bundle / "workflow.yaml"
    if not workflow_path.is_file() or not workflow_path.resolve().is_relative_to(bundle):
        _fail("missing_workflow", workflow_location)
    try:
        workflow_source = workflow_path.read_bytes()
        workflow_text = workflow_source.decode("utf-8")
    except (OSError, UnicodeError):
        _fail("missing_workflow", workflow_location)
    raw_workflow = load_yaml(workflow_text, relative_path="workflow.yaml")
    workflow = _validate(_WORKFLOW_ADAPTER, raw_workflow, workflow_location)
    _validate_registries(model_aliases, handler_names, workflow_location)
    catalogs = _catalogs(tool_catalogs, workflow_location)
    schemas: dict[str, dict[str, object]] = {}
    validated_schema_nodes: set[tuple[str, int]] = set()
    sources = {"workflow.yaml": workflow_source}
    if workflow.input_schema is not None:
        input_schema_path, input_schema = _load_schema(
            bundle,
            cast(str | dict[str, object], workflow.input_schema),
            workflow_location,
            schemas,
            sources,
            validated_schema_nodes,
        )
    else:
        input_schema_path = None
        input_schema = None
    steps_dir = bundle / "steps"
    paths = (
        sorted(
            (
                path
                for path in steps_dir.iterdir()
                if path.is_file() and path.suffix in {".md", ".yaml"}
            ),
            key=lambda path: path.name,
        )
        if steps_dir.is_dir()
        else []
    )
    authored_steps: list[tuple[StepAuthoring, str, SourceLocation]] = []
    names: set[str] = set()
    if workflow.steps is not None:
        if paths:
            _fail("mixed_step_sources", workflow_location, "steps")
        for step_id, step in workflow.steps.items():
            if step.name is not None:
                _fail("ambiguous_step_name", workflow_location, "steps.*.name")
            authored_steps.append((step, step_id, workflow_location))
    for path in paths:
        if not path.resolve().is_relative_to(bundle):
            _fail("invalid_step_path", SourceLocation(path.name, 1, 1))
        raw, body, location, source = load_step(path, bundle=bundle)
        sources[location.path] = source
        if not isinstance(raw, dict):
            _fail("invalid_contract", location)
        raw_mapping = cast(dict[object, object], raw)
        step_type = raw_mapping.get("type")
        if step_type == "dispatch":
            _fail("unsupported_step_type", location)
        if body is not None and step_type not in {"decision", "llm"}:
            _fail("unexpected_step_body", location)
        if body is not None:
            if "instructions" in raw_mapping:
                _fail("ambiguous_instructions", location, "instructions")
            raw_mapping["instructions"] = body
        step = _validate(_STEP_ADAPTER, raw_mapping, location)
        step_id = step.name or path.stem
        if not _is_id(step_id):
            _fail("invalid_step_id", location)
        if step_id in names:
            _fail("duplicate_step", location)
        names.add(step_id)
        authored_steps.append((step, step_id, location))
    if not authored_steps:
        _fail("missing_step", workflow_location)
    registry = _model_registry if _model_registry is not None else ModelRegistry(model_profiles)
    selected_steps: list[tuple[StepAuthoring, str, SourceLocation]] = []
    effective_aliases = dict(model_aliases)
    effective_profiles = dict(model_profiles or {})
    for step, step_id, location in authored_steps:
        if isinstance(step, (DecisionStepAuthoring, LlmStepAuthoring)):
            alias = registry.select(
                step.model,
                default=workflow.defaults.model,
                aliases=model_aliases,
                workflow=workflow.name,
                step=step_id,
                location=location,
            )
            if alias in registry.profiles:
                effective_profiles[alias] = registry.profiles[alias]
                effective_aliases[alias] = registry.profiles[alias].model
            step = step.model_copy(update={"model": alias})
        selected_steps.append((step, step_id, location))
    compiled = {
        step_id: _compile_step(
            step,
            step_id=step_id,
            location=location,
            default_model=workflow.defaults.model,
            model_aliases=effective_aliases,
            catalogs=catalogs,
            handler_names=handler_names,
            bundle=bundle,
            schemas=schemas,
            schema_sources=sources,
            validated_schema_nodes=validated_schema_nodes,
        )
        for step, step_id, location in selected_steps
    }
    for compiled_step in compiled.values():
        if not isinstance(compiled_step, FinishStepPlan) and compiled_step.next is None:
            if not isinstance(compiled_step, DecisionStepPlan) or not compiled_step.on_answer:
                _fail("missing_transition", compiled_step.location, "next")
    dominators = _validate_graph(compiled, workflow.start)
    _validate_bindings(compiled, dominators)
    output = _binding(workflow.output, workflow_location) if workflow.output is not None else None
    _validate_output(output, compiled, dominators, workflow_location)
    _validate_model_capabilities(compiled, effective_profiles, allow_missing=model_profiles is None)
    _validate_schema_bindings(
        compiled,
        input_schema_path,
        schemas,
        catalogs,
        handler_schemas or {},
        output,
        workflow_location,
    )
    return WorkflowPlan(
        name=workflow.name,
        revision=_revision(
            sources, model_aliases, catalogs, handler_names, model_profiles, handler_schemas or {}
        ),
        start=workflow.start,
        default_model=workflow.defaults.model,
        input_schema_path=input_schema_path,
        input_schema=input_schema,
        schema_resources=tuple(
            SchemaResourcePlan(path=path, schema=cast(FrozenObject, freeze_json(schema)))
            for path, schema in sorted(schemas.items())
        ),
        output=output,
        steps=tuple(compiled[name] for name in sorted(compiled)),
        location=workflow_location,
    )

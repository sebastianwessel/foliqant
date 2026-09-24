"""Compile one local workflow bundle into an immutable execution plan."""

import hashlib
import json
import os
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Never, cast

from jsonschema import Draft202012Validator, SchemaError
from pydantic import TypeAdapter, ValidationError

from foliqant.contracts.models import ModelConfig
from foliqant.contracts.workflow import (
    Binding,
    CallableFlow,
    ChoiceQuestionShorthand,
    ConditionalRouting,
    DecisionStepAuthoring,
    DeclaredToolCatalog,
    FirstOfBinding,
    FlowCollectionStepAuthoring,
    FlowDefinition,
    FlowInstance,
    FlowTarget,
    HandlerStepAuthoring,
    LiteralBinding,
    LlmStepAuthoring,
    MatchRouting,
    McpStepAuthoring,
    MultiselectQuestionShorthand,
    NamedStep,
    ObjectOutput,
    OrdinalQuestionShorthand,
    PointerBinding,
    PredicateQuestionShorthand,
    RequestUnitsQuestionShorthand,
    ReviewRouting,
    RouteEntry,
    StartRouteEntry,
    StartRouting,
    StepAuthoring,
    TransitionTarget,
    UnresolvedRouting,
    WorkflowAuthoring,
)
from foliqant.core.errors import ServiceError
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.plan import (
    MAX_COLLECTION_DEPTH,
    BindingPlan,
    CategoryPlan,
    CompiledStep,
    ConditionalRoutingPlan,
    ConditionPlan,
    DecisionOptionPlan,
    DecisionQuestionPlan,
    DecisionStepPlan,
    FallbackPlan,
    FlowCollectionStepPlan,
    FlowPlan,
    HandlerStepPlan,
    LlmStepPlan,
    MatchRoutingPlan,
    McpStepPlan,
    RepeatPlan,
    RouteEntryPlan,
    SchemaResourcePlan,
    SourceLocation,
    ToolPolicyPlan,
    TransitionTargetPlan,
    UnresolvedRoutingPlan,
    WorkflowPlan,
)
from foliqant.core.prompt import compile_prompt, prompt_inputs
from foliqant.decisions import (
    ChoiceQuestion,
    MultiselectQuestion,
    OrdinalQuestion,
    PredicateQuestion,
    RequestUnitsQuestion,
)

from ._loader import YamlLocator, load_step, load_yaml
from .collections import validate_collection_literals
from .conditions import check_condition, compile_condition, condition_pointers
from .diagnostics import Diagnostics, safe_text
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
from .scopes import FlowScope, HandlerSchemas, WorkflowScope, binding_static, tokens
from .static_schema import SchemaView, incompatible_types, json_type, schema_types
from .static_values import Const, Obj, Static, allows_empty_string, of, types, values

_WORKFLOW_ADAPTER = TypeAdapter(WorkflowAuthoring)
_FLOW_ADAPTER = TypeAdapter(FlowDefinition)
_STEP_ADAPTER: TypeAdapter[StepAuthoring] = TypeAdapter(StepAuthoring)
_TOOL_CATALOG_ADAPTER = TypeAdapter(DeclaredToolCatalog)
type NativeQuestion = (
    ChoiceQuestion
    | MultiselectQuestion
    | PredicateQuestion
    | OrdinalQuestion
    | RequestUnitsQuestion
)

# Compiler-owned field names that are safe to echo in error field paths.
_FIELD_NAMES = frozenset(
    {
        "name",
        "start",
        "defaults",
        "model",
        "input_schema",
        "output",
        "steps",
        "flows",
        "id",
        "callable",
        "items",
        "max_items",
        "definition",
        "transition",
        "binding",
        "cases",
        "default_covers",
        "flow",
        "format",
        "prompt",
        "type",
        "on_unresolved",
        "fallback",
        "category",
        "on",
        "sources",
        "question",
        "questions",
        "instructions",
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
        "first_of",
        "fields",
        "route",
        "when",
        "repeat",
        "max_attempts",
        "until",
        "retry",
        "retry_input",
        "continue_when",
        "all",
        "any",
        "not",
        "present",
        "empty",
        "equals",
        "not_equals",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "matches",
        "length",
    }
)


@dataclass(frozen=True, slots=True)
class _Where:
    """Resolve authored key paths inside one file, below an optional prefix."""

    locator: YamlLocator
    prefix: tuple[str | int, ...] = ()

    def at(self, *path: str | int) -> SourceLocation:
        return self.locator.locate(*self.prefix, *path)

    def below(self, *path: str | int) -> "_Where":
        return _Where(self.locator, (*self.prefix, *path))


def _fail(reason: str, location: SourceLocation, field: str | None = None) -> Never:
    raise CompilationError(reason, location, field=field) from None


def _condition_position(loc: tuple[str | int, ...]) -> bool:
    """True when the error lies inside a condition field, judged by position.

    A business key that happens to be named ``when`` or ``until`` (an input or
    output field) is not a condition: step ``when`` follows the step entry,
    route ``when`` a route index, ``until`` the ``repeat`` and ``continue_when``
    the ``retry`` object.
    """
    for index, part in enumerate(loc):
        previous = loc[index - 1] if index else None
        if part == "when" and (
            previous == "NamedStep"
            or (isinstance(previous, int) and index >= 2 and loc[index - 2] == "route")
        ):
            return True
        if (part, previous) in {("until", "repeat"), ("continue_when", "retry")}:
            return True
    return False


def _validate[T](adapter: TypeAdapter[T], value: object, location: SourceLocation) -> T:
    try:
        return adapter.validate_python(value, strict=True)
    except ValidationError as error:
        issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
        parts = [
            str(part) if isinstance(part, int) or part in _FIELD_NAMES else "*"
            for part in issue["loc"]
        ]
        reason = "invalid_contract"
        if _condition_position(issue["loc"]):
            reason = "invalid_condition"
        elif issue["type"] == "extra_forbidden":
            reason = "unknown_field"
        _fail(reason, location, ".".join(parts) or None)


def _binding(value: Binding | ObjectOutput, location: SourceLocation) -> BindingPlan:
    try:
        if isinstance(value, LiteralBinding):
            return BindingPlan(kind="literal", literal=freeze_json(value.literal))
        if isinstance(value, ObjectOutput):
            return BindingPlan(
                kind="fields",
                fields=tuple((key, _binding(item, location)) for key, item in value.fields.items()),
            )
        has_default = "default" in value.model_fields_set
        default = freeze_json(value.default) if has_default else None
        if isinstance(value, FirstOfBinding):
            return BindingPlan(
                kind="first_of",
                members=tuple(member.pointer for member in value.first_of),
                has_default=has_default,
                default=default,
            )
        assert isinstance(value, PointerBinding)
        return BindingPlan(
            kind="pointer", pointer=value.pointer, has_default=has_default, default=default
        )
    except ServiceError:
        _fail("invalid_contract", location)


def _bindings_map(
    values: Mapping[str, Binding], location: SourceLocation
) -> tuple[tuple[str, BindingPlan], ...]:
    return tuple((key, _binding(value, location)) for key, value in sorted(values.items()))


def _pointers(binding: BindingPlan) -> tuple[str, ...]:
    """Every pointer of a binding, including object fields and candidates."""
    if binding.kind == "fields":
        return tuple(pointer for _, item in binding.fields for pointer in _pointers(item))
    if binding.kind == "pointer" and binding.pointer is not None:
        return (binding.pointer,)
    return binding.members


def _require(
    binding: BindingPlan,
    *,
    check: Callable[[str], None],
    available: Callable[[str], bool],
    fail: Callable[[], Never],
) -> None:
    """Validate every pointer; a binding without default needs an available source.

    A ``first_of`` without default needs at least one available member.
    """
    if binding.kind == "literal":
        return
    if binding.kind == "fields":
        for _, item in binding.fields:
            _require(item, check=check, available=available, fail=fail)
        return
    pointers = _pointers(binding)
    for pointer in pointers:
        check(pointer)
    if binding.has_default:
        return
    if not any(available(pointer) for pointer in pointers):
        fail()


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
    except (OSError, RuntimeError, ValueError):
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
    origin: Path | None = None,
) -> tuple[str, FrozenObject]:
    if isinstance(authored, dict):
        # A reserved virtual resource gives inline schemas the same local-ref base
        # and frozen registry treatment as file schemas without writing a file.
        relative = (
            ((origin or bundle) / f".foliqant-inline-{inline_name}.json")
            .relative_to(bundle)
            .as_posix()
        )
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
            if error.location.path in {relative, location.path}:
                _fail(
                    error.reason,
                    location,
                    "input_schema"
                    if inline_name == "input" or inline_name.endswith("-input")
                    else "output.schema",
                )
            raise

    path = _confined_path(bundle, authored, location)
    relative = path.relative_to(bundle).as_posix()
    if relative in loaded:
        return relative, cast(FrozenObject, freeze_json(loaded[relative]))
    try:
        source = sources.get(relative)
        if source is None:
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


def _target(value: TransitionTarget) -> TransitionTargetPlan:
    if isinstance(value, FlowTarget):
        return TransitionTargetPlan(flow=value.flow)
    return TransitionTargetPlan(outcome=value.outcome)


def _route(value: ConditionalRouting | StartRouting, where: _Where) -> ConditionalRoutingPlan:
    """Compile ordered entries; only the last entry may (and must) omit ``when``."""
    entries: list[RouteEntryPlan] = []
    authored: list[RouteEntry | StartRouteEntry] = list(value.route)
    last = len(authored) - 1
    for index, entry in enumerate(authored):
        if entry.when is None and index != last:
            _fail("misplaced_otherwise", where.at("route", index), "route")
        if entry.when is not None and index == last:
            _fail("route_without_otherwise", where.at("route", index), "route")
        outcome = getattr(entry, "outcome", None)
        target = (
            TransitionTargetPlan(flow=entry.flow)
            if entry.flow is not None
            else TransitionTargetPlan(outcome=outcome)
        )
        when = (
            compile_condition(entry.when, where.at("route", index, "when"))
            if entry.when is not None
            else None
        )
        entries.append(RouteEntryPlan(target, when))
    return ConditionalRoutingPlan(tuple(entries))


def _unresolved(
    value: ReviewRouting | None, where: _Where
) -> TransitionTargetPlan | UnresolvedRoutingPlan | ConditionalRoutingPlan | None:
    if value is None:
        return None
    if isinstance(value, ConditionalRouting):
        return _route(value, where)
    if isinstance(value, UnresolvedRouting):
        return UnresolvedRoutingPlan(
            default=_target(value.default),
            issues=tuple(
                (issue, _target(target))
                for issue in (
                    "no_supported_answer",
                    "conflicting_information",
                    "multiple_valid_options",
                )
                if (target := getattr(value, issue)) is not None
            ),
        )
    return _target(value)


def _transition(
    value: TransitionTarget | MatchRouting | ConditionalRouting,
    where: _Where,
    location: SourceLocation,
) -> TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan:
    if isinstance(value, ConditionalRouting):
        return _route(value, where)
    if isinstance(value, MatchRouting):
        return MatchRoutingPlan(
            _binding(value.binding, location),
            tuple((key, _target(target)) for key, target in sorted(value.cases.items())),
            _target(value.default),
            tuple(value.default_covers or ()),
        )
    return _target(value)


def _route_targets(
    route: TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan | UnresolvedRoutingPlan,
) -> list[TransitionTargetPlan]:
    if isinstance(route, MatchRoutingPlan):
        return [route.default, *(target for _, target in route.cases)]
    if isinstance(route, ConditionalRoutingPlan):
        return [entry.target for entry in route.entries]
    if isinstance(route, UnresolvedRoutingPlan):
        return [route.default, *(target for _, target in route.issues)]
    return [route]


def _compile_step(
    authored: StepAuthoring,
    *,
    step_id: str,
    location: SourceLocation,
    default_model: str | None,
    origin: Path,
    schema_identity: str,
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
        return DecisionStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            model=_model(cast(str | None, authored.model), default_model, model_aliases, location),
            sources=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.sources.items())
            ),
            questions=questions,
            question_mode="single" if authored.question is not None else "multiple",
            instructions=authored.instructions,
            source_formats=tuple(
                (name, source.format) for name, source in sorted(authored.sources.items())
            ),
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
                _schema_reference(
                    cast(str | dict[str, object], authored.output.schema_value),
                    origin,
                    bundle,
                    location,
                ),
                location,
                schemas,
                schema_sources,
                validated_schema_nodes,
                inline_name=f"step-{schema_identity}",
                origin=origin,
            )
        try:
            prompt = (
                compile_prompt(authored.prompt, authored.input)
                if authored.prompt is not None
                else None
            )
        except ServiceError:
            _fail("invalid_prompt", location, "prompt")
        return LlmStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            model=_model(cast(str | None, authored.model), default_model, model_aliases, location),
            input=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.input.items())
            ),
            instructions=authored.instructions,
            output_kind=output_kind,
            output_schema_path=output_schema_path,
            output_schema=output_schema,
            tools=policy,
            prompt=prompt,
            max_iterations=authored.max_iterations,
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
            handler=authored.handler,
            input=tuple(
                (key, _binding(value, location)) for key, value in sorted(authored.input.items())
            ),
        )
    if isinstance(authored, FlowCollectionStepAuthoring):
        return FlowCollectionStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            items=_binding(authored.items, location),
            flows=tuple(authored.flows),
            max_items=authored.max_items,
        )
    raise AssertionError("closed step union")


def _bindings(step: CompiledStep) -> tuple[BindingPlan, ...]:
    if isinstance(step, DecisionStepPlan):
        return tuple(value for _, value in step.sources)
    if isinstance(step, LlmStepPlan):
        return tuple(value for _, value in step.input)
    if isinstance(step, McpStepPlan):
        return tuple(value for _, value in step.arguments)
    if isinstance(step, (HandlerStepPlan, FlowCollectionStepPlan)):
        return tuple(value for _, value in step.input)
    return ()


def _step_reference(pointer: str) -> str | None:
    parts = pointer.split("/")
    if len(parts) < 3 or parts[1] != "steps":
        return None
    return parts[2].replace("~1", "/").replace("~0", "~")


def _step_available(pointer: str, prior: Collection[str], conditional: Collection[str]) -> bool:
    """A required step pointer needs an earlier step; a conditional one only its status."""
    reference = _step_reference(pointer)
    if reference is None:
        return True
    if reference not in prior:
        return False
    if reference in conditional:
        parts = tokens(pointer)
        return len(parts) == 2 or (len(parts) == 3 and parts[2] == "status")
    return True


def _flow_pointer_check(
    steps: Mapping[str, CompiledStep], location: SourceLocation, field: str | None = None
) -> Callable[[str], None]:
    def check(pointer: str) -> None:
        parts = pointer.split("/")
        if pointer and (len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}):
            _fail("dangling_pointer", location, field)
        reference = _step_reference(pointer)
        if reference is not None and reference not in steps:
            _fail("dangling_pointer", location, field)

    return check


def _validate_bindings(
    steps: Mapping[str, CompiledStep],
    conditional: Collection[str],
    when_locations: Mapping[str, SourceLocation],
) -> None:
    prior: set[str] = set()
    for name, step in steps.items():
        check = _flow_pointer_check(steps, step.location)

        def unavailable(step: CompiledStep = step) -> Never:
            _fail("unavailable_step_reference", step.location)

        for binding in _bindings(step):
            _require(
                binding,
                check=check,
                available=lambda pointer: _step_available(pointer, prior, conditional),
                fail=unavailable,
            )
        if step.when is not None:
            location = when_locations[name]
            when_check = _flow_pointer_check(steps, location, "when")
            for pointer in condition_pointers(step.when):
                when_check(pointer)
                reference = _step_reference(pointer)
                # A condition tolerates absence, but a later step can never have run.
                if reference is not None and reference not in prior:
                    _fail("unavailable_step_reference", location, "when")
        prior.add(name)


def _validate_output(
    output: BindingPlan | None,
    steps: Mapping[str, CompiledStep],
    conditional: Collection[str],
    location: SourceLocation,
) -> None:
    if output is None:
        return
    first = next(iter(steps))

    def check(pointer: str) -> None:
        parts = pointer.split("/")
        if pointer and (len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}):
            _fail("incompatible_output_binding", location)
        reference = _step_reference(pointer)
        if reference is not None and reference not in steps:
            _fail("dangling_pointer", location)

    def available(pointer: str) -> bool:
        # Every operation may stop for review and a conditional step may be
        # skipped. A later or conditional result needs an explicit default.
        reference = _step_reference(pointer)
        return reference is None or (reference == first and reference not in conditional)

    def fail() -> Never:
        _fail("incompatible_output_binding", location)

    _require(output, check=check, available=available, fail=fail)


def _without_default(binding: BindingPlan) -> BindingPlan:
    return replace(binding, has_default=False, default=None)


def _binding_value(
    binding: BindingPlan,
    resolve: Callable[[str], Static],
    location: SourceLocation,
    field: str,
) -> Static:
    """Static value without the default; an impossible path must declare one."""
    if binding.kind == "fields":
        return of(
            Obj(
                {
                    name: _binding_value(item, resolve, location, field)
                    for name, item in binding.fields
                }
            )
        )
    base = binding_static(_without_default(binding), resolve)
    if base.impossible:
        if binding.has_default:
            return of(Const(thaw_json(binding.default)))
        _fail("dangling_pointer", location, field)
    return base


def _check_default_type(
    binding: BindingPlan, target: set[str] | None, location: SourceLocation, field: str
) -> None:
    if binding.kind == "fields":
        return
    if binding.has_default and incompatible_types({json_type(binding.default)}, target):
        _fail("incompatible_binding_type", location, field)


def _validate_schema_bindings(
    steps: Mapping[str, CompiledStep],
    scope: FlowScope,
    output: BindingPlan | None,
    location: SourceLocation,
    diagnostics: Diagnostics,
    when_locations: Mapping[str, SourceLocation],
) -> None:
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
            expected = scope.view(
                cast(dict[str, object], scope.catalogs[step.server].tools[step.tool].input_schema)
            )
        elif isinstance(step, FlowCollectionStepPlan):
            pairs = step.input
            expected = scope.view({"type": "object", "properties": {"items": {"type": "array"}}})
        elif isinstance(step, HandlerStepPlan):
            pairs = step.input
            contract = scope.handlers.get(step.handler)
            if contract is not None and contract.input_schema is not None:
                expected = scope.view(cast(dict[str, object], thaw_json(contract.input_schema)))
        if expected is not None:
            node = expected.resolved().node
            if isinstance(node, dict):
                required = node.get("required", [])
                if isinstance(required, list) and not set(required).issubset(dict(pairs)):
                    _fail("invalid_input_bindings", step.location, field)
                if incompatible_types({"object"}, schema_types(expected)):
                    _fail("incompatible_binding_type", step.location, field)
        for key, binding in pairs:
            actual = _binding_value(binding, scope, step.location, field + ".*.pointer")
            if (
                isinstance(step, DecisionStepPlan)
                and dict(step.source_formats).get(key, "text") == "text"
            ):
                if incompatible_types(types(actual), {"string"}):
                    _fail("incompatible_binding_type", step.location, field + ".*")
                if binding.has_default and not isinstance(binding.default, str):
                    _fail("incompatible_binding_type", step.location, field + ".*.default")
                if allows_empty_string(actual) or binding.default == "":
                    diagnostics.add(
                        "empty_text_source",
                        step.location,
                        f"Decision source `{key}` of step `{step.name}` may be an empty "
                        "string, which fails the run with invalid_input; require a nonempty "
                        "value, bind another field or use `format: json`.",
                        field_path=field + ".*",
                    )
            if expected is None:
                continue
            target = expected.child(key)
            if target is None:
                _fail("invalid_input_bindings", step.location, field)
            if incompatible_types(types(actual), schema_types(target)):
                _fail("incompatible_binding_type", step.location, field + ".*")
            _check_default_type(binding, schema_types(target), step.location, field + ".*.default")
        if isinstance(step, LlmStepPlan) and step.prompt is not None:
            unused = sorted(set(dict(step.input)) - prompt_inputs(step.prompt))
            if unused:
                diagnostics.add(
                    "unused_llm_input",
                    step.location,
                    f"Step `{step.name}` declares inputs its prompt never references and "
                    f"therefore never sends: {', '.join(unused)}.",
                    field_path="input",
                )
        if step.when is not None:
            check_condition(
                step.when,
                resolve=scope,
                location=when_locations[step.name],
                field="when",
                diagnostics=diagnostics,
            )
    if output is not None:
        _binding_value(output, scope, location, "output.pointer")


def _repeat(
    value: FlowInstance | CallableFlow, where: _Where, location: SourceLocation
) -> RepeatPlan | None:
    authored = value.repeat
    if authored is None:
        return None
    retry = authored.retry
    return RepeatPlan(
        max_attempts=authored.max_attempts,
        until=compile_condition(authored.until, where.at("repeat", "until")),
        retry_flow=retry.flow if retry is not None else None,
        retry_flow_input=_bindings_map(retry.input, location) if retry is not None else (),
        continue_when=(
            compile_condition(retry.continue_when, where.at("repeat", "retry", "continue_when"))
            if retry is not None and retry.continue_when is not None
            else None
        ),
        retry_input=_bindings_map(authored.retry_input, location),
    )


def _compile_flow(
    name: str,
    instance: FlowInstance | CallableFlow,
    *,
    workflow: WorkflowAuthoring,
    workflow_path: Path,
    workflow_where: _Where,
    root: Path,
    cache: dict[Path, bytes],
    registry: ModelRegistry,
    model_aliases: Mapping[str, str],
    model_profiles: Mapping[str, ModelConfig] | None,
    catalogs: Mapping[str, DeclaredToolCatalog],
    handler_names: Collection[str],
    handler_schemas: Mapping[str, HandlerSchemas],
    default_review: TransitionTargetPlan | UnresolvedRoutingPlan | ConditionalRoutingPlan | None,
    diagnostics: Diagnostics,
) -> tuple[FlowPlan, dict[str, dict[str, object]]]:
    assert workflow.name is not None
    path = workflow_path
    location = SourceLocation(path.relative_to(root).as_posix(), 1, 1)
    instance_where = workflow_where.below("flows", name)
    if instance.definition is None or isinstance(instance.definition, str):
        flow_reference = (
            instance.definition if instance.definition is not None else f"{name}/flow.yaml"
        )
        path = _definition_path(root, workflow_path.parent, flow_reference, location, flow=True)
        location = SourceLocation(path.relative_to(root).as_posix(), 1, 1)
        try:
            text = _read(path, root, cache).decode("utf-8")
        except UnicodeError:
            _fail("invalid_definition_file", location)
        definition = _validate(
            _FLOW_ADAPTER, load_yaml(text, relative_path=location.path), location
        )
        flow_where = _Where(YamlLocator(text, relative_path=location.path))
    else:
        definition = instance.definition
        flow_where = instance_where.below("definition")
    bundle = path.parent
    schemas: dict[str, dict[str, object]] = {}
    sources = {
        item.relative_to(bundle).as_posix(): source
        for item, source in cache.items()
        if item.is_relative_to(bundle)
    }
    validated_nodes: set[tuple[str, int]] = set()
    input_schema_path: str | None = None
    input_schema: FrozenObject | None = None
    if definition.input_schema is not None:
        input_schema_path, input_schema = _load_schema(
            bundle,
            cast(str | dict[str, object], definition.input_schema),
            location,
            schemas,
            sources,
            validated_nodes,
            inline_name=f"flow-{name}-input",
        )
    compiled: dict[str, CompiledStep] = {}
    when_locations: dict[str, SourceLocation] = {}
    effective_aliases = dict(model_aliases)
    effective_profiles = dict(model_profiles or {})
    default_model = definition.defaults.model or workflow.defaults.model
    for index, item in enumerate(definition.steps):
        entry = NamedStep(id=item) if isinstance(item, str) else item
        step_path = path
        step_location = location
        reference = entry.definition
        if reference is None:
            candidates = [
                f"{entry.id}.step.yaml",
                f"{entry.id}.step.md",
                f"{entry.id}/step.yaml",
                f"{entry.id}/step.md",
            ]
            found = [
                candidate
                for candidate in candidates
                if (bundle / candidate).exists() or (bundle / candidate).is_symlink()
            ]
            if len(found) != 1:
                _fail("ambiguous_step_file" if found else "missing_step_file", location)
            reference = found[0]
        if isinstance(reference, str):
            # An explicit definition may be shared from anywhere in the configuration
            # root; conventional discovery stays inside the flow bundle.
            step_path = _definition_path(
                root if entry.definition is not None else bundle,
                path.parent,
                reference,
                location,
                flow=False,
            )
            raw, body, local_location, _ = load_step(
                step_path, bundle=root, source=_read(step_path, root, cache)
            )
            step_location = SourceLocation(
                step_path.relative_to(root).as_posix(), local_location.line, local_location.column
            )
            if not isinstance(raw, dict):
                _fail("invalid_contract", step_location)
            raw = cast(dict[str, object], raw)
            if body is not None:
                if raw.get("type") not in {"llm", "decision"}:
                    _fail("unexpected_step_body", step_location)
                if "instructions" in raw:
                    _fail("ambiguous_instructions", step_location, "instructions")
                raw["instructions"] = body
            authored = _validate(_STEP_ADAPTER, raw, step_location)
        else:
            authored = reference
        if isinstance(authored, (DecisionStepAuthoring, LlmStepAuthoring)):
            alias = registry.select(
                authored.model,
                default=default_model,
                aliases=model_aliases,
                workflow=workflow.name,
                flow=name,
                step=entry.id,
                location=step_location,
            )
            if alias in registry.profiles:
                effective_profiles[alias] = registry.profiles[alias]
                effective_aliases[alias] = registry.profiles[alias].model
            authored = authored.model_copy(update={"model": alias})
        # A step outside the flow bundle keeps its own resources: its schemas are
        # relative to the step file and confined to the step's directory.
        step_bundle = bundle if step_path.is_relative_to(bundle) else step_path.parent
        shared = step_bundle != bundle
        step_schemas: dict[str, dict[str, object]] = {} if shared else schemas
        step_sources = (
            {
                item.relative_to(step_bundle).as_posix(): source
                for item, source in cache.items()
                if item.is_relative_to(step_bundle)
            }
            if shared
            else sources
        )
        step = _compile_step(
            authored,
            step_id=entry.id,
            location=step_location,
            default_model=default_model,
            origin=step_path.parent,
            schema_identity=f"{name}-{entry.id}",
            model_aliases=effective_aliases,
            catalogs=catalogs,
            handler_names=handler_names,
            bundle=step_bundle,
            schemas=step_schemas,
            schema_sources=step_sources,
            validated_schema_nodes=set() if shared else validated_nodes,
        )
        if shared:
            # Re-key the step's resources relative to the flow bundle for this plan.
            def rebase(relative: str, base: Path = step_bundle) -> str:
                return os.path.relpath(base / relative, bundle).replace(os.sep, "/")

            for relative, value in step_schemas.items():
                schemas[rebase(relative)] = value
            for relative, source in step_sources.items():
                cache[step_bundle / relative] = source
            if isinstance(step, LlmStepPlan) and step.output_schema_path is not None:
                step = replace(step, output_schema_path=rebase(step.output_schema_path))
        if entry.when is not None:
            when_locations[entry.id] = flow_where.at("steps", index, "when")
            step = replace(step, when=compile_condition(entry.when, when_locations[entry.id]))
        compiled[entry.id] = step
    conditional = frozenset(when_locations)
    _validate_bindings(compiled, conditional, when_locations)
    output = _binding(definition.output, location) if definition.output is not None else None
    _validate_output(output, compiled, conditional, location)
    _validate_model_capabilities(compiled, effective_profiles, allow_missing=model_profiles is None)
    local_scope = FlowScope(compiled, input_schema_path, schemas, catalogs, handler_schemas)
    _validate_schema_bindings(compiled, local_scope, output, location, diagnostics, when_locations)

    # Resource keys are global to this compiled workflow, while references retain
    # their authored relative bases. No schema bytes are reopened at execution.
    def resource_path(relative: str) -> str:
        return Path(os.path.normpath(bundle / relative)).relative_to(root).as_posix()

    for relative, source in sources.items():
        cache[Path(os.path.normpath(bundle / relative))] = source
    steps = tuple(
        replace(step, output_schema_path=resource_path(step.output_schema_path))
        if isinstance(step, LlmStepPlan) and step.output_schema_path is not None
        else step
        for step in compiled.values()
    )
    routed = isinstance(instance, FlowInstance)
    own_review = (
        _unresolved(instance.on_unresolved, instance_where.below("on_unresolved"))
        if isinstance(instance, FlowInstance)
        else None
    )
    inherited = routed and own_review is None and default_review is not None
    return FlowPlan(
        name=name,
        input=_bindings_map(instance.input, location) if isinstance(instance, FlowInstance) else (),
        steps=steps,
        input_schema_path=resource_path(input_schema_path)
        if input_schema_path is not None
        else None,
        input_schema=input_schema,
        output=output,
        transition=_transition(instance.transition, instance_where.below("transition"), location)
        if isinstance(instance, FlowInstance)
        else None,
        on_unresolved=default_review if inherited else own_review,
        callable=isinstance(instance, CallableFlow),
        location=location,
        repeat=_repeat(instance, instance_where, location),
        on_unresolved_inherited=inherited,
    ), {resource_path(key): value for key, value in schemas.items()}


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


def _schema_reference(
    value: str | dict[str, object], origin: Path, bundle: Path, location: SourceLocation
) -> str | dict[str, object]:
    if isinstance(value, dict):
        return value
    if "://" in value or Path(value).is_absolute():
        _fail("invalid_schema_path", location)
    try:
        candidate = (origin / value).resolve()
    except (OSError, RuntimeError, ValueError):
        _fail("invalid_schema_path", location)
    if not candidate.is_relative_to(bundle):
        _fail("invalid_schema_path", location)
    return candidate.relative_to(bundle).as_posix()


def _definition_path(
    root: Path, origin: Path, value: str, location: SourceLocation, *, flow: bool
) -> Path:
    reason = "invalid_flow_path" if flow else "invalid_step_path"
    if "://" in value or Path(value).is_absolute():
        _fail(reason, location)
    try:
        candidate = (origin / value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail(reason, location)
    allowed = {".yaml", ".yml"} if flow else {".yaml", ".yml", ".md"}
    if (
        not candidate.is_file()
        or not candidate.is_relative_to(root)
        or candidate.suffix not in allowed
    ):
        _fail(reason, location)
    return candidate


def _read(path: Path, root: Path, cache: dict[Path, bytes]) -> bytes:
    if path not in cache:
        try:
            cache[path] = path.read_bytes()
        except OSError:
            _fail(
                "invalid_definition_file", SourceLocation(path.relative_to(root).as_posix(), 1, 1)
            )
    return cache[path]


def _flow_targets(flow: FlowPlan) -> tuple[TransitionTargetPlan, ...]:
    targets: list[TransitionTargetPlan] = []
    if flow.transition is not None:
        targets.extend(_route_targets(flow.transition))
    if flow.on_unresolved is not None:
        targets.extend(_route_targets(flow.on_unresolved))
    return tuple(targets)


def _review_flow_targets(flow: FlowPlan) -> tuple[str, ...]:
    if flow.on_unresolved is None:
        return ()
    return tuple(
        target.flow for target in _route_targets(flow.on_unresolved) if target.flow is not None
    )


@dataclass(frozen=True, slots=True)
class _Graph:
    """Dominators, may-precede sets and retry ownership of the routed graph.

    ``retry_of`` holds the retry flows of routed flows only; their records are
    workflow-level. A callable flow's retry flow is item-local.
    """

    dominators: Mapping[str, frozenset[str]]
    ancestors: Mapping[str, frozenset[str]]
    retry_of: Mapping[str, str]

    def retry_for(self, name: str) -> Collection[str]:
        return {retry for retry, owner in self.retry_of.items() if owner == name}

    def may_have_run(self, name: str) -> frozenset[str]:
        """Flows that can have produced a record when ``name``'s routes are evaluated."""
        routed = self.ancestors[name] | {name}
        return frozenset(routed | {retry for flow in routed for retry in self.retry_for(flow)})


def _repeat_input_keys(flow: FlowPlan, schemas: Mapping[str, dict[str, object]]) -> set[str] | None:
    """Keys ``retry_input`` may override: bound inputs, or a callable's declared input.

    None means an open callable input without declared properties.
    """
    if not flow.callable:
        return set(dict(flow.input))
    if flow.input_schema_path is None:
        return None
    schema = dict(schemas[flow.input_schema_path])
    node = SchemaView(schema, schema, flow.input_schema_path, schemas).resolved().node
    properties = node.get("properties") if isinstance(node, dict) else None
    return set(properties) if isinstance(properties, dict) else None


def _validate_repeats(
    flows: Mapping[str, FlowPlan], where: _Where, schemas: Mapping[str, dict[str, object]]
) -> dict[str, str]:
    """Check repeat shapes and return each retry flow's single owning flow."""
    retry_of: dict[str, str] = {}
    for name, flow in flows.items():
        repeat = flow.repeat
        if repeat is None:
            continue
        here = where.below("flows", name, "repeat")
        keys = _repeat_input_keys(flow, schemas)
        if keys is not None and not set(dict(repeat.retry_input)) <= keys:
            _fail("invalid_repeat", here.at("retry_input"), "repeat.retry_input")
        if repeat.retry_flow is None:
            if any(
                (parts := tokens(pointer))[:1] == ["flows"]
                and len(parts) > 1
                and parts[1] in flows
                and parts[1] != name
                and flows[parts[1]].callable
                for _, binding in repeat.retry_input
                for pointer in _pointers(binding)
            ):
                _fail("invalid_repeat", here.at("retry_input"), "repeat.retry_input")
            continue
        target = flows.get(repeat.retry_flow)
        if (
            target is None
            or not target.callable
            or repeat.retry_flow == name
            or repeat.retry_flow in retry_of
            # A retry run is one execution; a retry flow cannot repeat itself.
            or target.repeat is not None
        ):
            _fail("invalid_repeat", here.at("retry", "flow"), "repeat.retry.flow")
        retry_of[repeat.retry_flow] = name
    return retry_of


def _reaches(graph: Mapping[str, tuple[str, ...]], start: str, goal: str) -> bool:
    pending, seen = [start], set[str]()
    while pending:
        current = pending.pop()
        if current == goal:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, ()))
    return False


def _validate_graph(
    flows: Mapping[str, FlowPlan],
    starts: tuple[str, ...],
    retry_of: Mapping[str, str],
    location: SourceLocation,
    where: _Where,
) -> _Graph:
    for start in starts:
        if start not in flows or flows[start].callable:
            _fail("missing_start", where.at("start"))
    routed = {
        name: tuple(
            dict.fromkeys(target.flow for target in _flow_targets(flow) if target.flow is not None)
        )
        for name, flow in flows.items()
    }
    graph: dict[str, tuple[tuple[str, int], ...]] = {}
    for name, flow in flows.items():
        for target in routed[name]:
            if target not in flows:
                _fail("missing_flow", flow.location)
            if flows[target].callable:
                _fail("invalid_routed_flow", flow.location)
        calls: list[tuple[str, int]] = []
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            for target in step.flows:
                if target not in flows or not flows[target].callable:
                    _fail("invalid_callable_flow", step.location, "flows")
                calls.append((target, 1))
        # A retry flow runs at the workflow level; it adds no collection nesting.
        calls.extend((retry, 0) for retry, owner in retry_of.items() if owner == name)
        graph[name] = tuple(dict.fromkeys((*((target, 0) for target in routed[name]), *calls)))
    for name, flow in flows.items():
        review_targets = _review_flow_targets(flow)
        if flow.on_unresolved_inherited:
            for target in review_targets:
                if target == name or _reaches(routed, target, name):
                    _fail(
                        "invalid_default_review_route",
                        where.at("defaults", "on_unresolved"),
                        "defaults.on_unresolved",
                    )
        elif name in review_targets:
            _fail(
                "review_route_to_self",
                where.at("flows", name, "on_unresolved"),
                "on_unresolved",
            )
    visited: set[str] = set()
    active: set[str] = set()
    call_depths: dict[str, int] = {}
    pending = [(start, False) for start in reversed(starts)]
    while pending:
        name, exiting = pending.pop()
        if exiting:
            call_depths[name] = max(
                (call_depths[target] + weight for target, weight in graph[name]), default=0
            )
            if call_depths[name] > MAX_COLLECTION_DEPTH:
                _fail("collection_depth_exceeded", flows[name].location)
            active.remove(name)
            visited.add(name)
            continue
        if name in active:
            _fail("workflow_cycle", flows[name].location)
        if name in visited:
            continue
        active.add(name)
        pending.append((name, True))
        pending.extend((target, False) for target, _ in reversed(graph[name]))
    if visited != set(flows):
        _fail("unreachable_flow", flows[sorted(set(flows) - visited)[0]].location)
    routed_flows = {name for name, flow in flows.items() if not flow.callable}
    entry = ""  # A virtual root precedes every start candidate; it is never a flow ID.
    predecessors = {name: set[str]() for name in routed_flows}
    for name, targets in routed.items():
        for target in targets:
            predecessors[target].add(name)
    for start in starts:
        predecessors[start].add(entry)
    dominators: dict[str, set[str]] = {name: set(routed_flows) | {entry} for name in routed_flows}
    dominators[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for name in routed_flows:
            parents = predecessors[name]
            common = (
                set.intersection(*(dominators[parent] for parent in parents)) if parents else set()
            )
            updated = common | {name}
            if updated != dominators[name]:
                dominators[name] = updated
                changed = True
    ancestors: dict[str, frozenset[str]] = {}
    for name in routed_flows:
        seen: set[str] = set()
        pending_names = [parent for parent in predecessors[name] if parent != entry]
        while pending_names:
            current = pending_names.pop()
            if current in seen:
                continue
            seen.add(current)
            pending_names.extend(parent for parent in predecessors[current] if parent != entry)
        ancestors[name] = frozenset(seen)
    return _Graph(
        {name: frozenset(dominators[name] - {entry}) for name in routed_flows},
        ancestors,
        # Retry flows of callable flows run per collection item, never at the boundary.
        {retry: owner for retry, owner in retry_of.items() if not flows[owner].callable},
    )


def _flow_reference_check(
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    location: SourceLocation,
    field: str | None,
) -> Callable[[str], None]:
    def check(pointer: str) -> None:
        parts = tokens(pointer)
        if not parts:
            return
        if parts[0] not in {"payload", "metadata", "flows"}:
            _fail("dangling_pointer", location, field)
        if parts[0] != "flows":
            return
        if len(parts) < 3 or parts[1] not in flows or parts[2] not in {"result", "attempts"}:
            _fail("invalid_flow_reference", location, field)
        target = flows[parts[1]]
        retry = parts[1] in graph.retry_of
        if target.callable and not retry:
            _fail("invalid_flow_reference", location, field)
        if parts[2] == "attempts" and target.repeat is None and not retry:
            _fail("invalid_flow_reference", location, field)

    return check


def _flow_available(available: Collection[str]) -> Callable[[str], bool]:
    def check(pointer: str) -> bool:
        parts = tokens(pointer)
        return not parts or parts[0] != "flows" or parts[1] in available

    return check


def _boundary_bindings(
    pairs: tuple[tuple[str, BindingPlan], ...],
    *,
    expected_path: str | None,
    schemas: Mapping[str, dict[str, object]],
    scope: WorkflowScope,
    check: Callable[[str], None],
    available: Callable[[str], bool],
    location: SourceLocation,
    field: str,
) -> None:
    """Check boundary bindings for availability, schema paths and target types."""
    expected = (
        SchemaView(
            dict(schemas[expected_path]), dict(schemas[expected_path]), expected_path, schemas
        )
        if expected_path is not None
        else None
    )
    if expected is not None:
        node = expected.resolved().node
        if incompatible_types({"object"}, schema_types(expected)):
            _fail("incompatible_binding_type", location, field)
        if isinstance(node, dict) and isinstance(node.get("required"), list):
            if not set(node["required"]).issubset(dict(pairs)):
                _fail("invalid_input_bindings", location, field)

    def unavailable() -> Never:
        _fail("unavailable_flow_reference", location, field)

    for key, binding in pairs:
        _require(binding, check=check, available=available, fail=unavailable)
        actual = _binding_value(binding, scope, location, field)
        if expected is None:
            continue
        target = expected.child(key)
        if target is None:
            _fail("invalid_input_bindings", location, field)
        if incompatible_types(types(actual), schema_types(target)):
            _fail("incompatible_binding_type", location, field)
        _check_default_type(binding, schema_types(target), location, field)


def _check_route_conditions(
    route: ConditionalRoutingPlan,
    *,
    allowed: Collection[str],
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    scope: WorkflowScope,
    where: _Where,
    field: str,
    diagnostics: Diagnostics,
    start: bool = False,
) -> None:
    """Scope and type checks for route entries; warn about unreachable entries."""
    decided = False
    for index, entry in enumerate(route.entries):
        location = where.at("route", index)
        if decided:
            diagnostics.add(
                "route_unreachable_entry",
                location,
                f"Entry {index} of `{field}` follows an entry whose condition always holds.",
                field_path=f"{field}.route",
            )
            continue
        if entry.when is None:
            continue
        condition_location = where.at("route", index, "when")
        _check_scope(
            entry.when,
            allowed=allowed,
            flows=flows,
            graph=graph,
            location=condition_location,
            field=f"{field}.route.when",
            start=start,
        )
        verdict = check_condition(
            entry.when,
            resolve=scope,
            location=condition_location,
            field=f"{field}.route.when",
            diagnostics=diagnostics,
        )
        if verdict is False:
            diagnostics.add(
                "route_unreachable_entry",
                location,
                f"Entry {index} of `{field}` can never be selected: its condition is "
                "statically false.",
                field_path=f"{field}.route",
            )
        elif verdict is True:
            decided = True


def _check_scope(
    condition: ConditionPlan,
    *,
    allowed: Collection[str],
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    location: SourceLocation,
    field: str,
    start: bool = False,
) -> None:
    """A condition tolerates absence, but not flows that can never have run."""
    check = _flow_reference_check(flows, graph, location, field)
    for pointer in condition_pointers(condition):
        parts = tokens(pointer)
        if start and parts[:1] == ["flows"]:
            _fail("unavailable_flow_reference", location, field)
        check(pointer)
        if parts[:1] == ["flows"] and parts[1] not in allowed:
            _fail("unavailable_flow_reference", location, field)


def _coverage(
    flow: FlowPlan,
    route: MatchRoutingPlan,
    scope: WorkflowScope,
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    """Compare case keys with the statically allowed values of the routed field."""
    location = where.at("flows", flow.name, "transition", "cases")
    allowed = values(scope.binding(route.binding))
    if allowed is None:
        if route.default_covers:
            _fail(
                "default_covers_mismatch",
                where.at("flows", flow.name, "transition", "default_covers"),
                "transition.default_covers",
            )
        diagnostics.add(
            "case_on_unknown_type",
            location,
            f"The allowed values of the field routed by flow `{flow.name}` are unknown "
            "(no enum or const in its schema); case keys cannot be checked.",
            field_path="transition.cases",
        )
        return
    strings = [value for value in allowed.values() if isinstance(value, str)]
    keys = [key for key, _ in route.cases]
    for key in keys:
        if key not in strings:
            _fail("unmatched_case", location, "transition.cases")
    uncovered = [value for value in strings if value not in keys]
    if route.default_covers:
        if sorted(route.default_covers) != sorted(uncovered):
            _fail(
                "default_covers_mismatch",
                where.at("flows", flow.name, "transition", "default_covers"),
                "transition.default_covers",
            )
    elif uncovered:
        diagnostics.add(
            "uncovered_value",
            location,
            f"Flow `{flow.name}` routes these allowed values to `default` without a case: "
            + ", ".join(safe_text(value) for value in uncovered)
            + ". List them in `default_covers` when that is intended.",
            field_path="transition.cases",
        )


def _validate_boundaries(
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    output: BindingPlan | None,
    start: str | ConditionalRoutingPlan,
    scope: WorkflowScope,
    schemas: Mapping[str, dict[str, object]],
    location: SourceLocation,
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    dominators = graph.dominators
    if isinstance(start, ConditionalRoutingPlan):
        _check_route_conditions(
            start,
            allowed=(),
            flows=flows,
            graph=graph,
            scope=scope,
            where=where.below("start"),
            field="start",
            diagnostics=diagnostics,
            start=True,
        )
    for name, flow in flows.items():
        if flow.callable:
            if flow.input_schema_path is not None:
                schema = dict(schemas[flow.input_schema_path])
                view = SchemaView(schema, schema, flow.input_schema_path, schemas)
                if incompatible_types({"object"}, schema_types(view)):
                    _fail("incompatible_binding_type", flow.location, "input_schema")
            if flow.repeat is not None:
                _validate_item_repeat(
                    flow,
                    flows=flows,
                    scope=scope,
                    schemas=schemas,
                    where=where.below("flows", name, "repeat"),
                    diagnostics=diagnostics,
                )
            continue
        flow_where = where.below("flows", name)
        _boundary_bindings(
            flow.input,
            expected_path=flow.input_schema_path,
            schemas=schemas,
            scope=scope,
            check=_flow_reference_check(flows, graph, flow.location, None),
            available=_flow_available(dominators[name] - {name}),
            location=flow.location,
            field="input",
        )
        allowed = graph.may_have_run(name)
        transition = flow.transition
        if isinstance(transition, MatchRoutingPlan):
            binding = transition.binding

            def unavailable(flow: FlowPlan = flow) -> Never:
                _fail("unavailable_flow_reference", flow.location)

            _require(
                binding,
                check=_flow_reference_check(flows, graph, flow.location, None),
                available=_flow_available(dominators[name]),
                fail=unavailable,
            )
            value = _binding_value(binding, scope, flow.location, "transition.binding")
            if incompatible_types(types(value), {"string", "null"}):
                _fail("incompatible_route_type", flow.location, "transition.binding")
            if (
                binding.has_default
                and binding.default is not None
                and not isinstance(binding.default, str)
            ):
                _fail("incompatible_route_type", flow.location, "transition.binding.default")
            _coverage(flow, transition, scope, where, diagnostics)
        elif isinstance(transition, ConditionalRoutingPlan):
            _check_route_conditions(
                transition,
                allowed=allowed,
                flows=flows,
                graph=graph,
                scope=scope,
                where=flow_where.below("transition"),
                field="transition",
                diagnostics=diagnostics,
            )
        if isinstance(flow.on_unresolved, ConditionalRoutingPlan):
            _check_route_conditions(
                flow.on_unresolved,
                allowed=allowed,
                flows=flows,
                graph=graph,
                scope=scope,
                where=(
                    where.below("defaults", "on_unresolved")
                    if flow.on_unresolved_inherited
                    else flow_where.below("on_unresolved")
                ),
                field="on_unresolved",
                diagnostics=diagnostics,
            )
        if flow.repeat is not None:
            _validate_repeat_bindings(
                flow,
                flows=flows,
                graph=graph,
                scope=scope,
                schemas=schemas,
                where=flow_where.below("repeat"),
                diagnostics=diagnostics,
            )
    if output is not None:
        terminals = [
            name
            for name, flow in flows.items()
            if not flow.callable
            and (
                flow.on_unresolved is None
                or any(target.outcome is not None for target in _flow_targets(flow))
            )
        ]
        available: set[str] = (
            set.intersection(*(set(dominators[name]) for name in terminals)) if terminals else set()
        )

        def unavailable_output() -> Never:
            _fail("unavailable_flow_reference", location)

        _require(
            output,
            check=_flow_reference_check(flows, graph, location, None),
            available=_flow_available(available),
            fail=unavailable_output,
        )
        _binding_value(output, scope, location, "output")


def _validate_repeat_bindings(
    flow: FlowPlan,
    *,
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    scope: WorkflowScope,
    schemas: Mapping[str, dict[str, object]],
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    repeat = flow.repeat
    assert repeat is not None
    name = flow.name
    dominators = graph.dominators[name]
    allowed = graph.may_have_run(name)
    _check_scope(
        repeat.until,
        allowed=allowed,
        flows=flows,
        graph=graph,
        location=where.at("until"),
        field="repeat.until",
    )
    check_condition(
        repeat.until,
        resolve=scope,
        location=where.at("until"),
        field="repeat.until",
        diagnostics=diagnostics,
    )
    retry = repeat.retry_flow
    if retry is not None:
        location = where.at("retry", "input")
        _boundary_bindings(
            repeat.retry_flow_input,
            expected_path=flows[retry].input_schema_path,
            schemas=schemas,
            scope=scope,
            check=_flow_reference_check(flows, graph, location, "repeat.retry.input"),
            available=_flow_available(dominators),
            location=location,
            field="repeat.retry.input",
        )
        if repeat.continue_when is not None:
            condition_location = where.at("retry", "continue_when")
            _check_scope(
                repeat.continue_when,
                allowed=allowed | {retry},
                flows=flows,
                graph=graph,
                location=condition_location,
                field="repeat.retry.continue_when",
            )
            check_condition(
                repeat.continue_when,
                resolve=scope,
                location=condition_location,
                field="repeat.retry.continue_when",
                diagnostics=diagnostics,
            )
    if repeat.retry_input:
        location = where.at("retry_input")
        expected = flow.input_schema_path
        pairs = repeat.retry_input
        _boundary_bindings(
            pairs,
            expected_path=None,
            schemas=schemas,
            scope=scope,
            check=_flow_reference_check(flows, graph, location, "repeat.retry_input"),
            available=_flow_available(dominators | ({retry} if retry is not None else set())),
            location=location,
            field="repeat.retry_input",
        )
        if expected is not None:
            view = SchemaView(dict(schemas[expected]), dict(schemas[expected]), expected, schemas)
            for key, binding in pairs:
                target = view.child(key)
                actual = _binding_value(binding, scope, location, "repeat.retry_input")
                if target is not None and incompatible_types(types(actual), schema_types(target)):
                    _fail("incompatible_binding_type", location, "repeat.retry_input")
                if target is not None:
                    _check_default_type(
                        binding, schema_types(target), location, "repeat.retry_input"
                    )


def _validate_item_repeat(
    flow: FlowPlan,
    *,
    flows: Mapping[str, FlowPlan],
    scope: WorkflowScope,
    schemas: Mapping[str, dict[str, object]],
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    """Check a callable flow's repeat in its item scope.

    Pointers see ``/payload`` (the item input), ``/metadata`` and the records of
    this flow and its retry flow only; no workflow-level flow result exists there.
    """
    repeat = flow.repeat
    assert repeat is not None
    name, retry = flow.name, repeat.retry_flow
    local = {key: flows[key] for key in (name, retry) if key is not None}
    item_scope = WorkflowScope(local, scope.flow_scopes, flow.input_schema_path, schemas)

    def reference_check(location: SourceLocation, field: str) -> Callable[[str], None]:
        def check(pointer: str) -> None:
            parts = tokens(pointer)
            if not parts:
                return
            if parts[0] not in {"payload", "metadata", "flows"}:
                _fail("dangling_pointer", location, field)
            if parts[0] == "flows" and (
                len(parts) < 3 or parts[1] not in local or parts[2] not in {"result", "attempts"}
            ):
                _fail("invalid_flow_reference", location, field)

        return check

    def condition(value: ConditionPlan, location: SourceLocation, field: str) -> None:
        check = reference_check(location, field)
        for pointer in condition_pointers(value):
            check(pointer)
        check_condition(
            value, resolve=item_scope, location=location, field=field, diagnostics=diagnostics
        )

    condition(repeat.until, where.at("until"), "repeat.until")
    if retry is not None:
        location = where.at("retry", "input")
        _boundary_bindings(
            repeat.retry_flow_input,
            expected_path=flows[retry].input_schema_path,
            schemas=schemas,
            scope=item_scope,
            check=reference_check(location, "repeat.retry.input"),
            available=_flow_available({name}),
            location=location,
            field="repeat.retry.input",
        )
        if repeat.continue_when is not None:
            condition(
                repeat.continue_when,
                where.at("retry", "continue_when"),
                "repeat.retry.continue_when",
            )
    if repeat.retry_input:
        location = where.at("retry_input")
        _boundary_bindings(
            repeat.retry_input,
            expected_path=None,
            schemas=schemas,
            scope=item_scope,
            check=reference_check(location, "repeat.retry_input"),
            available=_flow_available(set(local)),
            location=location,
            field="repeat.retry_input",
        )
        if flow.input_schema_path is not None:
            schema = dict(schemas[flow.input_schema_path])
            view = SchemaView(schema, schema, flow.input_schema_path, schemas)
            for key, binding in repeat.retry_input:
                target = view.child(key)
                actual = _binding_value(binding, item_scope, location, "repeat.retry_input")
                if target is not None and incompatible_types(types(actual), schema_types(target)):
                    _fail("incompatible_binding_type", location, "repeat.retry_input")


def _flow_steps(flow: FlowPlan) -> int:
    return len(flow.steps)


def _worst_steps(flow: FlowPlan, flows: Mapping[str, FlowPlan]) -> int:
    """Steps one invocation may visit, including every repeat attempt and retry run."""
    repeat = flow.repeat
    if repeat is None:
        return _flow_steps(flow)
    retry = flows[repeat.retry_flow] if repeat.retry_flow is not None else None
    return repeat.max_attempts * _flow_steps(flow) + (repeat.max_attempts - 1) * (
        _flow_steps(retry) if retry is not None else 0
    )


def _validate_budgets(
    flows: Mapping[str, FlowPlan],
    max_steps: int | None,
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    if max_steps is None:
        return
    for name, flow in flows.items():
        repeat = flow.repeat
        if repeat is not None:
            if _worst_steps(flow, flows) > max_steps:
                _fail("repeat_budget", where.at("flows", name, "repeat", "max_attempts"), "repeat")
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            child = max((_worst_steps(flows[target], flows) for target in step.flows), default=0)
            if step.max_items * child > max_steps:
                diagnostics.add(
                    "collection_budget",
                    step.location,
                    f"Collection step `{step.name}` may visit {step.max_items * child} steps "
                    f"({step.max_items} items x {child} steps), more than execution.max_steps "
                    f"({max_steps}).",
                    field_path="max_items",
                )


def _review_output(output: BindingPlan | None, ran: Collection[str]) -> str:
    """Describe what the host receives when a run ends in review after ``ran``."""
    if output is None:
        return "the accepted input as payload"
    read = {
        parts[1]
        for pointer in _pointers(output)
        if len(parts := tokens(pointer)) > 1 and parts[0] == "flows"
    }
    if output.kind in {"pointer", "first_of"} and output.has_default:
        default = safe_text(thaw_json(output.default))
        if read and not read & set(ran):
            return f"the workflow output default {default}"
        return f"the workflow output resolved from the flows that ran, or its default {default}"
    return "the workflow output resolved from the flows that ran"


def _flow_diagnostics(
    flows: Mapping[str, FlowPlan],
    output: BindingPlan | None,
    graph: _Graph,
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    for name, flow in flows.items():
        if flow.repeat is not None and flow.repeat.retry_flow is None:
            if any(isinstance(step, (DecisionStepPlan, LlmStepPlan)) for step in flow.steps):
                diagnostics.add(
                    "repeat_without_retry",
                    where.at("flows", name, "repeat"),
                    f"Flow `{name}` repeats a model step with identical input; add a `retry` "
                    "flow that changes the input.",
                    field_path="repeat",
                )
        if flow.callable:
            continue
        if flow.on_unresolved is None:
            received = _review_output(output, graph.may_have_run(name))
            diagnostics.add(
                "review_ends_run",
                where.at("flows", name),
                f"Flow `{name}` has no review route: when it stops for review the run ends "
                f"with needs_review and the host receives {received}.",
                field_path="on_unresolved",
            )


def compile_workflow(
    directory: Path,
    *,
    model_aliases: Mapping[str, str],
    tool_catalogs: Mapping[str, DeclaredToolCatalog | Mapping[str, object]],
    handler_names: Collection[str],
    model_profiles: Mapping[str, ModelConfig] | None = None,
    handler_schemas: Mapping[str, HandlerSchemas] | None = None,
    configuration_root: Path | None = None,
    max_steps: int | None = None,
    _model_registry: ModelRegistry | None = None,
) -> WorkflowPlan:
    """Compile conventional or explicit definitions without contacting dependencies.

    Referenced flows stay inside ``configuration_root`` (the workflow directory
    by default). Step/schema references stay inside their flow definition's
    directory, with paths resolved from the declaring file. All dependency bytes
    are frozen before clients open. Conventional lookup resolves definitions;
    the declared step list and transitions alone determine execution order.
    ``max_steps`` enables the static budget checks for ``repeat`` and collections.
    Non-fatal findings are returned in ``WorkflowPlan.diagnostics``.
    """
    try:
        bundle = directory.resolve(strict=True)
        root = (configuration_root or bundle).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("missing_workflow", SourceLocation("workflow.yaml", 1, 1))
    workflow_path = bundle / "workflow.yaml"
    location = SourceLocation("workflow.yaml", 1, 1)
    if not workflow_path.is_file() or not workflow_path.resolve().is_relative_to(root):
        _fail("missing_workflow", location)
    workflow_path = workflow_path.resolve()
    location = SourceLocation(workflow_path.relative_to(root).as_posix(), 1, 1)
    cache: dict[Path, bytes] = {}
    try:
        workflow_text = _read(workflow_path, root, cache).decode("utf-8")
    except UnicodeError:
        _fail("missing_workflow", location)
    raw_workflow = load_yaml(workflow_text, relative_path=location.path)
    if isinstance(raw_workflow, dict):
        raw_workflow.setdefault("name", bundle.name)
        declared_flows = raw_workflow.get("flows")
        if "start" not in raw_workflow and isinstance(declared_flows, dict):
            routed_names = [
                name
                for name, flow in declared_flows.items()
                if not isinstance(flow, dict) or flow.get("callable") is not True
            ]
            if len(routed_names) == 1:
                raw_workflow["start"] = routed_names[0]
    workflow = _validate(_WORKFLOW_ADAPTER, raw_workflow, location)
    if workflow.name is None or workflow.start is None:
        _fail("missing_start", location)
    where = _Where(YamlLocator(workflow_text, relative_path=location.path))
    diagnostics = Diagnostics()
    _validate_registries(model_aliases, handler_names, location)
    catalogs = _catalogs(tool_catalogs, location)
    registry = _model_registry if _model_registry is not None else ModelRegistry(model_profiles)
    schemas: dict[str, dict[str, object]] = {}
    input_schema_path: str | None = None
    input_schema: FrozenObject | None = None
    if workflow.input_schema is not None:
        local_schemas: dict[str, dict[str, object]] = {}
        sources: dict[str, bytes] = {}
        local_path, input_schema = _load_schema(
            bundle,
            cast(str | dict[str, object], workflow.input_schema),
            location,
            local_schemas,
            sources,
            set(),
        )
        input_schema_path = (bundle / local_path).relative_to(root).as_posix()
        schemas.update(
            {
                (bundle / key).relative_to(root).as_posix(): value
                for key, value in local_schemas.items()
            }
        )
        cache.update({bundle / key: value for key, value in sources.items()})
    default_review = _unresolved(
        workflow.defaults.on_unresolved, where.below("defaults", "on_unresolved")
    )
    flows: dict[str, FlowPlan] = {}
    for name, instance in workflow.flows.items():
        flow, resources = _compile_flow(
            name,
            instance,
            workflow=workflow,
            workflow_path=workflow_path,
            workflow_where=where,
            root=root,
            cache=cache,
            registry=registry,
            model_aliases=model_aliases,
            model_profiles=model_profiles,
            catalogs=catalogs,
            handler_names=handler_names,
            handler_schemas=handler_schemas or {},
            default_review=default_review,
            diagnostics=diagnostics,
        )
        flows[name] = flow
        schemas.update(resources)
    start: str | ConditionalRoutingPlan = (
        workflow.start
        if isinstance(workflow.start, str)
        else _route(workflow.start, where.below("start"))
    )
    starts = (
        (start,)
        if isinstance(start, str)
        else tuple(dict.fromkeys(entry.target.flow for entry in start.entries if entry.target.flow))
    )
    retry_of = _validate_repeats(flows, where, schemas)
    graph = _validate_graph(flows, starts, retry_of, location, where)
    validate_collection_literals(flows, schemas)
    output = _binding(workflow.output, location) if workflow.output is not None else None
    flow_scopes = {
        name: FlowScope(
            {step.name: step for step in flow.steps},
            flow.input_schema_path,
            schemas,
            catalogs,
            handler_schemas or {},
        )
        for name, flow in flows.items()
    }
    scope = WorkflowScope(flows, flow_scopes, input_schema_path, schemas)
    _validate_boundaries(flows, graph, output, start, scope, schemas, location, where, diagnostics)
    _validate_budgets(flows, max_steps, where, diagnostics)
    _flow_diagnostics(flows, output, graph, where, diagnostics)
    flows = {
        name: replace(
            flow,
            available_flows=tuple(
                other
                for other in flows
                if other in graph.dominators.get(name, ()) and other != name
            ),
        )
        for name, flow in flows.items()
    }
    return WorkflowPlan(
        name=workflow.name,
        revision=_revision(
            {path.relative_to(root).as_posix(): source for path, source in cache.items()},
            model_aliases,
            catalogs,
            handler_names,
            model_profiles,
            handler_schemas or {},
        ),
        start=start,
        default_model=workflow.defaults.model,
        input_schema_path=input_schema_path,
        input_schema=input_schema,
        schema_resources=tuple(
            SchemaResourcePlan(path=path, schema=cast(FrozenObject, freeze_json(schema)))
            for path, schema in sorted(schemas.items())
        ),
        output=output,
        flows=tuple(flows.values()),
        location=location,
        diagnostics=diagnostics.result(),
    )

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

from ._loader import YamlLocator, load_step, load_yaml, step_locator
from .collections import validate_collection_literals
from .conditions import check_condition, compile_condition, condition_pointers
from .diagnostics import Diagnostics, safe_text
from .errors import CompilationError, message_for
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


# Union tags that equal an authored key (`route:` selects the `route` form).
_UNION_TAGS = frozenset(
    {
        "flow",
        "outcome",
        "cases",
        "route",
        "issues",
        "pointer",
        "literal",
        "first_of",
        "fields",
        "all",
        "any",
        "not",
        "leaf",
        "handler",
        "llm",
        "mcp",
        "decision",
        "flow_collection",
    }
)

_VALIDATION_MESSAGES = {
    "missing": "A required field is missing.",
    "extra_forbidden": "The field is not supported here.",
    "literal_error": "The value is not one of the allowed options.",
    "greater_than_equal": "The value is below the allowed minimum.",
    "greater_than": "The value is below the allowed minimum.",
    "less_than_equal": "The value is above the allowed maximum.",
    "less_than": "The value is above the allowed maximum.",
    "too_short": "The list or mapping has fewer entries than required.",
    "too_long": "The list or mapping has more entries than allowed.",
    "string_too_short": "The text is shorter than required.",
    "string_too_long": "The text is longer than allowed.",
    "string_pattern_mismatch": "The text does not have the required form (e.g. a snake_case ID).",
    "union_tag_invalid": "The object does not have the distinguishing key of any allowed form.",
    "union_tag_not_found": "The object does not have the distinguishing key of any allowed form.",
    "value_error": "The value violates a rule of this field (uniqueness or a combination).",
}


def _safe_part(part: str | int) -> str:
    if isinstance(part, int) or part in _FIELD_NAMES or _is_id(part):
        return str(part)
    return "*"


def _field_path(parts: tuple[str | int, ...]) -> str | None:
    """Dotted key path; only compiler field names and validated IDs are echoed."""
    return ".".join(_safe_part(part) for part in parts) or None


def _name(value: object) -> str:
    """A configured identifier for a message; unsafe text is replaced."""
    return safe_text(value) if _is_id(value) else "?"


@dataclass(frozen=True, slots=True)
class _Where:
    """Resolve authored key paths inside one file, below an optional prefix."""

    locator: YamlLocator
    prefix: tuple[str | int, ...] = ()

    def at(self, *path: str | int) -> SourceLocation:
        return self.locator.locate(*self.prefix, *path)

    def field(self, *path: str | int) -> str | None:
        return _field_path((*self.prefix, *path))

    def below(self, *path: str | int) -> "_Where":
        return _Where(self.locator, (*self.prefix, *path))


@dataclass(frozen=True, slots=True)
class _FlowSource:
    """Where one flow's parts are authored, for precise workflow-level problems.

    ``instance`` is ``flows.<id>`` in workflow.yaml, ``definition`` the flow
    definition (file or inline), ``steps`` each step definition and
    ``entries`` each step list entry (which holds ``when``).
    """

    instance: _Where
    definition: _Where
    steps: Mapping[str, _Where]
    entries: Mapping[str, _Where]


def _fail(
    reason: str,
    location: SourceLocation,
    field: str | None = None,
    message: str | None = None,
) -> Never:
    raise CompilationError(reason, location, field=field, message=message) from None


def _fail_at(reason: str, where: _Where, *path: str | int, message: str | None = None) -> Never:
    """Fail at an authored key path; the field is that path inside the file."""
    _fail(reason, where.at(*path), where.field(*path), message)


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


def _validate[T](adapter: TypeAdapter[T], value: object, where: "_Where | SourceLocation") -> T:
    try:
        return adapter.validate_python(value, strict=True)
    except ValidationError as error:
        issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
        loc = tuple(issue["loc"])
        reason = "invalid_contract"
        if _condition_position(loc):
            reason = "invalid_condition"
        elif issue["type"] == "extra_forbidden":
            reason = "unknown_field"
        message = _VALIDATION_MESSAGES.get(issue["type"], message_for(reason))
        if isinstance(where, SourceLocation):
            parts = [
                str(part) if isinstance(part, int) or part in _FIELD_NAMES else "*" for part in loc
            ]
            _fail(reason, where, ".".join(parts) or None, message)
        kept, location, final = where.locator.follow((*where.prefix, *loc), _UNION_TAGS)
        if isinstance(final, str) and final in _FIELD_NAMES:
            kept = (*kept, final)
        _fail(
            reason, location, _validation_field(kept, rejected=reason == "unknown_field"), message
        )


# Below these keys authored content is data (schemas, literals, defaults), never echoed.
_DATA_FIELDS = frozenset({"literal", "default", "input_schema", "output_schema", "schema"})


def _validation_field(parts: tuple[str | int, ...], *, rejected: bool) -> str | None:
    """Field of a validation error: IDs are echoed, but never data or a rejected key."""
    safe: list[str] = []
    data = False
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if isinstance(part, int):
            safe.append(str(part))
        elif part in _FIELD_NAMES:
            safe.append(part)
        elif _is_id(part) and not data and not (rejected and last):
            safe.append(part)
        else:
            safe.append("*")
        data = data or part in _DATA_FIELDS
    return ".".join(safe) or None


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
    alias: str | None, default: str | None, aliases: Mapping[str, str], where: _Where
) -> str:
    selected = alias or default
    if selected is None or selected not in aliases:
        _fail_at("unknown_model", where, "model")
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
            _fail_at(
                "misplaced_otherwise",
                where,
                "route",
                index,
                message=f"Route entry {index} has no `when` but is not the last entry, so the "
                "entries after it can never be selected.",
            )
        if entry.when is not None and index == last:
            _fail_at(
                "route_without_otherwise",
                where,
                "route",
                index,
                message=f"The last route entry ({index}) has `when`: when no condition holds "
                "the run has no target.",
            )
        outcome = getattr(entry, "outcome", None)
        target = (
            TransitionTargetPlan(flow=entry.flow)
            if entry.flow is not None
            else TransitionTargetPlan(outcome=outcome)
        )
        when = (
            compile_condition(
                entry.when, where.at("route", index, "when"), where.field("route", index, "when")
            )
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
    where: _Where,
) -> CompiledStep:
    """Compile one validated step; ``where`` locates its authored keys."""
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
            _fail_at("invalid_question", where, "question" if authored.question else "questions")
        for question in questions:
            if not set(question.allowed_source_ids).issubset(authored.sources):
                _fail_at("unknown_question_source", where, "sources")
        return DecisionStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            model=_model(cast(str | None, authored.model), default_model, model_aliases, where),
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
                _fail_at(
                    "unknown_tool_server",
                    where,
                    "tools",
                    "server",
                    message=f"Step `{_name(step_id)}` uses MCP server "
                    f"`{_name(authored.tools.server)}`, which settings.yaml does not declare.",
                )
            if any(name not in catalog.tools for name in authored.tools.allow):
                _fail_at("unknown_tool", where, "tools", "allow")
            choice = authored.tools.choice
            if isinstance(choice, str):
                policy = ToolPolicyPlan(
                    authored.tools.server,
                    tuple(authored.tools.allow),
                    choice_mode=choice,
                )
            else:
                if choice.name not in authored.tools.allow or choice.name not in catalog.tools:
                    _fail_at("unknown_tool", where, "tools", "choice")
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
                    where.at("output", "schema"),
                ),
                where.at("output", "schema"),
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
            _fail_at("invalid_prompt", where, "prompt")
        return LlmStepPlan(
            name=step_id,
            type=authored.type,
            location=location,
            model=_model(cast(str | None, authored.model), default_model, model_aliases, where),
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
            _fail_at(
                "unknown_tool_server",
                where,
                "server",
                message=f"Step `{_name(step_id)}` uses MCP server `{_name(authored.server)}`, "
                "which settings.yaml does not declare.",
            )
        if authored.tool not in catalog.tools:
            _fail_at("unknown_tool", where, "tool")
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
            _fail_at(
                "unknown_handler",
                where,
                "handler",
                message=f"Step `{_name(step_id)}` calls handler `{_name(authored.handler)}`, "
                "which settings.yaml does not declare under `handlers`.",
            )
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


def _named_bindings(step: CompiledStep) -> tuple[tuple[tuple[str, ...], BindingPlan], ...]:
    """Each binding with its authored key path inside the step definition."""
    if isinstance(step, DecisionStepPlan):
        return tuple((("sources", key), value) for key, value in step.sources)
    if isinstance(step, LlmStepPlan):
        return tuple((("input", key), value) for key, value in step.input)
    if isinstance(step, McpStepPlan):
        return tuple((("arguments", key), value) for key, value in step.arguments)
    if isinstance(step, FlowCollectionStepPlan):
        return ((("items",), step.items),)
    if isinstance(step, HandlerStepPlan):
        return tuple((("input", key), value) for key, value in step.input)
    return ()


def _step_reference(pointer: str) -> str | None:
    parts = pointer.split("/")
    if len(parts) < 3 or parts[1] != "steps":
        return None
    return parts[2].replace("~1", "/").replace("~0", "~")


def _optional_record_field(pointer: str, steps: Mapping[str, CompiledStep]) -> bool:
    """A record field present only for some outcomes: a handler's ``selection``."""
    parts = tokens(pointer)
    step = steps.get(parts[1]) if len(parts) > 2 else None
    return step is not None and parts[2] == "selection" and isinstance(step, HandlerStepPlan)


def _step_available(
    pointer: str,
    prior: Collection[str],
    conditional: Collection[str],
    steps: Mapping[str, CompiledStep],
) -> bool:
    """A required step pointer needs an earlier step; a conditional one only its status."""
    reference = _step_reference(pointer)
    if reference is None:
        return True
    if reference not in prior:
        return False
    if reference in conditional:
        parts = tokens(pointer)
        return len(parts) == 2 or (len(parts) == 3 and parts[2] == "status")
    return not _optional_record_field(pointer, steps)


def _flow_pointer_check(
    steps: Mapping[str, CompiledStep], location: SourceLocation, field: str | None = None
) -> Callable[[str], None]:
    def check(pointer: str) -> None:
        parts = pointer.split("/")
        if pointer and (len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}):
            _fail(
                "dangling_pointer",
                location,
                field,
                "Inside a flow, pointers read /payload, /metadata or /steps/<id> only.",
            )
        reference = _step_reference(pointer)
        if reference is not None and reference not in steps:
            _fail(
                "dangling_pointer",
                location,
                field,
                f"The pointer reads step `{_name(reference)}`, which this flow does not declare.",
            )

    return check


def _validate_bindings(
    steps: Mapping[str, CompiledStep],
    conditional: Collection[str],
    step_wheres: Mapping[str, _Where],
    entry_wheres: Mapping[str, _Where],
) -> None:
    prior: set[str] = set()
    for name, step in steps.items():
        where = step_wheres[name]
        for path, binding in _named_bindings(step):
            location, field = where.at(*path), where.field(*path)
            check = _flow_pointer_check(steps, location, field)

            def unavailable(
                step: CompiledStep = step,
                binding: BindingPlan = binding,
                location: SourceLocation = location,
                field: str | None = field,
            ) -> Never:
                referenced = sorted(
                    {
                        reference
                        for pointer in _pointers(binding)
                        if (reference := _step_reference(pointer)) is not None
                    }
                )
                details = ", ".join(
                    f"`{_name(item)}` "
                    + (
                        "runs later"
                        if item not in prior
                        else "may be skipped (it has `when`)"
                        if item in conditional
                        else "may not report this field"
                    )
                    for item in referenced
                )
                _fail(
                    "unavailable_step_reference",
                    location,
                    field,
                    f"Step `{_name(step.name)}` requires a value from step {details}; "
                    "the run would fail with missing_binding.",
                )

            _require(
                binding,
                check=check,
                available=lambda pointer: _step_available(pointer, prior, conditional, steps),
                fail=unavailable,
            )
        if step.when is not None:
            entry = entry_wheres[name]
            location, field = entry.at("when"), entry.field("when")
            when_check = _flow_pointer_check(steps, location, field)
            for pointer in condition_pointers(step.when):
                when_check(pointer)
                reference = _step_reference(pointer)
                # A condition tolerates absence, but a later step can never have run.
                if reference is not None and reference not in prior:
                    _fail(
                        "unavailable_step_reference",
                        location,
                        field,
                        f"The `when` of step `{_name(name)}` reads step `{_name(reference)}`, "
                        "which never runs before it.",
                    )
        prior.add(name)


def _validate_output(
    output: BindingPlan | None,
    steps: Mapping[str, CompiledStep],
    conditional: Collection[str],
    where: _Where,
) -> None:
    if output is None:
        return
    first = next(iter(steps))
    location, field = where.at("output"), where.field("output")

    def check(pointer: str) -> None:
        parts = pointer.split("/")
        if pointer and (len(parts) < 2 or parts[1] not in {"payload", "metadata", "steps"}):
            _fail(
                "incompatible_output_binding",
                location,
                field,
                "A flow output reads /payload, /metadata or /steps/<id> only.",
            )
        reference = _step_reference(pointer)
        if reference is not None and reference not in steps:
            _fail(
                "dangling_pointer",
                location,
                field,
                f"The flow output reads step `{_name(reference)}`, which this flow does not "
                "declare.",
            )

    def available(pointer: str) -> bool:
        # Every operation may stop for review and a conditional step may be
        # skipped. A later or conditional result needs an explicit default, and
        # a first step stopping for review reports no selection.
        reference = _step_reference(pointer)
        if reference is None:
            return True
        parts = tokens(pointer)
        return (
            reference == first
            and reference not in conditional
            and not (len(parts) > 2 and parts[2] == "selection")
        )

    def fail() -> Never:
        _fail(
            "incompatible_output_binding",
            location,
            field,
            "The flow output is projected also when a step stops for review; it reads a "
            "later, conditional or review-dependent step value without a `default`.",
        )

    _require(output, check=check, available=available, fail=fail)


def _without_default(binding: BindingPlan) -> BindingPlan:
    return replace(binding, has_default=False, default=None)


def _binding_value(
    binding: BindingPlan,
    resolve: Callable[[str], Static],
    location: SourceLocation,
    field: str | None,
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
        pointers = ", ".join(f"`{safe_text(pointer, 160)}`" for pointer in _pointers(binding))
        _fail(
            "dangling_pointer",
            location,
            field,
            f"{pointers} can never resolve: the path does not exist in the known schema.",
        )
    return base


def _may_be_missing(binding: BindingPlan, resolve: Callable[[str], Static]) -> bool:
    """A required binding whose value can be structurally missing at run time.

    Structural absence is independent of business data: a later repeat attempt
    that may not run, or a flow result default that lacks the selected key.
    """
    if binding.kind == "literal" or binding.has_default:
        return False
    if binding.kind == "fields":
        return any(_may_be_missing(item, resolve) for _, item in binding.fields)
    statics = [resolve(pointer) for pointer in _pointers(binding)]
    return all(item.missing for item in statics)


def _check_default_type(
    binding: BindingPlan, target: set[str] | None, location: SourceLocation, field: str | None
) -> None:
    if binding.kind == "fields":
        return
    if binding.has_default and incompatible_types({json_type(binding.default)}, target):
        _fail(
            "incompatible_binding_type",
            location,
            field,
            f"The default is a JSON {json_type(binding.default)}, which the destination field "
            "does not accept.",
        )


def _type_message(actual: Static, target: set[str] | None) -> str:
    found = ", ".join(sorted(types(actual) or {"unknown"}))
    expected = ", ".join(sorted(target or {"unknown"}))
    return f"The bound value is a JSON {found}; the destination accepts {expected}."


def _validate_schema_bindings(
    steps: Mapping[str, CompiledStep],
    scope: FlowScope,
    output: BindingPlan | None,
    flow_where: _Where,
    diagnostics: Diagnostics,
    step_wheres: Mapping[str, _Where],
    entry_wheres: Mapping[str, _Where],
) -> None:
    for step in steps.values():
        where = step_wheres[step.name]
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
        collection = isinstance(step, FlowCollectionStepPlan)

        def path(
            key: str, *rest: str, collection: bool = collection, field: str = field
        ) -> tuple[str, ...]:
            return ("items", *rest) if collection else (field, key, *rest)

        if expected is not None:
            node = expected.resolved().node
            if isinstance(node, dict):
                required = node.get("required", [])
                if isinstance(required, list) and not set(required).issubset(dict(pairs)):
                    missing = sorted(set(cast(list[str], required)) - set(dict(pairs)))
                    _fail(
                        "invalid_input_bindings",
                        where.at(field),
                        where.field(field),
                        f"Step `{_name(step.name)}` binds no value for the required input "
                        + ", ".join(f"`{_name(item)}`" for item in missing)
                        + ".",
                    )
                if incompatible_types({"object"}, schema_types(expected)):
                    _fail("incompatible_binding_type", where.at(field), where.field(field))
        for key, binding in pairs:
            location, key_field = where.at(*path(key)), where.field(*path(key))
            actual = _binding_value(binding, scope, location, key_field)
            if (
                isinstance(step, DecisionStepPlan)
                and dict(step.source_formats).get(key, "text") == "text"
            ):
                if incompatible_types(types(actual), {"string"}):
                    _fail(
                        "incompatible_binding_type",
                        location,
                        key_field,
                        _type_message(actual, {"string"}) + " Use `format: json` for data.",
                    )
                if binding.has_default and not isinstance(binding.default, str):
                    _fail(
                        "incompatible_binding_type",
                        where.at(*path(key, "default")),
                        where.field(*path(key, "default")),
                        "A text source default must be a string.",
                    )
                if allows_empty_string(actual) or binding.default == "":
                    diagnostics.add(
                        "empty_text_source",
                        location,
                        f"Decision source `{key}` of step `{step.name}` may be an empty "
                        "string, which fails the run with invalid_input; require a nonempty "
                        "value, bind another field or use `format: json`.",
                        field_path=key_field,
                    )
            if expected is None:
                continue
            target = expected.child(key)
            if target is None:
                _fail(
                    "invalid_input_bindings",
                    location,
                    key_field,
                    f"Step `{_name(step.name)}` binds `{_name(key)}`, which its input schema "
                    "does not accept.",
                )
            if incompatible_types(types(actual), schema_types(target)):
                _fail(
                    "incompatible_binding_type",
                    location,
                    key_field,
                    _type_message(actual, schema_types(target)),
                )
            _check_default_type(
                binding,
                schema_types(target),
                where.at(*path(key, "default")),
                where.field(*path(key, "default")),
            )
        if isinstance(step, LlmStepPlan) and step.prompt is not None:
            unused = sorted(set(dict(step.input)) - prompt_inputs(step.prompt))
            if unused:
                diagnostics.add(
                    "unused_llm_input",
                    where.at("input", unused[0]),
                    f"Step `{step.name}` declares inputs its prompt never references and "
                    f"therefore never sends: {', '.join(unused)}.",
                    field_path=where.field("input"),
                )
        if step.when is not None:
            entry = entry_wheres[step.name]
            check_condition(
                step.when,
                resolve=scope,
                location=entry.at("when"),
                field=entry.field("when") or "when",
                diagnostics=diagnostics,
            )
    if output is not None:
        _binding_value(output, scope, flow_where.at("output"), flow_where.field("output"))


def _repeat(value: FlowInstance | CallableFlow, where: _Where) -> RepeatPlan | None:
    authored = value.repeat
    if authored is None:
        return None
    retry = authored.retry
    location = where.at("repeat")
    return RepeatPlan(
        max_attempts=authored.max_attempts,
        until=compile_condition(
            authored.until, where.at("repeat", "until"), where.field("repeat", "until")
        ),
        retry_flow=retry.flow if retry is not None else None,
        retry_flow_input=_bindings_map(retry.input, location) if retry is not None else (),
        continue_when=(
            compile_condition(
                retry.continue_when,
                where.at("repeat", "retry", "continue_when"),
                where.field("repeat", "retry", "continue_when"),
            )
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
) -> tuple[FlowPlan, dict[str, dict[str, object]], "_FlowSource"]:
    assert workflow.name is not None
    path = workflow_path
    location = SourceLocation(path.relative_to(root).as_posix(), 1, 1)
    instance_where = workflow_where.below("flows", name)
    if instance.definition is None or isinstance(instance.definition, str):
        flow_reference = (
            instance.definition if instance.definition is not None else f"{name}/flow.yaml"
        )
        path = _definition_path(
            root,
            workflow_path.parent,
            flow_reference,
            instance_where.at("definition") if instance.definition else instance_where.at(),
            flow=True,
            field=instance_where.field("definition"),
        )
        location = SourceLocation(path.relative_to(root).as_posix(), 1, 1)
        try:
            text = _read(path, root, cache).decode("utf-8")
        except UnicodeError:
            _fail("invalid_definition_file", location)
        flow_where = _Where(YamlLocator(text, relative_path=location.path))
        definition = _validate(
            _FLOW_ADAPTER, load_yaml(text, relative_path=location.path), flow_where
        )
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
    conditional_steps: set[str] = set()
    step_wheres: dict[str, _Where] = {}
    entry_wheres: dict[str, _Where] = {}
    effective_aliases = dict(model_aliases)
    effective_profiles = dict(model_profiles or {})
    default_model = definition.defaults.model or workflow.defaults.model
    for index, item in enumerate(definition.steps):
        entry = NamedStep(id=item) if isinstance(item, str) else item
        step_path = path
        step_location = location
        entry_where = flow_where.below("steps", index)
        step_where = entry_where.below("definition")
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
                _fail_at(
                    "ambiguous_step_file" if found else "missing_step_file",
                    flow_where,
                    "steps",
                    index,
                    message=(
                        f"Step `{_name(entry.id)}` has several conventional definition files."
                        if found
                        else f"Step `{_name(entry.id)}` of flow `{_name(name)}` has no "
                        "definition file."
                    ),
                )
            reference = found[0]
        if isinstance(reference, str):
            # An explicit definition may be shared from anywhere in the configuration
            # root; conventional discovery stays inside the flow bundle.
            step_path = _definition_path(
                root if entry.definition is not None else bundle,
                path.parent,
                reference,
                entry_where.at("definition") if entry.definition is not None else entry_where.at(),
                flow=False,
                field=entry_where.field("definition"),
            )
            step_source = _read(step_path, root, cache)
            raw, body, local_location, _ = load_step(step_path, bundle=root, source=step_source)
            step_location = SourceLocation(
                step_path.relative_to(root).as_posix(), local_location.line, local_location.column
            )
            step_where = _Where(step_locator(step_path, bundle=root, source=step_source))
            if not isinstance(raw, dict):
                _fail("invalid_contract", step_location)
            raw = cast(dict[str, object], raw)
            if body is not None:
                if raw.get("type") not in {"llm", "decision"}:
                    _fail_at("unexpected_step_body", step_where, "type")
                if "instructions" in raw:
                    _fail_at("ambiguous_instructions", step_where, "instructions")
                raw["instructions"] = body
            authored = _validate(_STEP_ADAPTER, raw, step_where)
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
                location=step_where.at("model"),
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
            where=step_where,
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
        step_wheres[entry.id] = step_where
        entry_wheres[entry.id] = entry_where
        if entry.when is not None:
            conditional_steps.add(entry.id)
            step = replace(
                step,
                when=compile_condition(
                    entry.when, entry_where.at("when"), entry_where.field("when")
                ),
            )
        compiled[entry.id] = step
    conditional = frozenset(conditional_steps)
    _validate_bindings(compiled, conditional, step_wheres, entry_wheres)
    output = _binding(definition.output, location) if definition.output is not None else None
    _validate_output(output, compiled, conditional, flow_where)
    _validate_model_capabilities(
        compiled, effective_profiles, step_wheres, allow_missing=model_profiles is None
    )
    local_scope = FlowScope(compiled, input_schema_path, schemas, catalogs, handler_schemas)
    _validate_schema_bindings(
        compiled, local_scope, output, flow_where, diagnostics, step_wheres, entry_wheres
    )

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
    return (
        FlowPlan(
            name=name,
            input=_bindings_map(instance.input, location)
            if isinstance(instance, FlowInstance)
            else (),
            steps=steps,
            input_schema_path=resource_path(input_schema_path)
            if input_schema_path is not None
            else None,
            input_schema=input_schema,
            output=output,
            transition=_transition(
                instance.transition, instance_where.below("transition"), location
            )
            if isinstance(instance, FlowInstance)
            else None,
            on_unresolved=default_review if inherited else own_review,
            callable=isinstance(instance, CallableFlow),
            location=location,
            repeat=_repeat(instance, instance_where),
            on_unresolved_inherited=inherited,
        ),
        {resource_path(key): value for key, value in schemas.items()},
        _FlowSource(instance_where, flow_where, step_wheres, entry_wheres),
    )


def _validate_model_capabilities(
    steps: Mapping[str, CompiledStep],
    profiles: Mapping[str, ModelConfig] | None,
    wheres: Mapping[str, _Where],
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
            _fail_at("unknown_model", wheres[step.name], "model")
        schema_output = isinstance(step, DecisionStepPlan) or step.output_kind == "schema"
        tools = isinstance(step, LlmStepPlan) and step.tools is not None
        if (
            (schema_output and not profile.supports_json_schema)
            or (not schema_output and not profile.supports_text)
            or (tools and not profile.supports_tools)
            or (schema_output and profile.output_mode == "tool" and not profile.supports_tools)
        ):
            _fail_at(
                "unsupported_model_capability",
                wheres[step.name],
                "model",
                message=f"The model profile of step `{_name(step.name)}` does not support "
                "its output or tool policy.",
            )


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
    root: Path,
    origin: Path,
    value: str,
    location: SourceLocation,
    *,
    flow: bool,
    field: str | None = None,
) -> Path:
    reason = "invalid_flow_path" if flow else "invalid_step_path"
    if "://" in value or Path(value).is_absolute():
        _fail(reason, location, field)
    try:
        candidate = (origin / value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail(reason, location, field)
    allowed = {".yaml", ".yml"} if flow else {".yaml", ".yml", ".md"}
    if (
        not candidate.is_file()
        or not candidate.is_relative_to(root)
        or candidate.suffix not in allowed
    ):
        _fail(reason, location, field)
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


type _Located = tuple[TransitionTargetPlan, _Where, tuple[str | int, ...]]


def _target_key(target: TransitionTargetPlan) -> str:
    return "flow" if target.flow is not None else "outcome"


def _located_targets(
    route: TransitionTargetPlan | MatchRoutingPlan | ConditionalRoutingPlan | UnresolvedRoutingPlan,
    where: _Where,
) -> list[_Located]:
    """Every target of a route with the key path where it is authored."""
    if isinstance(route, MatchRoutingPlan):
        located: list[_Located] = [
            (target, where, ("cases", key, _target_key(target))) for key, target in route.cases
        ]
        located.append((route.default, where, ("default", _target_key(route.default))))
        return located
    if isinstance(route, ConditionalRoutingPlan):
        return [
            (entry.target, where, ("route", index, _target_key(entry.target)))
            for index, entry in enumerate(route.entries)
        ]
    if isinstance(route, UnresolvedRoutingPlan):
        located = [(route.default, where, ("default", _target_key(route.default)))]
        located.extend(
            (target, where, (issue, _target_key(target))) for issue, target in route.issues
        )
        return located
    return [(route, where, (_target_key(route),))]


def _review_where(flow: FlowPlan, source: _FlowSource, workflow_where: _Where) -> _Where:
    if flow.on_unresolved_inherited:
        return workflow_where.below("defaults", "on_unresolved")
    return source.instance.below("on_unresolved")


def _flow_located_targets(
    flow: FlowPlan, source: _FlowSource, workflow_where: _Where
) -> tuple[list[_Located], list[_Located]]:
    """Transition and review targets of one routed flow with their authored paths."""
    transition = (
        _located_targets(flow.transition, source.instance.below("transition"))
        if flow.transition is not None
        else []
    )
    review = (
        _located_targets(flow.on_unresolved, _review_where(flow, source, workflow_where))
        if flow.on_unresolved is not None
        else []
    )
    return transition, review


@dataclass(frozen=True, slots=True)
class _Graph:
    """Dominators, may-precede sets and retry ownership of the routed graph.

    ``retry_of`` holds the retry flows of routed flows only; their records are
    workflow-level. A callable flow's retry flow is item-local. ``successors``
    are the routed targets of each routed flow (transitions and review routes).
    """

    dominators: Mapping[str, frozenset[str]]
    ancestors: Mapping[str, frozenset[str]]
    retry_of: Mapping[str, str]
    successors: Mapping[str, tuple[str, ...]]

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
            unknown = sorted(set(dict(repeat.retry_input)) - keys)
            _fail_at(
                "invalid_repeat",
                here,
                "retry_input",
                message=f"`retry_input` of flow `{_name(name)}` overrides "
                + ", ".join(f"`{_name(item)}`" for item in unknown)
                + ", which the flow's input does not declare.",
            )
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
                _fail_at(
                    "invalid_repeat",
                    here,
                    "retry_input",
                    message=f"`retry_input` of flow `{_name(name)}` reads a callable flow, "
                    "but the repeat has no `retry` flow that could produce it.",
                )
            continue
        target = flows.get(repeat.retry_flow)
        retry = _name(repeat.retry_flow)
        problem: str | None = None
        if target is None:
            problem = f"`{retry}` is not a declared flow"
        elif not target.callable:
            problem = f"`{retry}` is a routed flow; a retry flow must be `callable: true`"
        elif repeat.retry_flow == name:
            problem = "a flow cannot be its own retry flow"
        elif repeat.retry_flow in retry_of:
            owner = _name(retry_of[repeat.retry_flow])
            problem = f"`{retry}` already serves the repeat of `{owner}`"
        elif target.repeat is not None:
            # A retry run is one execution; a retry flow cannot repeat itself.
            problem = f"`{retry}` declares its own `repeat`"
        if problem is not None:
            _fail_at(
                "invalid_repeat",
                here,
                "retry",
                "flow",
                message=f"The retry flow of `{_name(name)}` is invalid: {problem}.",
            )
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


def _start_paths(start: str | ConditionalRoutingPlan) -> list[tuple[str, tuple[str | int, ...]]]:
    if isinstance(start, str):
        return [(start, ("start",))]
    return [
        (entry.target.flow, ("start", "route", index, "flow"))
        for index, entry in enumerate(start.entries)
        if entry.target.flow is not None
    ]


type _Edge = tuple[str, int, _Where, tuple[str | int, ...]]
"""Target, collection nesting weight and the authored location of the edge."""


def _validate_graph(
    flows: Mapping[str, FlowPlan],
    start: str | ConditionalRoutingPlan,
    retry_of: Mapping[str, str],
    sources: Mapping[str, _FlowSource],
    where: _Where,
) -> _Graph:
    starts = tuple(dict.fromkeys(name for name, _ in _start_paths(start)))
    for candidate, path in _start_paths(start):
        if candidate not in flows or flows[candidate].callable:
            _fail_at(
                "missing_start",
                where,
                *path,
                message=f"The start `{_name(candidate)}` is "
                + ("a callable flow." if candidate in flows else "not a declared flow."),
            )
    routed = {
        name: tuple(
            dict.fromkeys(target.flow for target in _flow_targets(flow) if target.flow is not None)
        )
        for name, flow in flows.items()
    }
    graph: dict[str, tuple[_Edge, ...]] = {}
    for name, flow in flows.items():
        source = sources[name]
        edges: list[_Edge] = []
        transition, review = (
            _flow_located_targets(flow, source, where) if not flow.callable else ([], [])
        )
        for target, target_where, path in review:
            if target.outcome == "completed":
                _fail_at(
                    "review_completes_run",
                    target_where,
                    *path,
                    message=f"The review route of flow `{_name(name)}` targets `outcome: "
                    "completed`; a run that needs review would be reported as completed.",
                )
        for target, target_where, path in (*transition, *review):
            if target.flow is None:
                continue
            if target.flow not in flows:
                _fail_at(
                    "missing_flow",
                    target_where,
                    *path,
                    message=f"Flow `{_name(name)}` routes to `{_name(target.flow)}`, which is "
                    "not declared under `flows`.",
                )
            if flows[target.flow].callable:
                _fail_at(
                    "invalid_routed_flow",
                    target_where,
                    *path,
                    message=f"Flow `{_name(name)}` routes to callable flow "
                    f"`{_name(target.flow)}`; callable flows run only from a collection or "
                    "a repeat.",
                )
            edges.append((target.flow, 0, target_where, path))
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            step_where = source.steps[step.name]
            for index, target_name in enumerate(step.flows):
                if target_name not in flows or not flows[target_name].callable:
                    _fail_at(
                        "invalid_callable_flow",
                        step_where,
                        "flows",
                        index,
                        message=f"Collection step `{_name(step.name)}` allowlists "
                        f"`{_name(target_name)}`, which is not a declared callable flow.",
                    )
                edges.append((target_name, 1, step_where, ("flows", index)))
        # A retry flow runs at the workflow level; it adds no collection nesting.
        edges.extend(
            (retry, 0, source.instance, ("repeat", "retry", "flow"))
            for retry, owner in retry_of.items()
            if owner == name
        )
        unique: dict[tuple[str, int], _Edge] = {}
        for edge in edges:
            unique.setdefault((edge[0], edge[1]), edge)
        graph[name] = tuple(unique.values())
    for name, flow in flows.items():
        if flow.callable:
            continue
        review_where = _review_where(flow, sources[name], where)
        for target, target_where, path in _flow_located_targets(flow, sources[name], where)[1]:
            if target.flow is None:
                continue
            if flow.on_unresolved_inherited:
                if target.flow == name or _reaches(routed, target.flow, name):
                    _fail(
                        "invalid_default_review_route",
                        review_where.at(),
                        review_where.field(),
                        f"Flow `{_name(name)}` inherits `defaults.on_unresolved`, whose target "
                        f"`{_name(target.flow)}` "
                        + (
                            "is the flow itself"
                            if target.flow == name
                            else f"routes back to `{_name(name)}`"
                        )
                        + "; declare an own `on_unresolved` on one of them.",
                    )
            elif target.flow == name:
                _fail_at(
                    "review_route_to_self",
                    target_where,
                    *path,
                    message=f"The review route of flow `{_name(name)}` targets the flow itself; "
                    "a flow runs at most once.",
                )
    visited: set[str] = set()
    stack: list[str] = []
    call_depths: dict[str, int] = {}
    pending: list[tuple[str, bool, _Edge | None, str | None]] = [
        (candidate, False, None, None) for candidate in reversed(starts)
    ]
    while pending:
        name, exiting, via, parent = pending.pop()
        if exiting:
            call_depths[name] = max(
                (call_depths[target] + weight for target, weight, _, _ in graph[name]), default=0
            )
            if call_depths[name] > MAX_COLLECTION_DEPTH:
                _fail_at(
                    "collection_depth_exceeded",
                    sources[name].instance,
                    message=f"Callable flows below `{_name(name)}` nest "
                    f"{call_depths[name]} collection levels; at most {MAX_COLLECTION_DEPTH} "
                    "are allowed.",
                )
            stack.pop()
            visited.add(name)
            continue
        if name in stack:
            assert via is not None and parent is not None
            cycle = [*stack[stack.index(name) :], name]
            _fail_at(
                "workflow_cycle",
                via[2],
                *via[3],
                message="The flows form a cycle: "
                + " -> ".join(f"`{_name(item)}`" for item in cycle)
                + ", so a run might never terminate.",
            )
        if name in visited:
            continue
        stack.append(name)
        pending.append((name, True, None, None))
        pending.extend((item[0], False, item, name) for item in reversed(graph[name]))
    if visited != set(flows):
        orphan = next(name for name in flows if name not in visited)
        _fail_at(
            "unreachable_flow",
            where,
            "flows",
            orphan,
            message=f"Flow `{_name(orphan)}` is not reachable from any start candidate through "
            "a route, a collection call or a repeat retry, so it can never run.",
        )
    routed_flows = {name for name, flow in flows.items() if not flow.callable}
    entry = ""  # A virtual root precedes every start candidate; it is never a flow ID.
    predecessors = {name: set[str]() for name in routed_flows}
    for name, targets in routed.items():
        for successor in targets:
            predecessors[successor].add(name)
    for candidate in starts:
        predecessors[candidate].add(entry)
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
        {name: routed[name] for name in routed_flows},
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
            _fail(
                "dangling_pointer",
                location,
                field,
                "Workflow-boundary pointers read /payload, /metadata or "
                "/flows/<id>/result|attempts only.",
            )
        if parts[0] != "flows":
            return
        if len(parts) < 3 or parts[1] not in flows or parts[2] not in {"result", "attempts"}:
            _fail(
                "invalid_flow_reference",
                location,
                field,
                f"`{safe_text(pointer, 160)}` does not select /flows/<declared flow>/result or "
                "/attempts.",
            )
        target = flows[parts[1]]
        retry = parts[1] in graph.retry_of
        if target.callable and not retry:
            _fail(
                "invalid_flow_reference",
                location,
                field,
                f"Callable flow `{_name(parts[1])}` has no workflow-level record; bind its "
                "result through the collection step that calls it.",
            )
        if parts[2] == "attempts" and target.repeat is None and not retry:
            _fail(
                "invalid_flow_reference",
                location,
                field,
                f"Flow `{_name(parts[1])}` does not repeat, so it has no `attempts`.",
            )

    return check


def _flow_available(available: Collection[str]) -> Callable[[str], bool]:
    def check(pointer: str) -> bool:
        parts = tokens(pointer)
        return not parts or parts[0] != "flows" or parts[1] in available

    return check


def _unavailable_message(
    binding: BindingPlan, available: Collection[str], subject: str, graph: _Graph
) -> str:
    missing = sorted(
        {
            parts[1]
            for pointer in _pointers(binding)
            if len(parts := tokens(pointer)) > 1 and parts[0] == "flows"
            if parts[1] not in available
        }
    )
    reasons = ", ".join(
        f"`{_name(item)}` "
        + ("(a retry flow runs only after a failed attempt)" if item in graph.retry_of else "")
        for item in missing
    ).replace(" ,", ",")
    return (
        f"{subject} requires the result of {reasons.strip()}, which does not run on every path "
        "here; the run would fail with missing_binding. Add a `default`."
    )


def _boundary_bindings(
    pairs: tuple[tuple[str, BindingPlan], ...],
    *,
    expected_path: str | None,
    schemas: Mapping[str, dict[str, object]],
    scope: WorkflowScope,
    graph: _Graph,
    flows: Mapping[str, FlowPlan],
    available: Collection[str],
    where: _Where,
    base: tuple[str | int, ...],
    subject: str,
    completed: Collection[str] = (),
) -> None:
    """Check boundary bindings for availability, schema paths and target types.

    ``completed`` names the flows known to have completed (not reviewed) here.
    """
    resolved = scope.completed_at(completed)
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
            _fail_at("incompatible_binding_type", where, *base)
        if isinstance(node, dict) and isinstance(node.get("required"), list):
            missing = sorted(set(cast(list[str], node["required"])) - set(dict(pairs)))
            if missing:
                _fail_at(
                    "invalid_input_bindings",
                    where,
                    *base,
                    message=f"{subject} binds no value for the required input "
                    + ", ".join(f"`{_name(item)}`" for item in missing)
                    + ".",
                )

    for key, binding in pairs:
        location, field = where.at(*base, key), where.field(*base, key)

        def unavailable(
            binding: BindingPlan = binding,
            location: SourceLocation = location,
            field: str | None = field,
            key: str = key,
        ) -> Never:
            _fail(
                "unavailable_flow_reference",
                location,
                field,
                _unavailable_message(binding, available, f"{subject} `{_name(key)}`", graph),
            )

        _require(
            binding,
            check=_flow_reference_check(flows, graph, location, field),
            available=_flow_available(available),
            fail=unavailable,
        )
        actual = _binding_value(binding, scope, location, field)
        if _may_be_missing(binding, resolved):
            _fail(
                "unavailable_value",
                location,
                field,
                f"{subject} `{_name(key)}` can be missing at run time: it selects a repeat "
                "attempt that may not run, or a key the flow result's `default` lacks.",
            )
        if expected is None:
            continue
        target = expected.child(key)
        if target is None:
            _fail(
                "invalid_input_bindings",
                location,
                field,
                f"{subject} binds `{_name(key)}`, which the input schema does not accept.",
            )
        if incompatible_types(types(actual), schema_types(target)):
            _fail(
                "incompatible_binding_type",
                location,
                field,
                _type_message(actual, schema_types(target)),
            )
        _check_default_type(binding, schema_types(target), location, field)


def _completed_before(flows: Mapping[str, FlowPlan], graph: _Graph, consumer: str) -> set[str]:
    """Routed flows that ran and completed on every path reaching ``consumer``.

    A flow that may precede ``consumer`` through its review route may have
    stopped for review, so its output defaults may apply.
    """
    completed: set[str] = set()
    for other in graph.may_have_run(consumer) - {consumer}:
        flow = flows.get(other)
        if flow is None or flow.callable:
            continue
        if not any(
            target == consumer or _reaches(graph.successors, target, consumer)
            for target in _review_flow_targets(flow)
        ):
            completed.add(other)
    return completed


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
) -> set[int]:
    """Scope and type checks for route entries; warn about and return unreachable entries."""
    decided = False
    dead: set[int] = set()
    for index, entry in enumerate(route.entries):
        location = where.at("route", index)
        if decided:
            dead.add(index)
            diagnostics.add(
                "route_unreachable_entry",
                location,
                f"Entry {index} of `{field}` follows an entry whose condition always holds.",
                field_path=where.field("route", index),
            )
            continue
        if entry.when is None:
            continue
        condition_location = where.at("route", index, "when")
        condition_field = where.field("route", index, "when") or field
        _check_scope(
            entry.when,
            allowed=allowed,
            flows=flows,
            graph=graph,
            location=condition_location,
            field=condition_field,
            start=start,
        )
        verdict = check_condition(
            entry.when,
            resolve=scope,
            location=condition_location,
            field=condition_field,
            diagnostics=diagnostics,
        )
        if verdict is False:
            dead.add(index)
            diagnostics.add(
                "route_unreachable_entry",
                location,
                f"Entry {index} of `{field}` can never be selected: its condition is "
                "statically false.",
                field_path=where.field("route", index),
            )
        elif verdict is True:
            decided = True
    return dead


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
            _fail(
                "unavailable_flow_reference",
                location,
                field,
                "A start condition runs before any flow; it reads /payload and /metadata only.",
            )
        check(pointer)
        if parts[:1] == ["flows"] and parts[1] not in allowed:
            _fail(
                "unavailable_flow_reference",
                location,
                field,
                f"The condition reads flow `{_name(parts[1])}`, which can never have run "
                "before it is evaluated.",
            )


def _coverage(
    flow: FlowPlan,
    route: MatchRoutingPlan,
    scope: WorkflowScope,
    where: _Where,
    diagnostics: Diagnostics,
) -> None:
    """Compare case keys with the statically allowed values of the routed field."""
    location = where.at("cases")
    allowed = values(scope.binding(route.binding))
    if allowed is None:
        if route.default_covers:
            _fail_at(
                "default_covers_mismatch",
                where,
                "default_covers",
                message=f"The allowed values of the field routed by flow `{_name(flow.name)}` "
                "are unknown, so `default_covers` cannot be checked; remove it or declare an "
                "enum or const.",
            )
        diagnostics.add(
            "case_on_unknown_type",
            location,
            f"The allowed values of the field routed by flow `{flow.name}` are unknown "
            "(no enum or const in its schema); case keys cannot be checked.",
            field_path=where.field("cases"),
        )
        return
    strings = [value for value in allowed.values() if isinstance(value, str)]
    keys = [key for key, _ in route.cases]
    for key in keys:
        if key not in strings:
            _fail(
                "unmatched_case",
                where.at("cases", key),
                where.field("cases"),
                f"Case `{safe_text(key)}` of flow `{_name(flow.name)}` can never match; the "
                "routed field allows only "
                + (", ".join(f"`{safe_text(value)}`" for value in strings) or "no strings")
                + ".",
            )
    uncovered = [value for value in strings if value not in keys]
    if route.default_covers:
        if sorted(route.default_covers) != sorted(uncovered):
            _fail_at(
                "default_covers_mismatch",
                where,
                "default_covers",
                message=f"`default_covers` of flow `{_name(flow.name)}` must list exactly the "
                "values without a case: "
                + (", ".join(f"`{safe_text(value)}`" for value in uncovered) or "none")
                + ".",
            )
    elif uncovered:
        diagnostics.add(
            "uncovered_value",
            location,
            f"Flow `{flow.name}` routes these allowed values to `default` without a case: "
            + ", ".join(safe_text(value) for value in uncovered)
            + ". List them in `default_covers` when that is intended.",
            field_path=where.field("cases"),
        )


def _validate_boundaries(
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    output: BindingPlan | None,
    start: str | ConditionalRoutingPlan,
    scope: WorkflowScope,
    schemas: Mapping[str, dict[str, object]],
    where: _Where,
    sources: Mapping[str, _FlowSource],
    diagnostics: Diagnostics,
) -> set[tuple[str, int]]:
    """Check every boundary binding and route; return the dead route entries.

    A dead entry is ``(source, index)``: a ``route`` entry that can never be
    selected; ``source`` is ``""`` for the routed start.
    """
    dominators = graph.dominators
    dead: set[tuple[str, int]] = set()
    if isinstance(start, ConditionalRoutingPlan):
        dead.update(
            ("", index)
            for index in _check_route_conditions(
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
        )
    for name, flow in flows.items():
        source = sources[name]
        if flow.callable:
            if flow.input_schema_path is not None:
                schema = dict(schemas[flow.input_schema_path])
                view = SchemaView(schema, schema, flow.input_schema_path, schemas)
                if incompatible_types({"object"}, schema_types(view)):
                    _fail_at("incompatible_binding_type", source.definition, "input_schema")
            if flow.repeat is not None:
                _validate_item_repeat(
                    flow,
                    flows=flows,
                    scope=scope,
                    schemas=schemas,
                    where=source.instance.below("repeat"),
                    diagnostics=diagnostics,
                )
            continue
        instance = source.instance
        completed = _completed_before(flows, graph, name)
        _boundary_bindings(
            flow.input,
            expected_path=flow.input_schema_path,
            schemas=schemas,
            scope=scope,
            graph=graph,
            flows=flows,
            available=dominators[name] - {name},
            where=instance,
            base=("input",),
            subject=f"The input of flow `{_name(name)}`",
            completed=completed,
        )
        allowed = graph.may_have_run(name)
        transition = flow.transition
        transition_where = instance.below("transition")
        if isinstance(transition, MatchRoutingPlan):
            binding = transition.binding
            location, field = transition_where.at("binding"), transition_where.field("binding")

            def unavailable(
                binding: BindingPlan = binding,
                location: SourceLocation = location,
                field: str | None = field,
                name: str = name,
            ) -> Never:
                _fail(
                    "unavailable_flow_reference",
                    location,
                    field,
                    _unavailable_message(
                        binding,
                        dominators[name],
                        f"The `cases` binding of flow `{_name(name)}`",
                        graph,
                    ),
                )

            _require(
                binding,
                check=_flow_reference_check(flows, graph, location, field),
                available=_flow_available(dominators[name]),
                fail=unavailable,
            )
            value = _binding_value(binding, scope, location, field)
            if _may_be_missing(binding, scope.completed_at({*completed, name})):
                _fail(
                    "unavailable_value",
                    location,
                    field,
                    f"The `cases` binding of flow `{_name(name)}` can be missing at run time; "
                    "add a `default`.",
                )
            kinds = types(value)
            if kinds is not None and not kinds <= {"string", "null"}:
                _fail(
                    "incompatible_route_type",
                    location,
                    field,
                    f"The `cases` binding of flow `{_name(name)}` may be a JSON "
                    + ", ".join(sorted(kinds - {"string", "null"}))
                    + "; only strings and null select a case or `default`, other values fail "
                    "the run.",
                )
            if (
                binding.has_default
                and binding.default is not None
                and not isinstance(binding.default, str)
            ):
                _fail_at(
                    "incompatible_route_type",
                    transition_where,
                    "binding",
                    "default",
                    message="The default of a `cases` binding must be a string or null.",
                )
            _coverage(flow, transition, scope, transition_where, diagnostics)
        elif isinstance(transition, ConditionalRoutingPlan):
            dead.update(
                (name, index)
                for index in _check_route_conditions(
                    transition,
                    allowed=allowed,
                    flows=flows,
                    graph=graph,
                    scope=scope,
                    where=transition_where,
                    field="transition",
                    diagnostics=diagnostics,
                )
            )
        if isinstance(flow.on_unresolved, ConditionalRoutingPlan):
            _check_route_conditions(
                flow.on_unresolved,
                allowed=allowed,
                flows=flows,
                graph=graph,
                scope=scope,
                where=_review_where(flow, source, where),
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
                where=instance.below("repeat"),
                diagnostics=diagnostics,
                completed={*completed, name},
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
        _boundary_bindings(
            (("output", output),),
            expected_path=None,
            schemas=schemas,
            scope=scope,
            graph=graph,
            flows=flows,
            available=available,
            where=where,
            base=(),
            subject="The workflow",
        )
    return dead


def _validate_repeat_bindings(
    flow: FlowPlan,
    *,
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    scope: WorkflowScope,
    schemas: Mapping[str, dict[str, object]],
    where: _Where,
    diagnostics: Diagnostics,
    completed: Collection[str],
) -> None:
    repeat = flow.repeat
    assert repeat is not None
    name = flow.name
    dominators = graph.dominators[name]
    allowed = graph.may_have_run(name)
    until_field = where.field("until") or "repeat.until"
    _check_scope(
        repeat.until,
        allowed=allowed,
        flows=flows,
        graph=graph,
        location=where.at("until"),
        field=until_field,
    )
    check_condition(
        repeat.until,
        resolve=scope,
        location=where.at("until"),
        field=until_field,
        diagnostics=diagnostics,
    )
    retry = repeat.retry_flow
    if retry is not None:
        _boundary_bindings(
            repeat.retry_flow_input,
            expected_path=flows[retry].input_schema_path,
            schemas=schemas,
            scope=scope,
            graph=graph,
            flows=flows,
            available=dominators,
            where=where,
            base=("retry", "input"),
            subject=f"The retry input of flow `{_name(name)}`",
            completed=completed,
        )
        if repeat.continue_when is not None:
            condition_location = where.at("retry", "continue_when")
            condition_field = where.field("retry", "continue_when") or "repeat.retry"
            _check_scope(
                repeat.continue_when,
                allowed=allowed | {retry},
                flows=flows,
                graph=graph,
                location=condition_location,
                field=condition_field,
            )
            check_condition(
                repeat.continue_when,
                resolve=scope,
                location=condition_location,
                field=condition_field,
                diagnostics=diagnostics,
            )
    if repeat.retry_input:
        expected = flow.input_schema_path
        pairs = repeat.retry_input
        _boundary_bindings(
            pairs,
            expected_path=None,
            schemas=schemas,
            scope=scope,
            graph=graph,
            flows=flows,
            available=dominators | ({retry} if retry is not None else set()),
            where=where,
            base=("retry_input",),
            subject=f"`retry_input` of flow `{_name(name)}`",
            # Attempts after the first start once the retry flow has completed.
            completed={*completed, *((retry,) if retry is not None else ())},
        )
        if expected is not None:
            view = SchemaView(dict(schemas[expected]), dict(schemas[expected]), expected, schemas)
            for key, binding in pairs:
                location, field = where.at("retry_input", key), where.field("retry_input", key)
                target = view.child(key)
                actual = _binding_value(binding, scope, location, field)
                if target is not None and incompatible_types(types(actual), schema_types(target)):
                    _fail(
                        "incompatible_binding_type",
                        location,
                        field,
                        _type_message(actual, schema_types(target)),
                    )
                if target is not None:
                    _check_default_type(binding, schema_types(target), location, field)


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

    def reference_check(location: SourceLocation, field: str | None) -> Callable[[str], None]:
        def check(pointer: str) -> None:
            parts = tokens(pointer)
            if not parts:
                return
            if parts[0] not in {"payload", "metadata", "flows"}:
                _fail(
                    "dangling_pointer",
                    location,
                    field,
                    "In a callable flow's repeat, pointers read /payload (the item input), "
                    "/metadata and this flow's and its retry flow's records only.",
                )
            if parts[0] == "flows" and (
                len(parts) < 3 or parts[1] not in local or parts[2] not in {"result", "attempts"}
            ):
                _fail(
                    "invalid_flow_reference",
                    location,
                    field,
                    f"The item-scoped repeat of `{_name(name)}` can read only "
                    f"/flows/{_name(name)} and its retry flow.",
                )

        return check

    def condition(value: ConditionPlan, location: SourceLocation, field: str) -> None:
        check = reference_check(location, field)
        for pointer in condition_pointers(value):
            check(pointer)
        check_condition(
            value, resolve=item_scope, location=location, field=field, diagnostics=diagnostics
        )

    condition(repeat.until, where.at("until"), where.field("until") or "repeat.until")
    for base, pairs, available in (
        (("retry", "input"), repeat.retry_flow_input, {name}),
        (("retry_input",), repeat.retry_input, set(local)),
    ):
        # The attempt (and, for `retry_input`, the retry run) completed before.
        resolved = item_scope.completed_at(available)
        for key, binding in pairs:
            location, field = where.at(*base, key), where.field(*base, key)

            def unavailable(
                location: SourceLocation = location, field: str | None = field
            ) -> Never:
                _fail(
                    "unavailable_flow_reference",
                    location,
                    field,
                    "The binding reads the retry flow, which has not run at this point; add "
                    "a `default`.",
                )

            _require(
                binding,
                check=reference_check(location, field),
                available=_flow_available(available),
                fail=unavailable,
            )
            _binding_value(binding, item_scope, location, field)
            if _may_be_missing(binding, resolved):
                _fail(
                    "unavailable_value",
                    location,
                    field,
                    "The binding can be missing at run time; add a `default`.",
                )
    if retry is not None:
        expected = flows[retry].input_schema_path
        if expected is not None:
            view = SchemaView(dict(schemas[expected]), dict(schemas[expected]), expected, schemas)
            node = view.resolved().node
            if isinstance(node, dict) and isinstance(node.get("required"), list):
                missing = sorted(
                    set(cast(list[str], node["required"])) - set(dict(repeat.retry_flow_input))
                )
                if missing:
                    _fail_at(
                        "invalid_input_bindings",
                        where,
                        "retry",
                        "input",
                        message="The retry input binds no value for the required input "
                        + ", ".join(f"`{_name(item)}`" for item in missing)
                        + ".",
                    )
            for key, binding in repeat.retry_flow_input:
                location = where.at("retry", "input", key)
                field = where.field("retry", "input", key)
                target = view.child(key)
                if target is None:
                    _fail_at("invalid_input_bindings", where, "retry", "input", key)
                actual = _binding_value(binding, item_scope, location, field)
                if incompatible_types(types(actual), schema_types(target)):
                    _fail(
                        "incompatible_binding_type",
                        location,
                        field,
                        _type_message(actual, schema_types(target)),
                    )
                _check_default_type(binding, schema_types(target), location, field)
        if repeat.continue_when is not None:
            condition(
                repeat.continue_when,
                where.at("retry", "continue_when"),
                where.field("retry", "continue_when") or "repeat.retry",
            )
    if repeat.retry_input and flow.input_schema_path is not None:
        schema = dict(schemas[flow.input_schema_path])
        view = SchemaView(schema, schema, flow.input_schema_path, schemas)
        for key, binding in repeat.retry_input:
            location, field = where.at("retry_input", key), where.field("retry_input", key)
            target = view.child(key)
            actual = _binding_value(binding, item_scope, location, field)
            if target is not None and incompatible_types(types(actual), schema_types(target)):
                _fail(
                    "incompatible_binding_type",
                    location,
                    field,
                    _type_message(actual, schema_types(target)),
                )


def _own_worst_steps(flow: FlowPlan, flows: Mapping[str, FlowPlan]) -> int:
    """``repeat_budget``: attempts × own steps + retries × retry steps; collections count one."""
    repeat = flow.repeat
    if repeat is None:
        return len(flow.steps)
    retry = flows[repeat.retry_flow] if repeat.retry_flow is not None else None
    return repeat.max_attempts * len(flow.steps) + (repeat.max_attempts - 1) * (
        len(retry.steps) if retry is not None else 0
    )


def _worst_steps(
    flow: FlowPlan, flows: Mapping[str, FlowPlan], memo: dict[str, int] | None = None
) -> int:
    """Steps one invocation may visit: every attempt, retry run and collection item.

    Nested collections expand recursively (``1 + max_items × worst(child)``);
    skipped steps are counted, so this is a worst case, not a prediction.
    """
    memo = {} if memo is None else memo
    if flow.name in memo:
        return memo[flow.name]
    own = 0
    for step in flow.steps:
        own += 1
        if isinstance(step, FlowCollectionStepPlan):
            child = max(
                (_worst_steps(flows[target], flows, memo) for target in step.flows), default=0
            )
            own += step.max_items * child
    repeat = flow.repeat
    total = own
    if repeat is not None:
        retry = (
            _worst_steps(flows[repeat.retry_flow], flows, memo)
            if repeat.retry_flow is not None
            else 0
        )
        total = repeat.max_attempts * own + (repeat.max_attempts - 1) * retry
    memo[flow.name] = total
    return total


def _validate_budgets(
    flows: Mapping[str, FlowPlan],
    graph: _Graph,
    start: str | ConditionalRoutingPlan,
    max_steps: int | None,
    where: _Where,
    sources: Mapping[str, _FlowSource],
    diagnostics: Diagnostics,
) -> None:
    if max_steps is None:
        return
    memo: dict[str, int] = {}
    collection_warned = False
    for name, flow in flows.items():
        repeat = flow.repeat
        if repeat is not None:
            worst = _own_worst_steps(flow, flows)
            if worst > max_steps:
                _fail_at(
                    "repeat_budget",
                    sources[name].instance,
                    "repeat",
                    "max_attempts",
                    message=f"The repeat of flow `{_name(name)}` may visit {worst} steps "
                    f"({repeat.max_attempts} attempts x {len(flow.steps)} steps"
                    + (
                        f" + {repeat.max_attempts - 1} retries x "
                        f"{len(flows[repeat.retry_flow].steps)} steps"
                        if repeat.retry_flow is not None
                        else ""
                    )
                    + f"), more than execution.max_steps ({max_steps}).",
                )
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            child = max(
                (_worst_steps(flows[target], flows, memo) for target in step.flows), default=0
            )
            if step.max_items * child > max_steps:
                collection_warned = True
                step_where = sources[name].steps[step.name]
                diagnostics.add(
                    "collection_budget",
                    step_where.at("max_items"),
                    f"Collection step `{step.name}` may visit {step.max_items * child} steps "
                    f"({step.max_items} items x {child} steps, including nested collections "
                    f"and item repeats), more than execution.max_steps ({max_steps}).",
                    field_path=step_where.field("max_items"),
                )
    if collection_warned:
        return
    # The most expensive routed path from a start candidate to a terminal outcome.
    longest: dict[str, tuple[int, tuple[str, ...]]] = {}

    def cost(name: str) -> tuple[int, tuple[str, ...]]:
        if name not in longest:
            tail = max(
                (cost(target) for target in graph.successors.get(name, ())),
                default=(0, ()),
                key=lambda item: item[0],
            )
            longest[name] = (_worst_steps(flows[name], flows, memo) + tail[0], (name, *tail[1]))
        return longest[name]

    candidates = [candidate for candidate, _ in _start_paths(start)]
    total, path = max((cost(candidate) for candidate in candidates), key=lambda item: item[0])
    if total <= max_steps:
        return
    running = 0
    crossing = path[-1]
    for name in path:
        running += _worst_steps(flows[name], flows, memo)
        if running > max_steps:
            crossing = name
            break
    diagnostics.add(
        "run_budget",
        where.at("flows", crossing),
        "The path "
        + " -> ".join(f"`{name}`" for name in path)
        + f" may visit {total} steps in the worst case (every step, repeat attempt, retry run "
        f"and collection item), more than execution.max_steps ({max_steps}); a run on this "
        f"path fails with step_limit_reached at `{crossing}`.",
        field_path=where.field("flows", crossing),
    )


def _returns_own_result(output: BindingPlan | None, name: str, ran: Collection[str]) -> bool:
    """True when a run ending in review at ``name`` returns ``name``'s projected result."""
    if output is None or output.kind not in {"pointer", "first_of"}:
        return False
    for pointer in _pointers(output):
        parts = tokens(pointer)
        if parts[:1] != ["flows"]:
            return False
        if len(parts) > 2 and parts[1] == name and parts[2] == "result":
            return True
        if len(parts) > 1 and parts[1] in ran:
            # An earlier flow's result would be selected first.
            return False
    return False


def _review_output(output: BindingPlan | None, ran: Collection[str]) -> str:
    """Describe what the host receives when a run ends in review after ``ran``."""
    if output is None:
        return "the accepted input as payload"
    if output.kind == "literal":
        return "the literal workflow output"
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
                    field_path=where.field("flows", name, "repeat"),
                )
        if flow.callable or flow.on_unresolved is not None:
            continue
        ran = graph.may_have_run(name) - {name}
        if _returns_own_result(output, name, ran):
            diagnostics.add(
                "review_ends_run",
                where.at("flows", name),
                f"Flow `{name}` has no review route: when it stops for review the run ends "
                f"with needs_review and the host receives the projected result of `{name}`.",
                field_path=where.field("flows", name, "on_unresolved"),
                level="info",
            )
            continue
        received = _review_output(output, graph.may_have_run(name))
        diagnostics.add(
            "review_ends_run",
            where.at("flows", name),
            f"Flow `{name}` has no review route: when it stops for review the run ends with "
            f"needs_review and the host receives {received}, not a projection of `{name}`'s "
            "result. Declare `on_unresolved` (a review flow, or `outcome: needs_review` "
            "when that is intended).",
            field_path=where.field("flows", name, "on_unresolved"),
            level="warning",
        )


def _unreachable_through_dead_entries(
    flows: Mapping[str, FlowPlan],
    start: str | ConditionalRoutingPlan,
    retry_of: Mapping[str, str],
    dead: set[tuple[str, int]],
    where: _Where,
) -> None:
    """A flow reachable only through route entries that can never be selected never runs."""
    if not dead:
        return

    def live(route: object, source: str) -> list[str]:
        if isinstance(route, ConditionalRoutingPlan):
            return [
                entry.target.flow
                for index, entry in enumerate(route.entries)
                if entry.target.flow is not None and (source, index) not in dead
            ]
        if isinstance(route, (TransitionTargetPlan, MatchRoutingPlan, UnresolvedRoutingPlan)):
            return [target.flow for target in _route_targets(route) if target.flow is not None]
        return []

    pending = live(start, "") if isinstance(start, ConditionalRoutingPlan) else [start]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        flow = flows[name]
        if flow.transition is not None:
            pending.extend(live(flow.transition, name))
        if flow.on_unresolved is not None:
            # Review routes may be selected by any issue; their conditions are not pruned.
            pending.extend(live(flow.on_unresolved, "review:" + name))
        for step in flow.steps:
            if isinstance(step, FlowCollectionStepPlan):
                pending.extend(step.flows)
        pending.extend(retry for retry, owner in retry_of.items() if owner == name)
    for name in flows:
        if name not in seen:
            entries = ", ".join(
                f"entry {index} of `{'start' if source == '' else _name(source)}`"
                for source, index in sorted(dead)
            )
            _fail_at(
                "unreachable_flow",
                where,
                "flows",
                name,
                message=f"Flow `{_name(name)}` is reachable only through route entries that "
                f"can never be selected ({entries}), so it can never run.",
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
    ``max_steps`` enables the static budget checks for paths, ``repeat`` and
    collections. Non-fatal findings are returned in ``WorkflowPlan.diagnostics``;
    the first error raises :class:`CompilationError`.
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
    where = _Where(YamlLocator(workflow_text, relative_path=location.path))
    workflow = _validate(_WORKFLOW_ADAPTER, raw_workflow, where)
    if workflow.name is None or workflow.start is None:
        _fail(
            "missing_start",
            location,
            "start",
            "The workflow declares several routed flows but no `start`.",
        )
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
            where.at("input_schema"),
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
    default_where = where.below("defaults", "on_unresolved")
    default_review = _unresolved(workflow.defaults.on_unresolved, default_where)
    if default_review is not None:
        for target, target_where, path in _located_targets(default_review, default_where):
            if target.outcome == "completed":
                _fail_at(
                    "review_completes_run",
                    target_where,
                    *path,
                    message="`defaults.on_unresolved` targets `outcome: completed`; a run that "
                    "needs review would be reported as completed.",
                )
    flows: dict[str, FlowPlan] = {}
    flow_sources: dict[str, _FlowSource] = {}
    for name, instance in workflow.flows.items():
        flow, resources, flow_source = _compile_flow(
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
        flow_sources[name] = flow_source
        schemas.update(resources)
    start: str | ConditionalRoutingPlan = (
        workflow.start
        if isinstance(workflow.start, str)
        else _route(workflow.start, where.below("start"))
    )
    retry_of = _validate_repeats(flows, where, schemas)
    graph = _validate_graph(flows, start, retry_of, flow_sources, where)

    def locate_step(flow: str, step: str) -> tuple[SourceLocation, str | None]:
        step_where = flow_sources[flow].steps[step]
        return step_where.at("items"), step_where.field("items")

    validate_collection_literals(flows, schemas, locate_step)
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
    dead = _validate_boundaries(
        flows, graph, output, start, scope, schemas, where, flow_sources, diagnostics
    )
    _unreachable_through_dead_entries(flows, start, retry_of, dead, where)
    _validate_budgets(flows, graph, start, max_steps, where, flow_sources, diagnostics)
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

"""Compile one local workflow bundle into an immutable execution plan."""

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Literal, Never, Protocol, cast

from jsonschema import Draft202012Validator, SchemaError
from pydantic import TypeAdapter, ValidationError

from foliqant.contracts.models import ModelConfig
from foliqant.contracts.workflow import (
    Binding,
    CallableFlow,
    ChoiceQuestionShorthand,
    DecisionStepAuthoring,
    DeclaredToolCatalog,
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
    OrdinalQuestionShorthand,
    PredicateQuestionShorthand,
    RequestUnitsQuestionShorthand,
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
    SchemaResourcePlan,
    SourceLocation,
    ToolPolicyPlan,
    TransitionTargetPlan,
    UnresolvedRoutingPlan,
    WorkflowPlan,
)
from foliqant.core.prompt import compile_prompt
from foliqant.decisions import (
    ChoiceQuestion,
    MultiselectQuestion,
    OrdinalQuestion,
    PredicateQuestion,
    RequestUnitsQuestion,
)

from ._loader import load_step, load_yaml
from .collections import validate_collection_literals
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


def _unresolved(
    value: TransitionTarget | UnresolvedRouting | None,
) -> TransitionTargetPlan | UnresolvedRoutingPlan | None:
    if value is None:
        return None
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


def _validate_bindings(steps: Mapping[str, CompiledStep]) -> None:
    prior: set[str] = set()
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
            if reference is not None:
                if reference not in steps:
                    _fail("dangling_pointer", step.location)
                if reference not in prior and not binding.optional:
                    _fail("unavailable_step_reference", step.location)
        prior.add(name)


def _validate_output(
    output: BindingPlan | None, steps: Mapping[str, CompiledStep], location: SourceLocation
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
    # Every operation may stop for review. A later result needs an explicit default.
    if reference != next(iter(steps)) and not output.optional:
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


def _collection_result_schema() -> dict[str, object]:
    """Known ledger structure; child business results keep their authored types."""
    return {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "flow": {"type": "string"},
                        "status": {"type": "string"},
                        "steps": {"type": "object"},
                        "result": {},
                        "usage": {"type": "object"},
                        "elapsed_seconds": {"type": "number"},
                        "error": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            }
        },
    }


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
        if isinstance(step, FlowCollectionStepPlan):
            return view(_collection_result_schema())
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
                            "kind": {"const": "flow_collection"},
                            "result": {},
                            "partial_result": _collection_result_schema(),
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
        elif isinstance(step, FlowCollectionStepPlan):
            pairs = step.input
            expected = view({"type": "object", "properties": {"items": {"type": "array"}}})
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
            if (
                isinstance(step, DecisionStepPlan)
                and dict(step.source_formats).get(key, "text") == "text"
            ):
                if incompatible_types(schema_types(actual), {"string"}):
                    _fail("incompatible_binding_type", step.location, field + ".*")
                if binding.optional and not isinstance(binding.default, str):
                    _fail("incompatible_binding_type", step.location, field + ".*.default")
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


def _compile_flow(
    name: str,
    instance: FlowInstance | CallableFlow,
    *,
    workflow: WorkflowAuthoring,
    workflow_path: Path,
    root: Path,
    cache: dict[Path, bytes],
    registry: ModelRegistry,
    model_aliases: Mapping[str, str],
    model_profiles: Mapping[str, ModelConfig] | None,
    catalogs: Mapping[str, DeclaredToolCatalog],
    handler_names: Collection[str],
    handler_schemas: Mapping[str, HandlerSchemas],
) -> tuple[FlowPlan, dict[str, dict[str, object]]]:
    assert workflow.name is not None
    path = workflow_path
    location = SourceLocation(path.relative_to(root).as_posix(), 1, 1)
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
    else:
        definition = instance.definition
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
    effective_aliases = dict(model_aliases)
    effective_profiles = dict(model_profiles or {})
    for item in definition.steps:
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
            step_path = _definition_path(bundle, path.parent, reference, location, flow=False)
            raw, body, local_location, _ = load_step(
                step_path, bundle=bundle, source=_read(step_path, root, cache)
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
                default=workflow.defaults.model,
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
        compiled[entry.id] = _compile_step(
            authored,
            step_id=entry.id,
            location=step_location,
            default_model=workflow.defaults.model,
            origin=step_path.parent,
            schema_identity=f"{name}-{entry.id}",
            model_aliases=effective_aliases,
            catalogs=catalogs,
            handler_names=handler_names,
            bundle=bundle,
            schemas=schemas,
            schema_sources=sources,
            validated_schema_nodes=validated_nodes,
        )
    _validate_bindings(compiled)
    output = _binding(definition.output, location) if definition.output is not None else None
    _validate_output(output, compiled, location)
    _validate_model_capabilities(compiled, effective_profiles, allow_missing=model_profiles is None)
    _validate_schema_bindings(
        compiled, input_schema_path, schemas, catalogs, handler_schemas, output, location
    )

    # Resource keys are global to this compiled workflow, while references retain
    # their authored relative bases. No schema bytes are reopened at execution.
    def resource_path(relative: str) -> str:
        return (bundle / relative).relative_to(root).as_posix()

    for relative, source in sources.items():
        cache[bundle / relative] = source
    steps = tuple(
        replace(step, output_schema_path=resource_path(step.output_schema_path))
        if isinstance(step, LlmStepPlan) and step.output_schema_path is not None
        else step
        for step in compiled.values()
    )
    route = instance.transition if isinstance(instance, FlowInstance) else None
    transition = (
        MatchRoutingPlan(
            _binding(route.binding, location),
            tuple((key, _target(target)) for key, target in sorted(route.cases.items())),
            _target(route.default),
        )
        if isinstance(route, MatchRouting)
        else _target(route)
        if route is not None
        else None
    )
    return FlowPlan(
        name=name,
        input=tuple(
            (key, _binding(binding, location)) for key, binding in sorted(instance.input.items())
        )
        if isinstance(instance, FlowInstance)
        else (),
        steps=steps,
        input_schema_path=resource_path(input_schema_path)
        if input_schema_path is not None
        else None,
        input_schema=input_schema,
        output=output,
        transition=transition,
        on_unresolved=_unresolved(instance.on_unresolved)
        if isinstance(instance, FlowInstance)
        else None,
        callable=isinstance(instance, CallableFlow),
        location=location,
    ), {resource_path(key): value for key, value in schemas.items()}


def _flow_targets(flow: FlowPlan) -> tuple[TransitionTargetPlan, ...]:
    targets = (
        [flow.transition.default, *(target for _, target in flow.transition.cases)]
        if isinstance(flow.transition, MatchRoutingPlan)
        else [flow.transition]
        if flow.transition is not None
        else []
    )
    unresolved = flow.on_unresolved
    if isinstance(unresolved, UnresolvedRoutingPlan):
        targets.extend([unresolved.default, *(target for _, target in unresolved.issues)])
    elif unresolved is not None:
        targets.append(unresolved)
    return tuple(targets)


def _validate_graph(
    flows: Mapping[str, FlowPlan], start: str, location: SourceLocation
) -> dict[str, set[str]]:
    if start not in flows or flows[start].callable:
        _fail("missing_start", location)
    routed = {
        name: tuple(
            dict.fromkeys(target.flow for target in _flow_targets(flow) if target.flow is not None)
        )
        for name, flow in flows.items()
    }
    graph: dict[str, tuple[str, ...]] = {}
    for name, flow in flows.items():
        for target in routed[name]:
            if target not in flows:
                _fail("missing_flow", flow.location)
            if flows[target].callable:
                _fail("invalid_routed_flow", flow.location)
        calls = []
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            for target in step.flows:
                if target not in flows or not flows[target].callable:
                    _fail("invalid_callable_flow", step.location, "flows")
                calls.append(target)
        graph[name] = tuple(dict.fromkeys((*routed[name], *calls)))
    visited: set[str] = set()
    active: set[str] = set()
    call_depths: dict[str, int] = {}
    pending = [(start, False)]
    while pending:
        name, exiting = pending.pop()
        if exiting:
            call_depths[name] = max(
                (call_depths[target] + int(flows[target].callable) for target in graph[name]),
                default=0,
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
        pending.extend((target, False) for target in reversed(graph[name]))
    if visited != set(flows):
        _fail("unreachable_flow", flows[sorted(set(flows) - visited)[0]].location)
    routed_flows = {name for name, flow in flows.items() if not flow.callable}
    predecessors = {name: set[str]() for name in routed_flows}
    for name, targets in routed.items():
        for target in targets:
            predecessors[target].add(name)
    dominators = {name: ({name} if name == start else set(routed_flows)) for name in routed_flows}
    changed = True
    while changed:
        changed = False
        for name in routed_flows:
            if name == start:
                continue
            parents = predecessors[name]
            common = (
                set.intersection(*(dominators[parent] for parent in parents)) if parents else set()
            )
            updated = common | {name}
            if updated != dominators[name]:
                dominators[name] = updated
                changed = True
    return dominators


def _boundary_binding(
    binding: BindingPlan,
    flows: Mapping[str, FlowPlan],
    available: set[str],
    location: SourceLocation,
) -> None:
    if binding.kind != "pointer" or binding.pointer is None:
        return
    tokens = [part.replace("~1", "/").replace("~0", "~") for part in binding.pointer.split("/")[1:]]
    if not tokens:
        return
    if tokens[0] not in {"payload", "metadata", "flows"}:
        _fail("dangling_pointer", location)
    if tokens[0] != "flows":
        return
    if (
        len(tokens) < 3
        or tokens[2] != "result"
        or tokens[1] not in flows
        or flows[tokens[1]].callable
    ):
        _fail("invalid_flow_reference", location)
    if tokens[1] not in available and not binding.optional:
        _fail("unavailable_flow_reference", location)


def _boundary_schema(
    binding: BindingPlan,
    *,
    workflow_input_path: str | None,
    flows: Mapping[str, FlowPlan],
    schemas: Mapping[str, dict[str, object]],
    catalogs: Mapping[str, DeclaredToolCatalog],
    handlers: Mapping[str, HandlerSchemas],
) -> SchemaView:
    def view(schema: dict[str, object], path: str = "") -> SchemaView:
        return SchemaView(schema, schema, path, schemas)

    if binding.kind == "literal":
        return view({"type": json_type(binding.literal)})
    tokens = [
        part.replace("~1", "/").replace("~0", "~")
        for part in (binding.pointer or "").split("/")[1:]
    ]
    result = view({})
    if tokens and tokens[0] == "payload":
        if workflow_input_path is not None:
            result = view(schemas[workflow_input_path], workflow_input_path)
        tokens = tokens[1:]
    elif len(tokens) >= 3 and tokens[0] == "flows" and tokens[1] in flows and tokens[2] == "result":
        flow = flows[tokens[1]]
        output = flow.output
        if output is None:
            result = (
                view(schemas[flow.input_schema_path], flow.input_schema_path)
                if flow.input_schema_path
                else view({"type": "object"})
            )
            tokens = tokens[3:]
        elif output.kind == "literal":
            result = view({"type": json_type(output.literal)})
            tokens = tokens[3:]
        else:
            local = [
                part.replace("~1", "/").replace("~0", "~")
                for part in (output.pointer or "").split("/")[1:]
            ]
            if local and local[0] == "payload":
                result = (
                    view(schemas[flow.input_schema_path], flow.input_schema_path)
                    if flow.input_schema_path
                    else view({"type": "object"})
                )
                tokens = local[1:] + tokens[3:]
            elif len(local) >= 3 and local[0] == "steps" and local[2] == "result":
                step = flow.step(local[1])
                if isinstance(step, LlmStepPlan):
                    result = (
                        view(schemas[step.output_schema_path], step.output_schema_path)
                        if step.output_schema_path
                        else view({"type": "string"})
                    )
                elif isinstance(step, FlowCollectionStepPlan):
                    result = view(_collection_result_schema())
                elif isinstance(step, HandlerStepPlan) and step.handler in handlers:
                    result = view(
                        cast(dict[str, object], thaw_json(handlers[step.handler].output_schema))
                    )
                elif isinstance(step, McpStepPlan):
                    result = view(
                        cast(
                            dict[str, object],
                            catalogs[step.server].tools[step.tool].output_schema or {},
                        )
                    )
                tokens = local[3:] + tokens[3:]
            else:
                return result
            # An optional output may have a differently typed authored fallback.
            # Avoid false static rejection; runtime input validation remains exact.
            if output.optional:
                return view({})
    else:
        return result
    resolved = result.pointer(tokens)
    if resolved is None:
        if binding.optional:
            return view({"type": json_type(binding.default)})
        return view({"type": []})
    return resolved


def _validate_boundaries(
    flows: Mapping[str, FlowPlan],
    dominators: Mapping[str, set[str]],
    output: BindingPlan | None,
    workflow_input_path: str | None,
    schemas: Mapping[str, dict[str, object]],
    catalogs: Mapping[str, DeclaredToolCatalog],
    handlers: Mapping[str, HandlerSchemas],
    location: SourceLocation,
) -> None:
    def source(binding: BindingPlan) -> SchemaView:
        result = _boundary_schema(
            binding,
            workflow_input_path=workflow_input_path,
            flows=flows,
            schemas=schemas,
            catalogs=catalogs,
            handlers=handlers,
        )
        if schema_types(result) == set():
            _fail("dangling_pointer", location)
        return result

    for name, flow in flows.items():
        if flow.callable:
            if flow.input_schema_path is not None:
                schema = dict(schemas[flow.input_schema_path])
                view = SchemaView(schema, schema, flow.input_schema_path, schemas)
                if incompatible_types({"object"}, schema_types(view)):
                    _fail("incompatible_binding_type", flow.location, "input_schema")
            continue
        expected = (
            SchemaView(
                dict(schemas[flow.input_schema_path]),
                dict(schemas[flow.input_schema_path]),
                flow.input_schema_path,
                schemas,
            )
            if flow.input_schema_path is not None
            else None
        )
        if expected is not None:
            node = expected.resolved().node
            if incompatible_types({"object"}, schema_types(expected)):
                _fail("incompatible_binding_type", flow.location, "input")
            if isinstance(node, dict) and isinstance(node.get("required"), list):
                if not set(node["required"]).issubset(dict(flow.input)):
                    _fail("invalid_input_bindings", flow.location, "input")
        for key, binding in flow.input:
            _boundary_binding(binding, flows, dominators[name] - {name}, flow.location)
            actual = source(binding)
            if expected is not None:
                target = expected.child(key)
                if target is None:
                    _fail("invalid_input_bindings", flow.location, "input")
                if incompatible_types(schema_types(actual), schema_types(target)):
                    _fail("incompatible_binding_type", flow.location, "input")
                if binding.optional and incompatible_types(
                    {json_type(binding.default)}, schema_types(target)
                ):
                    _fail("incompatible_binding_type", flow.location, "input")
        if isinstance(flow.transition, MatchRoutingPlan):
            binding = flow.transition.binding
            _boundary_binding(binding, flows, dominators[name], flow.location)
            if incompatible_types(schema_types(source(binding)), {"string", "null"}):
                _fail("incompatible_route_type", flow.location, "transition.binding")
            if (
                binding.optional
                and binding.default is not None
                and not isinstance(binding.default, str)
            ):
                _fail("incompatible_route_type", flow.location, "transition.binding.default")
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
        available = (
            set.intersection(*(dominators[name] for name in terminals)) if terminals else set()
        )
        _boundary_binding(output, flows, available, location)
        source(output)


def compile_workflow(
    directory: Path,
    *,
    model_aliases: Mapping[str, str],
    tool_catalogs: Mapping[str, DeclaredToolCatalog | Mapping[str, object]],
    handler_names: Collection[str],
    model_profiles: Mapping[str, ModelConfig] | None = None,
    handler_schemas: Mapping[str, HandlerSchemas] | None = None,
    configuration_root: Path | None = None,
    _model_registry: ModelRegistry | None = None,
) -> WorkflowPlan:
    """Compile conventional or explicit definitions without contacting dependencies.

    Referenced flows stay inside ``configuration_root`` (the workflow directory
    by default). Step/schema references stay inside their flow definition's
    directory, with paths resolved from the declaring file. All dependency bytes
    are frozen before clients open. Conventional lookup resolves definitions;
    the declared step list and transitions alone determine execution order.
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
    flows: dict[str, FlowPlan] = {}
    for name, instance in workflow.flows.items():
        flow, resources = _compile_flow(
            name,
            instance,
            workflow=workflow,
            workflow_path=workflow_path,
            root=root,
            cache=cache,
            registry=registry,
            model_aliases=model_aliases,
            model_profiles=model_profiles,
            catalogs=catalogs,
            handler_names=handler_names,
            handler_schemas=handler_schemas or {},
        )
        flows[name] = flow
        schemas.update(resources)
    dominators = _validate_graph(flows, workflow.start, location)
    validate_collection_literals(flows, schemas)
    output = _binding(workflow.output, location) if workflow.output is not None else None
    _validate_boundaries(
        flows,
        dominators,
        output,
        input_schema_path,
        schemas,
        catalogs,
        handler_schemas or {},
        location,
    )
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
        start=workflow.start,
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
    )

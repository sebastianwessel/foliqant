"""Offline validation of authored collection literals against confined flow schemas."""

from collections.abc import Mapping
from typing import cast
from urllib.parse import quote

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012, Schema, SchemaRegistry

from foliqant.contracts.execution import FlowCollectionItem
from foliqant.core.json import FrozenJson, thaw_json
from foliqant.core.plan import FlowCollectionStepPlan, FlowPlan

from .errors import CompilationError

_ITEMS = TypeAdapter(list[FlowCollectionItem])
_BASE = "https://foliqant.invalid/compiled/"


def validate_collection_literals(
    flows: Mapping[str, FlowPlan], schemas: Mapping[str, dict[str, object]]
) -> None:
    """Literal/default batches are known exactly; dynamic batches validate at runtime."""
    registry: SchemaRegistry = Registry()
    for path, schema in schemas.items():
        registry = registry.with_resource(
            _BASE + quote(path, safe="/"),
            Resource.from_contents(cast(Schema, schema), default_specification=DRAFT202012),
        )
    for flow in flows.values():
        for step in flow.steps:
            if not isinstance(step, FlowCollectionStepPlan):
                continue
            values: list[FrozenJson] = []
            if step.items.kind == "literal":
                values.append(step.items.literal)
            elif step.items.has_default:
                values.append(step.items.default)
            for value in values:
                try:
                    items = _ITEMS.validate_python(thaw_json(value), strict=True)
                except ValidationError:
                    raise CompilationError(
                        "invalid_collection_items", step.location, field="items"
                    ) from None
                if (
                    len(items) > step.max_items
                    or len({item.id for item in items}) != len(items)
                    or any(item.flow not in step.flows for item in items)
                ):
                    raise CompilationError("invalid_collection_items", step.location, field="items")
                for item in items:
                    target = flows[item.flow]
                    if target.input_schema_path is None:
                        continue
                    validator = Draft202012Validator(
                        {"$ref": _BASE + quote(target.input_schema_path, safe="/")},
                        registry=registry,
                    )
                    try:
                        valid = validator.is_valid(item.input)
                    except (Unresolvable, RecursionError):
                        valid = False
                    if not valid:
                        raise CompilationError(
                            "invalid_collection_input", step.location, field="items"
                        ) from None

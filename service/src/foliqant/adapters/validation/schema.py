"""Reusable JSON Schema validators with a closed in-memory resource registry."""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Never, cast
from urllib.parse import quote

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry, Resource
from referencing import exceptions as referencing_exceptions
from referencing.jsonschema import DRAFT202012, Schema, SchemaRegistry

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import FrozenJson, JsonValue, thaw_json
from foliqant.core.plan import LlmStepPlan, SchemaResourcePlan, WorkflowPlan

from .provider_schema import inline_provider_schema

_MAX_SCHEMA_DEPTH = 64
_MAX_SCHEMA_NODES = 10_000


def _fail(code: ErrorCode) -> Never:
    raise ServiceError(code) from None


def _path(path: str) -> str:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        _fail(ErrorCode.INVALID_CONFIGURATION)
    return path


def _schema(value: object) -> Schema:
    try:
        mutable = thaw_json(cast(FrozenJson, value))
    except (RecursionError, ServiceError):
        _fail(ErrorCode.INVALID_CONFIGURATION)
    if not isinstance(mutable, (bool, dict)):
        _fail(ErrorCode.INVALID_CONFIGURATION)
    return cast(Schema, mutable)


def _resource(resource: SchemaResourcePlan) -> tuple[str, Schema]:
    return _path(resource.path), _schema(resource.schema)


class WorkflowSchemas:
    """Compile frozen workflow schemas into validators with no external retrieval.

    Synthetic HTTPS URIs provide normal RFC relative-reference behavior while
    the ``referencing`` registry's default failing retriever prevents network or
    filesystem fallback. Construction resolves every reachable reference before
    request data is accepted.
    """

    def __init__(self, plan: WorkflowPlan) -> None:
        try:
            self._base_uri = f"https://foliqant.invalid/bundles/{quote(plan.revision, safe='')}/"
            resources: dict[str, Schema] = {}
            registry: SchemaRegistry = Registry()
            for item in plan.schema_resources:
                path, schema = _resource(item)
                if path in resources:
                    _fail(ErrorCode.INVALID_CONFIGURATION)
                resources[path] = schema
                registry = registry.with_resource(
                    self._uri(path),
                    Resource.from_contents(schema, default_specification=DRAFT202012),
                )
            self._registry = registry
            self._resources = resources
            self._input = self._build_optional(
                plan.input_schema_path,
                plan.input_schema,
            )
            outputs: dict[str, Draft202012Validator] = {}
            output_paths: dict[str, str] = {}
            for step in plan.steps:
                if not isinstance(step, LlmStepPlan):
                    continue
                if step.output_kind == "schema":
                    if step.name in outputs:
                        _fail(ErrorCode.INVALID_CONFIGURATION)
                    validator = self._build_optional(
                        step.output_schema_path,
                        step.output_schema,
                    )
                    if validator is None:
                        _fail(ErrorCode.INVALID_CONFIGURATION)
                    outputs[step.name] = validator
                    if step.output_schema_path is None:
                        _fail(ErrorCode.INVALID_CONFIGURATION)
                    output_paths[step.name] = step.output_schema_path
                elif step.output_schema_path is not None or step.output_schema is not None:
                    _fail(ErrorCode.INVALID_CONFIGURATION)
            self._outputs = outputs
            self._output_paths = output_paths
        except ServiceError:
            raise
        except (
            KeyError,
            LookupError,
            RecursionError,
            SchemaError,
            TypeError,
            ValueError,
            referencing_exceptions.Unresolvable,
        ):
            _fail(ErrorCode.INVALID_CONFIGURATION)

    def _uri(self, path: str) -> str:
        return self._base_uri + quote(path, safe="/")

    def _build_optional(
        self, path: str | None, frozen_schema: object | None
    ) -> Draft202012Validator | None:
        if path is None:
            if frozen_schema is not None:
                _fail(ErrorCode.INVALID_CONFIGURATION)
            return None
        if frozen_schema is None:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        normalized = _path(path)
        schema = self._resources.get(normalized)
        if schema is None or schema != _schema(frozen_schema):
            _fail(ErrorCode.INVALID_CONFIGURATION)
        uri = self._uri(normalized)
        self._preflight(schema, uri)
        validator_schema = schema
        if isinstance(schema, Mapping):
            validator_schema = dict(schema)
            validator_schema["$id"] = uri
        return Draft202012Validator(validator_schema, registry=self._registry)

    def _preflight(self, root: Schema, root_uri: str) -> None:
        pending = [(root, self._registry.resolver(root_uri), 0)]
        seen: set[int] = set()
        count = 0
        while pending:
            schema, resolver, depth = pending.pop()
            if depth > _MAX_SCHEMA_DEPTH or count >= _MAX_SCHEMA_NODES:
                _fail(ErrorCode.INVALID_CONFIGURATION)
            if isinstance(schema, bool):
                continue
            if not isinstance(schema, Mapping) or id(schema) in seen:
                continue
            seen.add(id(schema))
            count += 1
            if "$id" in schema:
                _fail(ErrorCode.INVALID_CONFIGURATION)
            Draft202012Validator.check_schema(schema)
            for keyword in ("$ref", "$dynamicRef"):
                reference = schema.get(keyword)
                if not isinstance(reference, str):
                    continue
                resolved = resolver.lookup(reference)
                target = resolved.contents
                if not isinstance(target, (bool, Mapping)):
                    _fail(ErrorCode.INVALID_CONFIGURATION)
                pending.append((target, resolved.resolver, depth + 1))
            for child in DRAFT202012.subresources_of(schema):
                pending.append((child, resolver, depth + 1))

    def validate_input(self, payload: FrozenJson) -> None:
        """Validate request payload or raise a fixed ``INVALID_INPUT`` error."""

        if self._input is None:
            return
        self._validate(self._input, payload, ErrorCode.INVALID_INPUT)

    def validate_output(self, step_id: str, value: FrozenJson) -> None:
        """Validate one schema-output LLM step by exact compiled step ID."""

        validator = self._outputs.get(step_id)
        if validator is None:
            _fail(ErrorCode.INVALID_CONFIGURATION)
        self._validate(validator, value, ErrorCode.INVALID_OUTPUT)

    def provider_output_schema(self, step_id: str) -> dict[str, JsonValue]:
        """Return an independent schema bundle with no external references or I/O."""
        try:
            path = self._output_paths[step_id]
            return inline_provider_schema(self._resources[path], self._uri(path), self._registry)
        except ServiceError:
            raise
        except (
            KeyError,
            LookupError,
            RecursionError,
            TypeError,
            ValueError,
            referencing_exceptions.Unresolvable,
        ):
            _fail(ErrorCode.INVALID_CONFIGURATION)

    @staticmethod
    def _validate(validator: Draft202012Validator, value: FrozenJson, code: ErrorCode) -> None:
        try:
            validator.validate(thaw_json(value))
        except ValidationError:
            _fail(code)
        except (
            LookupError,
            RecursionError,
            TypeError,
            ValueError,
            referencing_exceptions.Unresolvable,
        ):
            _fail(ErrorCode.INVALID_CONFIGURATION)

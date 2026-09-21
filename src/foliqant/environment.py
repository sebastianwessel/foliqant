"""Typed, one-time resolution of explicitly annotated deployment fields."""

from collections.abc import Mapping
from typing import TypeVar, cast

from pydantic import BaseModel, SecretStr

from foliqant.contracts.base import BoundaryModel
from foliqant.contracts.environment import ResolvedSecret, is_environment_reference
from foliqant.core.errors import ErrorCode, ServiceError

_Config = TypeVar("_Config", bound=BaseModel)


class EnvironmentResolver:
    """Snapshot local environment values and resolve only marked settings fields.

    Resolved values are never reinterpreted as references. This object does not
    read files, change the process environment, or contact configured endpoints.
    """

    def __init__(self, environment: Mapping[str, str]) -> None:
        self._environment = dict(environment)

    def __repr__(self) -> str:
        return "EnvironmentResolver()"

    def _value(self, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return ResolvedSecret(cast(str, self._value(value.get_secret_value())))
        if isinstance(value, str):
            if is_environment_reference(value):
                result = self._environment.get(value[1:])
                if not isinstance(result, str) or not result.strip():
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                return result
            return value.replace("$$", "$")
        if isinstance(value, dict):
            return {key: self._value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._value(item) for item in value]
        return value

    def _document(self, model: BaseModel) -> dict[str, object]:
        if isinstance(model, BoundaryModel) and model._environment_resolved:
            return {name: getattr(model, name) for name in type(model).model_fields}
        document: dict[str, object] = {}
        for name, definition in type(model).model_fields.items():
            value = getattr(model, name)
            extra = definition.json_schema_extra
            if isinstance(extra, dict) and extra.get("environment_reference"):
                document[name] = self._value(value)
            elif isinstance(value, BaseModel):
                document[name] = self._document(value)
            elif isinstance(value, dict):
                document[name] = {
                    key: self._document(item) if isinstance(item, BaseModel) else item
                    for key, item in value.items()
                }
            else:
                document[name] = value
        return document

    def resolve(self, config: _Config) -> _Config:
        """Return an ephemeral, validated settings copy; retain no resolved export."""
        if isinstance(config, BoundaryModel) and config._environment_resolved:
            return config
        try:
            resolved = type(config).model_validate(
                self._document(config), strict=True, context={"resolved_environment": True}
            )
            self._mark_resolved(resolved)
            return resolved
        except (ValueError, TypeError):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None

    def _mark_resolved(self, model: BaseModel) -> None:
        if isinstance(model, BoundaryModel):
            model._environment_resolved = True
        for name in type(model).model_fields:
            value = getattr(model, name)
            if isinstance(value, BaseModel):
                self._mark_resolved(value)
            elif isinstance(value, dict):
                for item in value.values():
                    if isinstance(item, BaseModel):
                        self._mark_resolved(item)

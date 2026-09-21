"""Validate protected metadata and bind it to trusted ingress identity."""

from typing import Annotated, Self, cast

from pydantic import (
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic.json_schema import SkipJsonSchema

from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import IDENTITY_MAX_LENGTH, IDENTITY_PATTERN, Identity
from foliqant.core.json import JsonValue

from .base import BoundaryModel

type IdentityId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=IDENTITY_MAX_LENGTH,
        pattern=IDENTITY_PATTERN,
    ),
]
_IDENTITY: TypeAdapter[str] = TypeAdapter(IdentityId)
_IDENTITY_FIELDS = ("tenant_id", "principal_id")


def _omit_absent_default(schema: JsonDict) -> None:
    """Absent protected fields must not advertise a forbidden null default."""
    schema.pop("default", None)


class TraceCarrier(BoundaryModel):
    """An incoming W3C carrier; the propagator decides whether it is valid."""

    traceparent: Annotated[str, Field(max_length=512)] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_absent_default
    )
    tracestate: Annotated[str, Field(max_length=512)] | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_absent_default
    )

    @model_validator(mode="after")
    def reject_explicit_null(self) -> Self:
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("trace carrier fields must be strings when supplied")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        return {key: value for key, value in values.items() if key in self.model_fields_set}


class Metadata(BoundaryModel):
    """Open business metadata with closed, protected identity and trace fields."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    tenant_id: IdentityId | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_absent_default
    )
    principal_id: IdentityId | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_absent_default
    )
    telemetry: TraceCarrier | SkipJsonSchema[None] = Field(
        default=None, json_schema_extra=_omit_absent_default
    )

    @model_validator(mode="after")
    def validate_protected_fields(self) -> Self:
        for key in (*_IDENTITY_FIELDS, "telemetry"):
            if key not in self.model_fields_set:
                continue
            value = getattr(self, key)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError("protected metadata must have a nonblank value when supplied")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, JsonValue]:
        values = cast(dict[str, JsonValue], handler(self))
        for key in (*_IDENTITY_FIELDS, "telemetry"):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class Envelope(BoundaryModel):
    payload: JsonValue
    metadata: Metadata = Field(default_factory=Metadata)


def accept_envelope(envelope: Envelope, identity: Identity) -> AcceptedEnvelope:
    """Check caller identity claims and enrich absent fields from trusted context.

    This does not authenticate a token. Input adapters must authenticate first
    and must not build the trusted identity from these same unverified fields.
    """
    metadata = cast(dict[str, JsonValue], envelope.metadata.model_dump(mode="json"))
    for key in _IDENTITY_FIELDS:
        trusted = getattr(identity, key)
        if trusted is not None:
            try:
                trusted = _IDENTITY.validate_python(trusted, strict=True)
                if not trusted.strip():
                    raise ValueError("blank identity")
            except (ValidationError, ValueError):
                raise ServiceError(ErrorCode.INVALID_INPUT) from None
        if key in metadata and metadata[key] != trusted:
            raise ServiceError(ErrorCode.FORBIDDEN)
        if trusted is not None:
            metadata[key] = trusted
    return AcceptedEnvelope(payload=envelope.payload, metadata=metadata)

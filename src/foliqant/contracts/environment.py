"""Explicit deployment references; workflow and business strings remain literal."""

import re
from typing import Annotated

from pydantic import AfterValidator, Field, PlainSerializer, SecretStr, ValidationInfo
from pydantic.config import JsonDict

_REFERENCE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\Z")
EnvironmentName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


def is_environment_reference(value: str) -> bool:
    """Whether a complete deployment value names one environment variable."""
    return _REFERENCE.fullmatch(value) is not None


def validate_reference(value: str, info: ValidationInfo) -> str:
    """Allow full references or doubled-dollar literal escapes, never interpolation."""
    if info.context and info.context.get("resolved_environment"):
        return value
    if "$" in value.replace("$$", "") and not is_environment_reference(value):
        raise ValueError("use a complete $VARIABLE reference or $$ literal escapes")
    return value


def validate_secret_reference(value: SecretStr, info: ValidationInfo) -> SecretStr:
    validate_reference(value.get_secret_value(), info)
    return value


EnvironmentText = Annotated[str, AfterValidator(validate_reference)]


class ResolvedSecret(SecretStr):
    """A runtime secret whose content must never become a configuration export."""


def _serialize_secret(value: SecretStr) -> str:
    text = value.get_secret_value()
    if not isinstance(value, ResolvedSecret) and is_environment_reference(text):
        return text
    return "**********"


EnvironmentSecret = Annotated[
    SecretStr,
    AfterValidator(validate_secret_reference),
    PlainSerializer(_serialize_secret, return_type=str, when_used="json"),
]
ENVIRONMENT_FIELD: JsonDict = {"environment_reference": True}


def nonblank_credential(value: SecretStr) -> SecretStr:
    if not value.get_secret_value().strip():
        raise ValueError("credential must be nonblank")
    return value


EnvironmentCredential = Annotated[EnvironmentSecret, AfterValidator(nonblank_credential)]

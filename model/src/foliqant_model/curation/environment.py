"""Explicit, typed local environment overrides for curation only."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from pydantic import ValidationError

from ..contracts.cli import ErrorLocation
from ..errors import ModelError
from .contracts import CurationConfig

_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
_PREFIX = "FOLIQANT_CURATION_"


def _invalid(pointer: str) -> ModelError:
    return ModelError(
        "CONFIG_INVALID",
        "Curation environment override is invalid",
        location=ErrorLocation(kind="config-pointer", value=pointer),
    )


def _integer(value: str, pointer: str) -> int:
    if not _INTEGER.fullmatch(value):
        raise _invalid(pointer)
    return int(value)


def _number(value: str, pointer: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise _invalid(pointer) from error
    if not math.isfinite(parsed):
        raise _invalid(pointer)
    return parsed


def _value(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(_PREFIX + name)
    return value if value not in {None, ""} else None


def apply_curation_environment(
    config: CurationConfig, environment: Mapping[str, str]
) -> CurationConfig:
    """Apply the documented allowlist of local endpoint and generation overrides.

    The general configuration reader intentionally never interpolates environment
    variables. This narrow opt-in exists so a root ``.env`` can select a local
    endpoint without turning configuration files into a shell-like language.
    """

    payload = config.model_dump(mode="python")
    endpoint = payload["endpoint"]
    generation = payload["generation"]
    assert isinstance(endpoint, dict) and isinstance(generation, dict)

    values: tuple[tuple[str, str, str], ...] = (
        ("ENDPOINT_URL", "baseUrl", "/endpoint/baseUrl"),
        ("MODEL", "model", "/endpoint/model"),
        ("STRUCTURED_OUTPUT", "structuredOutput", "/endpoint/structuredOutput"),
    )
    for name, field, _pointer in values:
        if value := _value(environment, name):
            endpoint[field] = value
    for name, field, pointer in (
        ("TIMEOUT_SECONDS", "timeoutSeconds", "/endpoint/timeoutSeconds"),
        ("MAX_TOKENS", "maxTokens", "/endpoint/maxTokens"),
        ("MAX_RESPONSE_BYTES", "maxResponseBytes", "/endpoint/maxResponseBytes"),
    ):
        if value := _value(environment, name):
            endpoint[field] = _integer(value, pointer)
    if value := _value(environment, "TEMPERATURE"):
        endpoint["temperature"] = _number(value, "/endpoint/temperature")
    for name, field, pointer in (
        ("MAX_CANDIDATES", "maxCandidates", "/generation/maxCandidates"),
        ("MAX_ATTEMPTS", "maxAttempts", "/generation/maxAttempts"),
        ("SCENARIO_FAMILIES", "scenarioFamilies", "/generation/scenarioFamilies"),
        ("MAX_INPUT_CHARACTERS", "maxInputCharacters", "/generation/maxInputCharacters"),
    ):
        if value := _value(environment, name):
            generation[field] = _integer(value, pointer)
    if value := _value(environment, "LANGUAGES"):
        languages = value.split(",")
        if not languages or any(not item for item in languages):
            raise _invalid("/generation/languages")
        generation["languages"] = languages

    try:
        return CurationConfig.model_validate(payload, strict=True)
    except ValidationError as error:
        location = error.errors(include_input=False, include_context=False, include_url=False)[0][
            "loc"
        ]
        pointer = "/" + "/".join(str(part) for part in location)
        raise _invalid(pointer) from error

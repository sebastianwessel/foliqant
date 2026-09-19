"""Bounded JSON/YAML parsing with no executable tags, aliases or coercion."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import MappingNode, Node
from yaml.tokens import AliasToken, AnchorToken, TagToken

from .contracts.cli import ErrorLocation
from .errors import ModelError

MAX_CONFIG_BYTES = 1_048_576


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ModelError("CONFIG_DUPLICATE_KEY", "Duplicate object key")
        result[key] = value
    return result


def _nonfinite_constant(value: str) -> object:
    raise ModelError("CONFIG_INVALID", "Nonfinite numbers are not permitted")


def _validate_json_tree(value: object) -> None:
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ModelError("CONFIG_INVALID", "Nonfinite numbers are not permitted")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_tree(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ModelError("CONFIG_INVALID", "Object keys must be strings")
            key.encode("utf-8", errors="strict")
            _validate_json_tree(item)
        return
    raise ModelError("CONFIG_INVALID", "Only JSON-compatible values are permitted")


def parse_json(text: str) -> object:
    """Parse strict JSON without duplicate keys or nonfinite numbers."""
    try:
        value: object = json.loads(
            text, object_pairs_hook=_json_pairs, parse_constant=_nonfinite_constant
        )
        _validate_json_tree(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ModelError("CONFIG_INVALID", "Invalid UTF-8 JSON document") from error


class _ConfigLoader(yaml.SafeLoader):
    """Safe YAML with JSON scalar semantics and strict mapping construction."""


# Copy before changing resolvers so unrelated PyYAML users remain unaffected.
_ConfigLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern)
        for tag, pattern in rules
        if tag
        not in {
            "tag:yaml.org,2002:bool",
            "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:float",
            "tag:yaml.org,2002:timestamp",
        }
    ]
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^-?(?:0|[1-9][0-9]*)$"), list("-0123456789")
)
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+(?:[eE][+-]?[0-9]+)?|[eE][+-]?[0-9]+)$"),
    list("-0123456789"),
)


def _mapping(loader: _ConfigLoader, node: Node) -> dict[str, object]:
    if not isinstance(node, MappingNode):
        raise ModelError("CONFIG_INVALID", "Expected a mapping")
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key: object = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ModelError("CONFIG_INVALID", "Object keys must be strings")
        if key in result:
            raise ModelError("CONFIG_DUPLICATE_KEY", "Duplicate object key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_ConfigLoader.add_constructor("tag:yaml.org,2002:map", _mapping)


def _parse_yaml(text: str) -> object:
    try:
        for token in yaml.scan(text):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise ModelError("CONFIG_INVALID", "YAML tags, anchors and aliases are forbidden")
        loader = _ConfigLoader(text)
        try:
            value: object = loader.get_single_data()
        finally:
            loader.dispose()
        _validate_json_tree(value)
        return value
    except (yaml.YAMLError, ValueError, UnicodeError, RecursionError) as error:
        raise ModelError("CONFIG_INVALID", "Invalid UTF-8 YAML document") from error


def read_document(path: Path, max_bytes: int = MAX_CONFIG_BYTES) -> object:
    """Read a bounded local config; never interpolate paths or the environment."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    location = ErrorLocation(kind="input-path", value=str(path))
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ModelError("CONFIG_INVALID", "Input must be a regular file", location=location)
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ModelError("CONFIG_INVALID", "Input changed while opening", location=location)
            content = stream.read(max_bytes + 1)
    except FileNotFoundError as error:
        raise ModelError(
            "INPUT_NOT_FOUND", "Input file does not exist", location=location
        ) from error
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot read input file", location=location) from error
    if len(content) > max_bytes:
        raise ModelError("CONFIG_TOO_LARGE", "Input exceeds the size limit", location=location)
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ModelError("CONFIG_INVALID", "Input must be UTF-8", location=location) from error
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
        return parse_json(text)
    return _parse_yaml(text)


def load_config[T: BaseModel](path: Path, model: type[T]) -> T:
    """Validate a closed configuration and apply its declared defaults."""
    value = read_document(path)
    if isinstance(value, dict) and "schemaVersion" in value:
        version = value["schemaVersion"]
        if type(version) is not int or version != 1:
            raise ModelError("CONFIG_UNSUPPORTED_VERSION", "Unsupported configuration version")
    try:
        return model.model_validate(value, strict=True)
    except ValidationError as error:
        # Do not include Pydantic's input values or validation messages containing data.
        first = error.errors(include_input=False, include_context=False, include_url=False)[0]
        parts = first["loc"]
        pointer = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)
        raise ModelError(
            "CONFIG_INVALID",
            "Configuration does not match its schema",
            location=ErrorLocation(kind="config-pointer", value=pointer),
        ) from error

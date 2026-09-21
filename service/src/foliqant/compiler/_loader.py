"""Strict local YAML/Markdown loading for compiler-owned configuration."""

from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken

from foliqant.core.plan import SourceLocation

from .errors import CompilationError


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueSafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise ConstructorError(
                None, None, "invalid mapping key", key_node.start_mark
            ) from error
        if duplicate:
            raise ConstructorError(None, None, "duplicate mapping key", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_yaml(text: str, *, relative_path: str, line_offset: int = 0) -> object:
    """Load safe YAML with unique keys and convert parser details to safe locations."""

    try:
        if any(isinstance(token, AliasToken) for token in yaml.scan(text)):
            raise ConstructorError(None, None, "YAML aliases are not supported", None)
        return yaml.load(text, Loader=_UniqueSafeLoader)  # noqa: S506 - custom SafeLoader subclass
    except (yaml.YAMLError, RecursionError) as error:
        mark = getattr(error, "problem_mark", None)
        line = (mark.line + 1 + line_offset) if mark is not None else 1
        column = (mark.column + 1) if mark is not None else 1
        raise CompilationError(
            "invalid_yaml", SourceLocation(relative_path, line, column)
        ) from None


def load_step(path: Path, *, bundle: Path) -> tuple[object, str | None, SourceLocation, bytes]:
    relative = path.relative_to(bundle).as_posix()
    try:
        source = path.read_bytes()
        text = source.decode("utf-8")
    except (OSError, UnicodeError):
        raise CompilationError("invalid_step_file", SourceLocation(relative, 1, 1)) from None
    if path.suffix == ".md":
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise CompilationError("missing_frontmatter", SourceLocation(relative, 1, 1))
        try:
            end = next(
                index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
            )
        except StopIteration:
            raise CompilationError("missing_frontmatter", SourceLocation(relative, 1, 1)) from None
        data = load_yaml("\n".join(lines[1:end]), relative_path=relative, line_offset=1)
        body = "\n".join(lines[end + 1 :]).strip() or None
        return data, body, SourceLocation(relative, 2, 1), source
    return load_yaml(text, relative_path=relative), None, SourceLocation(relative, 1, 1), source

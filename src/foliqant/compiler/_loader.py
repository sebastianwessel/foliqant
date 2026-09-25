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
        # The workflow policy field is a literal mapping key. PyYAML's YAML
        # 1.1 resolver otherwise reads unquoted `on` as boolean True.
        key = (
            "on"
            if key_node.tag == "tag:yaml.org,2002:bool" and key_node.value == "on"
            else loader.construct_object(key_node, deep=deep)
        )
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


def load_step(
    path: Path, *, bundle: Path, source: bytes | None = None
) -> tuple[object, str | None, SourceLocation, bytes]:
    relative = path.relative_to(bundle).as_posix()
    try:
        source = path.read_bytes() if source is None else source
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


class YamlLocator:
    """Map authored key paths to 1-based source coordinates for diagnostics.

    The text was already accepted by :func:`load_yaml`; only node marks are read.
    A path that does not fully exist resolves to its deepest existing key.
    """

    def __init__(self, text: str, *, relative_path: str, line_offset: int = 0) -> None:
        self._path = relative_path
        self._offset = line_offset
        try:
            self._root: yaml.Node | None = yaml.compose(text, Loader=_UniqueSafeLoader)
        except (yaml.YAMLError, RecursionError):
            self._root = None

    def locate(self, *path: str | int) -> SourceLocation:
        node = self._root
        mark = node.start_mark if node is not None else None
        for token in path:
            if isinstance(node, MappingNode):
                match = next(
                    (
                        (key, value)
                        for key, value in node.value
                        if isinstance(key, yaml.ScalarNode) and key.value == str(token)
                    ),
                    None,
                )
                if match is None:
                    break
                mark, node = match[0].start_mark, match[1]
            elif isinstance(node, yaml.SequenceNode) and isinstance(token, int):
                if not 0 <= token < len(node.value):
                    break
                node = node.value[token]
                mark = node.start_mark
            else:
                break
        if mark is None:
            return SourceLocation(self._path, 1 + self._offset, 1)
        return SourceLocation(self._path, mark.line + 1 + self._offset, mark.column + 1)

    def follow(
        self, loc: tuple[str | int, ...], tags: frozenset[str]
    ) -> tuple[tuple[str | int, ...], SourceLocation, str | int | None]:
        """Map a validation error path to the authored key path and its coordinate.

        Union tags and class names that are not authored keys are skipped. A
        tag equal to a key (``route`` before ``route``) is recognized by its
        successor also being a key of the same mapping. Returns the matched
        path, the location of its deepest node and the unmatched final token.
        """
        node = self._root
        mark = node.start_mark if node is not None else None
        kept: list[str | int] = []
        matched_last = False
        for index, token in enumerate(loc):
            matched_last = False
            following = loc[index + 1] if index + 1 < len(loc) else None
            if isinstance(node, MappingNode) and isinstance(token, str):
                keys = {
                    key.value: (key, value)
                    for key, value in node.value
                    if isinstance(key, yaml.ScalarNode)
                }
                match = keys.get(token)
                child = match[1] if match is not None else None
                is_tag = token in tags and (
                    (isinstance(following, str) and following in keys)
                    or (
                        isinstance(following, str)
                        and not (
                            isinstance(child, MappingNode)
                            and any(
                                isinstance(key, yaml.ScalarNode) and key.value == following
                                for key, _ in child.value
                            )
                        )
                    )
                )
                if match is None or is_tag:
                    continue
                mark, node = match[0].start_mark, match[1]
                kept.append(token)
                matched_last = True
            elif isinstance(node, yaml.SequenceNode) and isinstance(token, int):
                if not 0 <= token < len(node.value):
                    continue
                node = node.value[token]
                mark = node.start_mark
                kept.append(token)
                matched_last = True
        final = loc[-1] if loc and not matched_last else None
        if mark is None:
            return tuple(kept), SourceLocation(self._path, 1 + self._offset, 1), final
        location = SourceLocation(self._path, mark.line + 1 + self._offset, mark.column + 1)
        return tuple(kept), location, final


def step_locator(path: Path, *, bundle: Path, source: bytes) -> YamlLocator:
    """Locator of a step file; for Markdown, of its frontmatter with the line offset."""
    relative = path.relative_to(bundle).as_posix()
    try:
        text = source.decode("utf-8")
    except UnicodeError:
        return YamlLocator("", relative_path=relative)
    if path.suffix != ".md":
        return YamlLocator(text, relative_path=relative)
    lines = text.splitlines()
    end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        len(lines),
    )
    return YamlLocator("\n".join(lines[1:end]), relative_path=relative, line_offset=1)

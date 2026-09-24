"""Collect non-fatal compiler findings with stable codes and source coordinates."""

from dataclasses import dataclass, field
from typing import Literal

from foliqant.core.plan import Diagnostic, SourceLocation

_MAX_MESSAGE = 480

WARNING_CODES = frozenset(
    {
        "uncovered_value",
        "condition_always_false",
        "condition_always_true",
        "route_unreachable_entry",
        "repeat_without_retry",
        "unused_llm_input",
        "collection_budget",
    }
)
INFO_CODES = frozenset({"case_on_unknown_type", "review_ends_run", "empty_text_source"})


def safe_text(value: object, limit: int = 64) -> str:
    """Render an authored identifier or JSON value compactly and bounded."""
    import json

    try:
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        text = "?"
    text = "".join(character if character.isprintable() else "?" for character in text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(slots=True)
class Diagnostics:
    """Ordered, de-duplicated diagnostics for one compilation."""

    items: list[Diagnostic] = field(default_factory=list)

    def add(
        self,
        code: str,
        location: SourceLocation,
        message: str,
        *,
        field_path: str | None = None,
    ) -> None:
        level: Literal["warning", "info"]
        if code in WARNING_CODES:
            level = "warning"
        elif code in INFO_CODES:
            level = "info"
        else:  # pragma: no cover - programming error guarded by tests
            raise ValueError("unknown diagnostic code")
        text = message if len(message) <= _MAX_MESSAGE else message[: _MAX_MESSAGE - 3] + "..."
        item = Diagnostic(code, level, location, text, field_path)
        if item not in self.items:
            self.items.append(item)

    def result(self) -> tuple[Diagnostic, ...]:
        return tuple(self.items)

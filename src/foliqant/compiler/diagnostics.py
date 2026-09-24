"""Collect non-fatal compiler findings with stable codes and source coordinates."""

from dataclasses import dataclass, field
from typing import Literal

from foliqant.core.plan import Diagnostic, SourceLocation

from .errors import hint_for

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
        "run_budget",
        "review_ends_run",
    }
)
INFO_CODES = frozenset({"case_on_unknown_type", "review_ends_run", "empty_text_source"})
"""Codes in both sets choose their level per finding (``review_ends_run``)."""


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
        level: Literal["warning", "info"] | None = None,
    ) -> None:
        allowed = {
            *(("warning",) if code in WARNING_CODES else ()),
            *(("info",) if code in INFO_CODES else ()),
        }
        if level is None and len(allowed) == 1:
            level = "warning" if "warning" in allowed else "info"
        if level is None or level not in allowed:  # pragma: no cover - programming error
            raise ValueError("unknown diagnostic code or level")
        text = message if len(message) <= _MAX_MESSAGE else message[: _MAX_MESSAGE - 3] + "..."
        item = Diagnostic(code, level, location, text, field_path, hint_for(code))
        if item not in self.items:
            self.items.append(item)

    def result(self) -> tuple[Diagnostic, ...]:
        return tuple(self.items)

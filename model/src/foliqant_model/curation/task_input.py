"""Keep task contracts immutable while a generator rewrites one text field."""

from __future__ import annotations

import json
import re
from collections import Counter

from ..contracts.inputs import DataRecord
from ..errors import ModelError
from ..scoring import parse_strict_json

_FIELDS = {"banking77": "request", "wanli": "claim", "tatqa": "question"}
_TASKS = {
    "banking77": "intent-classification",
    "wanli": "evidence-assessment",
    "tatqa": "financial-question-answering",
    "foliqant-scenarios": "evidence-based-decision",
}
_TARGET_FIELDS = {
    "banking77": ("intent",),
    "foliqant-scenarios": ("decision", "reason"),
    "wanli": ("label",),
}
_GUARD_VERSION = "scoped-task-input-guards-v2"
_MIN_CONTEXT_COPY_CHARACTERS = 24
_ROLE = re.compile(r"(?im)^\s*(?:\[/?(?:system|user|assistant)\]|(?:system|user|assistant)\s*:)")
_RULE = re.compile(r"(?i)\b(?:maps? to decision|apply this (?:complete )?rule|operation:|purpose:)")
_CURRENCY = re.compile(
    r"(?<![A-Za-z])(?:AUD|BGN|BRL|CAD|CHF|CNY|CZK|DKK|EUR|GBP|HKD|HUF|INR|JPY|KRW|"
    r"MXN|NOK|NZD|PLN|RON|SEK|SGD|TRY|USD|ZAR)(?![A-Za-z])|[€$£¥₹₩]",
    re.IGNORECASE,
)


def task_input_recipe() -> dict[str, object]:
    """Describe the source mappings and versioned guards bound into generation identity."""

    return {
        "editableFields": dict(sorted(_FIELDS.items())),
        "guards": {
            "contextCopyMinCharacters": _MIN_CONTEXT_COPY_CHARACTERS,
            "currencyPattern": _CURRENCY.pattern,
            "rolePattern": _ROLE.pattern,
            "rulePattern": _RULE.pattern,
        },
        "guardVersion": _GUARD_VERSION,
        "targetFields": {source: list(fields) for source, fields in sorted(_TARGET_FIELDS.items())},
        "tasks": dict(sorted(_TASKS.items())),
    }


def task_name(parent: DataRecord) -> str:
    """Return an answer-independent task identifier, never a scenario label."""
    return _TASKS.get(parent.sourceId, "text-task")


def _payload(parent: DataRecord) -> dict[str, object]:
    parsed = parse_strict_json(parent.messages[-2].content)
    field = _FIELDS[parent.sourceId]
    if not parsed.valid or not isinstance(parsed.value, dict):
        raise ModelError("ARGUMENT_INVALID", "Source task input must be a JSON object")
    if not isinstance(parsed.value.get(field), str) or not parsed.value[field]:
        raise ModelError("ARGUMENT_INVALID", "Source task input lacks its editable text field")
    return dict(parsed.value)


def editable_input(parent: DataRecord) -> str:
    """Select just the request, claim, question, or final plain user text."""
    field = _FIELDS.get(parent.sourceId)
    if field is None:
        return parent.messages[-2].content
    value = _payload(parent)[field]
    assert isinstance(value, str)
    return value


def assemble_input(parent: DataRecord, candidate: str) -> str:
    """Replace editable text while retaining all source metadata verbatim in value."""
    field = _FIELDS.get(parent.sourceId)
    if field is None:
        return candidate
    payload = _payload(parent)
    payload[field] = candidate
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _nested_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _nested_strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _nested_strings(item)]
    return []


def _immutable_context(parent: DataRecord) -> list[str]:
    values = [message.content for message in parent.messages[:-2]]
    field = _FIELDS.get(parent.sourceId)
    if field is not None:
        payload = _payload(parent)
        values.extend(
            text
            for key, value in payload.items()
            if key != field
            for text in _nested_strings(value)
        )
    return values


def _target_labels(parent: DataRecord) -> list[str]:
    fields = _TARGET_FIELDS.get(parent.sourceId, ())
    if not fields:
        return []
    parsed = parse_strict_json(parent.messages[-1].content)
    if not parsed.valid or not isinstance(parsed.value, dict):
        return []
    return [value for field in fields if isinstance((value := parsed.value.get(field)), str)]


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text, re.IGNORECASE) is not None


def _contains_explicit_target_cue(candidate: str, label: str) -> bool:
    if "_" in label or "-" in label:
        return _contains_token(candidate, label)
    cue = re.compile(
        rf"(?i)(?:[\"']?(?:answer|label)[\"']?\s*(?::|=|\bis\b)\s*[\"']?)"
        rf"{re.escape(label)}(?![\w-])"
    )
    return cue.search(candidate) is not None


def _currencies(value: str) -> Counter[str]:
    return Counter(
        match.group(0).upper() if match.group(0).isalpha() else match.group(0)
        for match in _CURRENCY.finditer(value)
    )


def candidate_structure_problem(parent: DataRecord, candidate: str) -> str | None:
    """Reject generated control wrappers before they enter training content."""
    original = editable_input(parent)
    if candidate.lstrip().startswith(("{", "[", "```")) and not original.lstrip().startswith(
        ("{", "[", "```")
    ):
        return "candidate-envelope-added"
    for pattern in (_ROLE, _RULE):
        if pattern.search(candidate) and not pattern.search(original):
            return "candidate-instructions-added"
    for message in parent.messages[:-2]:
        if (
            message.role == "system"
            and message.content in candidate
            and message.content not in original
        ):
            return "candidate-instructions-added"
    if _currencies(original) != _currencies(candidate):
        return "candidate-currencies-changed"
    for label in _target_labels(parent):
        if not _contains_token(original, label) and _contains_explicit_target_cue(candidate, label):
            return "candidate-answer-cue-added"
    normalized_original = _normalized_text(original)
    normalized_candidate = _normalized_text(candidate)
    for value in _immutable_context(parent):
        normalized_value = _normalized_text(value)
        if (
            len(normalized_value) >= _MIN_CONTEXT_COPY_CHARACTERS
            and normalized_value not in normalized_original
            and normalized_value in normalized_candidate
        ):
            return "candidate-context-copied"
    return None

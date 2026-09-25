"""Translate one provider response's explicitly reported token usage."""

from collections.abc import Mapping
from typing import cast

from pydantic_ai.usage import RequestUsage

from foliqant.core.execution import TokenUsage


def _reported(data: Mapping[str, object], name: str) -> int | None:
    if name not in data:
        return None
    return cast(int, data[name])


def _fallback_reported(details: Mapping[str, object], name: str) -> int | None:
    """A ``details`` count is trusted as a fallback only when it is nonzero.

    OpenAI's Responses adapter proves a zero in ``details`` isn't presence evidence: it writes
    ``details["reasoning_tokens"] = 0`` both for a genuinely reported zero (a case the declared
    field already resolves, since `RequestUsage.extract`'s provider mapping would find that same
    real zero) and when the measurement was entirely omitted. A zero found only in ``details``
    can't be told apart from that synthesized placeholder, so it is treated the same as absent;
    a nonzero value cannot come from that synthesis and is trusted.
    """
    value = _reported(details, name)
    return value if value else None


def _first_reported(
    reported: Mapping[str, object], details: Mapping[str, object], name: str
) -> int | None:
    value = _reported(reported, name)
    if value is not None:
        return value
    return _fallback_reported(details, _DETAILS_KEYS[name])


_DETAILS_KEYS = {
    "cache_read_tokens": "cached_tokens",
    "output_reasoning_tokens": "reasoning_tokens",
}


def request_token_usage(usage: RequestUsage) -> TokenUsage:
    """Keep unreported counters absent while preserving explicitly reported zeroes.

    ``RequestUsage.__init__`` only ever ``setattr``s the keys it is given, so a name absent from
    ``vars(usage)`` was never reported, even for ``cache_read_tokens``/``cache_write_tokens``,
    which the dataclass declares with a default of ``0``: an unset instance never gains that
    default as an instance attribute (only as a class attribute reachable through normal
    attribute lookup), so it stays out of ``vars()``. ``input_tokens``/``output_tokens``/
    ``cache_read_tokens``/``cache_write_tokens`` are reliably populated this way when
    ``RequestUsage.extract`` recognizes the provider (genai-prices' extractor maps the
    provider's raw usage onto these names before they reach ``RequestUsage.__init__``).

    pydantic-ai 2.46.0 declares no such field for reasoning tokens; ``extract`` only sets
    ``output_reasoning_tokens`` as an instance attribute when a recognized provider's extractor
    maps it (e.g. Anthropic's ``output_tokens_details.thinking_tokens``, OpenAI's
    ``completion_tokens_details.reasoning_tokens`` for the chat flavor, or
    ``output_tokens_details.reasoning_tokens`` for the Responses flavor). When extraction
    doesn't map a count — an unrecognized provider, or a mapping genai-prices lacks — fall back
    to the ``details`` key the adapter itself set from the raw response: OpenAI's adapter
    mirrors the same reasoning count into ``details["reasoning_tokens"]``, and a provider that
    reports a top-level cached count puts it in ``details["cached_tokens"]``. The fallback is
    only consulted when the field was not reported, and (per ``_fallback_reported``) only trusts
    a nonzero ``details`` value, so it never turns a genuinely unmeasured count into a false
    zero.
    """

    reported = cast(dict[str, object], vars(usage))
    details = cast(Mapping[str, object], usage.details)
    return TokenUsage(
        input_tokens=_reported(reported, "input_tokens"),
        output_tokens=_reported(reported, "output_tokens"),
        cache_read_input_tokens=_first_reported(reported, details, "cache_read_tokens"),
        cache_write_input_tokens=_reported(reported, "cache_write_tokens"),
        reasoning_output_tokens=_first_reported(reported, details, "output_reasoning_tokens"),
    )

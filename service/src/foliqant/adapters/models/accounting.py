"""Translate one provider response's explicitly reported token usage."""

from collections.abc import Mapping
from typing import cast

from pydantic_ai.usage import RequestUsage

from foliqant.core.execution import TokenUsage


def _reported(data: Mapping[str, object], name: str) -> int | None:
    if name not in data:
        return None
    return cast(int, data[name])


def request_token_usage(usage: RequestUsage) -> TokenUsage:
    """Keep unreported counters absent while preserving explicitly reported zeroes."""

    reported = cast(dict[str, object], vars(usage))
    # The SDK normalizes actually reported reasoning tokens under this name.
    # Its Responses adapter may synthesize details.reasoning_tokens=0 when
    # the provider omitted the measurement; details is not presence evidence.
    reasoning = _reported(reported, "output_reasoning_tokens")
    return TokenUsage(
        input_tokens=_reported(reported, "input_tokens"),
        output_tokens=_reported(reported, "output_tokens"),
        cache_read_input_tokens=_reported(reported, "cache_read_tokens"),
        cache_write_input_tokens=_reported(reported, "cache_write_tokens"),
        reasoning_output_tokens=reasoning,
    )

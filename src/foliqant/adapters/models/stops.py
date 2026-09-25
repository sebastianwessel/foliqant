"""Classify a provider's stop reason without reading any generated content."""

from pydantic_ai.messages import ModelResponse

from foliqant.core.errors import ErrorCode
from foliqant.core.execution import TokenUsage


def stop_error(response: ModelResponse) -> ErrorCode | None:
    """The canonical failure of a response the provider stopped before completing it.

    A refusal or content filter is ``output_refused``, an output token limit is
    ``output_limit_reached`` and a provider-side generation error is
    ``dependency_failure``. None of them is a completed transient response, so
    none authorizes a retry: with unchanged input and options, a length stop is
    expected to recur.
    """
    refusal = response.provider_details is not None and (
        response.provider_details.get("refusal") is not None
    )
    if refusal or response.finish_reason == "content_filter":
        return ErrorCode.OUTPUT_REFUSED
    if response.finish_reason == "length":
        return ErrorCode.OUTPUT_LIMIT_REACHED
    if response.finish_reason == "error":
        return ErrorCode.DEPENDENCY_FAILURE
    return None


def reasoning_consumed_budget(response: ModelResponse, usage: TokenUsage) -> bool | None:
    """Whether reported reasoning tokens used the whole output of a length stop.

    ``None`` when the response did not stop at its output limit or the provider
    did not report both counts.
    """
    if response.finish_reason != "length":
        return None
    if usage.output_tokens is None or usage.reasoning_output_tokens is None:
        return None
    return usage.output_tokens > 0 and usage.reasoning_output_tokens >= usage.output_tokens

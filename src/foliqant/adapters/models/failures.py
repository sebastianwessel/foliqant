"""Map provider errors to canonical codes without retaining or logging provider text.

A provider error body is inspected only to choose a code; nothing of it leaves
this module.
"""

from collections.abc import Mapping

from pydantic_ai.exceptions import (
    ContentFilterError,
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
)

from foliqant.adapters.execution.retry import http_failure
from foliqant.core.errors import ErrorCode, ServiceError

#: Inspect only a bounded prefix of provider text.
_MAX_INSPECTED = 4096

#: Provider error codes and types, lower case (OpenAI, Azure OpenAI, compatible servers).
_CONTEXT_CODES = frozenset(
    {"context_length_exceeded", "exceed_context_size_error", "request_too_large"}
)
_REFUSAL_CODES = frozenset({"content_filter", "content_policy_violation", "invalid_prompt"})
_MODEL_CODES = frozenset({"model_not_found", "deploymentnotfound", "not_found_error"})

#: Phrases of context window errors (Anthropic, vLLM, llama.cpp, Ollama, Mistral, Gemini).
_CONTEXT_PHRASES = (
    "context length",
    "context window",
    "context size",
    "context limit",
    "maximum context",
    "prompt is too long",
    "input is too long",
    "too many input tokens",
    "too many tokens",
    "input token count",
    "reduce the length of the messages",
)


def _texts(body: object) -> tuple[set[str], str]:
    """Lower-case error codes/types and the bounded message text of a provider body."""
    codes: set[str] = set()
    messages: list[str] = []
    pending: list[object] = [body]
    for _ in range(4):
        if not pending:
            break
        current, pending = pending, []
        for item in current:
            if isinstance(item, str):
                messages.append(item[:_MAX_INSPECTED])
            elif isinstance(item, Mapping):
                for key in ("code", "type"):
                    value = item.get(key)
                    if isinstance(value, str) and len(value) <= 128:
                        codes.add(value.lower())
                for key in ("message", "detail"):
                    value = item.get(key)
                    if isinstance(value, str):
                        messages.append(value[:_MAX_INSPECTED])
                for key in ("error", "innererror"):
                    if key in item:
                        pending.append(item[key])
    return codes, " ".join(messages).lower()


def model_http_failure(error: ModelHTTPError) -> ServiceError:
    """The canonical failure of a provider's HTTP error response.

    Context window and refusal errors are recognized from the provider's error
    code or message; a missing model or deployment is ``model_not_found``; other
    rejected requests are ``request_rejected``. Transient statuses keep their
    retry permission (see ``http_failure``).
    """
    status = error.status_code
    if status in (400, 404, 413, 422):
        codes, text = _texts(error.body)
        if codes & _CONTEXT_CODES or status == 413 or any(p in text for p in _CONTEXT_PHRASES):
            return ServiceError(ErrorCode.CONTEXT_LIMIT_EXCEEDED)
        if codes & _REFUSAL_CODES:
            return ServiceError(ErrorCode.OUTPUT_REFUSED)
        if status == 404 or codes & _MODEL_CODES:
            return ServiceError(ErrorCode.MODEL_NOT_FOUND)
    return http_failure(status, error.headers or {})


def behavior_failure(error: UnexpectedModelBehavior) -> ErrorCode:
    """The code of a response the agent could not use.

    A tool call that fails its schema or names an unknown tool exhausts the
    tool's retries (none are configured): ``invalid_tool_call``. Output cut off
    inside a tool call is ``output_limit_reached``, a content filter
    ``output_refused``; everything else is ``invalid_output``.
    """
    if isinstance(error, IncompleteToolCall):
        return ErrorCode.OUTPUT_LIMIT_REACHED
    if isinstance(error, ContentFilterError):
        return ErrorCode.OUTPUT_REFUSED
    message = error.message
    if message.startswith("Tool ") and "exceeded max retries" in message:
        return ErrorCode.INVALID_TOOL_CALL
    return ErrorCode.INVALID_OUTPUT

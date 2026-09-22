"""Per-attempt HTTP evidence retained before the MCP SDK normalizes errors."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx2

from foliqant.adapters.execution.retry import transient_response
from foliqant.core.retry import TransientFailure


@dataclass(slots=True)
class HttpAttempt:
    active: bool = True
    status: int | None = None
    transient: TransientFailure | None = None


_HTTP_ATTEMPT: ContextVar[HttpAttempt | None] = ContextVar(
    "foliqant_mcp_http_attempt", default=None
)


@contextmanager
def observe_http_attempt() -> Iterator[HttpAttempt]:
    """The SDK forwards sender context; each call owns a distinct mutable record."""
    observation = HttpAttempt()
    token = _HTTP_ATTEMPT.set(observation)
    try:
        yield observation
    finally:
        observation.active = False
        _HTTP_ATTEMPT.reset(token)


async def record_http_response(response: httpx2.Response) -> None:
    """Record only status and numeric delay for this actual tools/call POST.

    No body, URL, credentials, identity or request ID survives this hook. Never
    infer retry permission from a peer-controlled JSON-RPC error or message.
    """
    observation = _HTTP_ATTEMPT.get()
    if observation is None or not observation.active or response.request.method != "POST":
        return
    try:
        request = json.loads(response.request.content)
    except (ValueError, httpx2.RequestNotRead):
        return
    if not isinstance(request, dict) or request.get("method") != "tools/call":
        return
    observation.status = response.status_code
    observation.transient = transient_response(response.status_code, response.headers)

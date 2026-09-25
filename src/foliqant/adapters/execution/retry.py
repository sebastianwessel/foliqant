"""Classify completed HTTP responses without retaining response bodies or headers."""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.retry import TransientFailure

#: The retry table: completed responses that may succeed when repeated, and the code
#: each reports. Only these authorize a configured retry (``retry.max_attempts``).
#:
#: ======  =========================  ============================================
#: Status  Code                       Why a repeat can succeed
#: ======  =========================  ============================================
#: 408     ``request_timeout``        the server timed out waiting; nothing done
#: 429     ``rate_limited``           quota recovers (a valid Retry-After is honoured)
#: 500     ``dependency_failure``     transient server error
#: 502     ``dependency_failure``     gateway could not reach the server
#: 503     ``dependency_overloaded``  temporary overload
#: 504     ``request_timeout``        gateway timeout of a stalled upstream
#: 529     ``dependency_overloaded``  provider overload (Anthropic)
#: ======  =========================  ============================================
#:
#: Every other status is deterministic for the same request and never retried:
#: 400/422 ``request_rejected`` or ``context_limit_exceeded``, 401 ``unauthenticated``,
#: 403 ``forbidden``, 404 ``model_not_found`` / ``dependency_failure``.
TRANSIENT_HTTP_STATUSES: Mapping[int, ErrorCode] = {
    408: ErrorCode.REQUEST_TIMEOUT,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.DEPENDENCY_FAILURE,
    502: ErrorCode.DEPENDENCY_FAILURE,
    503: ErrorCode.DEPENDENCY_OVERLOADED,
    504: ErrorCode.REQUEST_TIMEOUT,
    529: ErrorCode.DEPENDENCY_OVERLOADED,
}

_TERMINAL_HTTP_STATUSES: Mapping[int, ErrorCode] = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
}


def transient_response(status: int, headers: Mapping[str, str]) -> TransientFailure | None:
    """Only the completed transient statuses of the retry table authorize another attempt.

    Authentication and malformed output never acquire retry permission here.
    Invalid Retry-After values fail closed.
    """
    code = TRANSIENT_HTTP_STATUSES.get(status)
    if code is None:
        return None
    value = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    delay = None
    if value is not None:
        if len(value) > 128:
            return None
        try:
            delay = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    return None
                delay = max(0.0, (date - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(delay) or delay < 0:
            return None
    return TransientFailure(code, retry_after_seconds=delay)


def http_failure(status: int, headers: Mapping[str, str]) -> ServiceError:
    """The canonical failure of one completed HTTP error response (status >= 400).

    A transient status keeps its precise code even when an invalid Retry-After
    removes the retry permission. Other client errors are ``request_rejected``;
    other server errors ``dependency_failure`` without retry permission. A 404
    is a ``dependency_failure`` here; a model adapter reports ``model_not_found``.
    """
    terminal = _TERMINAL_HTTP_STATUSES.get(status)
    if terminal is not None:
        return ServiceError(terminal)
    transient = transient_response(status, headers)
    if transient is not None:
        return transient
    code = TRANSIENT_HTTP_STATUSES.get(status)
    if code is not None:
        return ServiceError(code)
    if 400 <= status < 500 and status != 404:
        return ServiceError(ErrorCode.REQUEST_REJECTED)
    return ServiceError(ErrorCode.DEPENDENCY_FAILURE)

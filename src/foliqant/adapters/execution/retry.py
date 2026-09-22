"""Classify completed HTTP responses without retaining response bodies or headers."""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from foliqant.core.retry import TransientFailure

TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 529})


def transient_response(status: int, headers: Mapping[str, str]) -> TransientFailure | None:
    """Only explicit completed transient statuses authorize another attempt.

    Timeouts, ambiguous transport failures, authentication and malformed output
    never acquire retry permission here. Invalid Retry-After values fail closed.
    """
    if status not in TRANSIENT_HTTP_STATUSES:
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
    return TransientFailure(retry_after_seconds=delay)

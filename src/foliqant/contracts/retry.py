"""Shared optional retry policy for model and read-only MCP providers."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from foliqant.core.retry import RetryPolicy

from .base import BoundaryModel


class RetryConfig(BoundaryModel):
    """Repeat transient provider failures; ``max_attempts`` includes the first request.

    The default of four attempts is the first request plus up to three retries,
    with capped exponential full-jitter backoff that honours a valid Retry-After
    up to ``max_delay_seconds`` and never outlasts the request's deadline.
    """

    max_attempts: Annotated[int, Field(strict=True, ge=1, le=8)] = 4
    initial_delay_seconds: Annotated[float, Field(ge=0, le=60, allow_inf_nan=False)] = 1.0
    max_delay_seconds: Annotated[float, Field(ge=0, le=300, allow_inf_nan=False)] = 30.0

    @model_validator(mode="after")
    def ordered_delays(self) -> Self:
        self.policy()
        return self

    def policy(self) -> RetryPolicy:
        """Detach configuration into its immutable standard-library runtime policy."""
        return RetryPolicy(self.max_attempts, self.initial_delay_seconds, self.max_delay_seconds)

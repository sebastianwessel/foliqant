"""Configured per-request cost estimation with exact decimal arithmetic."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .execution import Cost, TokenUsage

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class PriceTier:
    """Prices per one million tokens; an absent cached price bills cached input as input."""

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        for price in (self.input_per_million, self.output_per_million):
            if type(price) is not Decimal or not price.is_finite() or price < 0:
                raise ValueError("prices must be finite nonnegative decimals")
        cached = self.cached_input_per_million
        if cached is not None and (
            type(cached) is not Decimal or not cached.is_finite() or cached < 0
        ):
            raise ValueError("prices must be finite nonnegative decimals")


@dataclass(frozen=True, slots=True)
class PricingPlan:
    """A model profile's configured prices; an estimate, never a provider invoice.

    ``long_context`` replaces ``base`` for every token of a request whose input
    tokens exceed ``long_context_threshold``. Reported output tokens include
    reasoning tokens; ``reasoning_billed_as: input`` bills that subset at the
    input price instead.
    """

    currency: str
    base: PriceTier
    reasoning_billed_as: Literal["output", "input"] = "output"
    long_context_threshold: int | None = None
    long_context: PriceTier | None = None
    reference_model: str | None = None

    def __post_init__(self) -> None:
        if (self.long_context is None) != (self.long_context_threshold is None):
            raise ValueError("a long-context tier requires a threshold")
        threshold = self.long_context_threshold
        if threshold is not None and (type(threshold) is not int or threshold < 1):
            raise ValueError("invalid long-context threshold")
        if self.reasoning_billed_as not in ("output", "input"):
            raise ValueError("invalid reasoning billing")

    def tier(self, input_tokens: int) -> PriceTier:
        """Select the tier that applies to one request's input token count."""
        if (
            self.long_context is not None
            and self.long_context_threshold is not None
            and input_tokens > self.long_context_threshold
        ):
            return self.long_context
        return self.base

    def request_cost(self, tokens: TokenUsage | None) -> Cost:
        """Estimate one request; any count the formula needs but lacks yields no amount."""
        return Cost(self.currency, self._amount(tokens), self.reference_model)

    def _amount(self, tokens: TokenUsage | None) -> Decimal | None:
        if tokens is None or tokens.input_tokens is None or tokens.output_tokens is None:
            return None
        tier = self.tier(tokens.input_tokens)
        cached = 0
        if tier.cached_input_per_million is not None:
            if tokens.cache_read_input_tokens is None:
                return None
            cached = tokens.cache_read_input_tokens
        reasoning = 0
        if self.reasoning_billed_as == "input":
            if tokens.reasoning_output_tokens is None:
                return None
            reasoning = tokens.reasoning_output_tokens
        cached_price = (
            tier.cached_input_per_million
            if tier.cached_input_per_million is not None
            else tier.input_per_million
        )
        total = (
            (tokens.input_tokens - cached + reasoning) * tier.input_per_million
            + cached * cached_price
            + (tokens.output_tokens - reasoning) * tier.output_per_million
        )
        return total / _MILLION

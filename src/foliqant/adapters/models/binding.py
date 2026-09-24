"""Immutable runtime binding for one configured PydanticAI model alias."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from foliqant.core.admission import CapacityLimiter
from foliqant.core.pricing import PricingPlan
from foliqant.core.retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class ModelBinding:
    """A prevalidated model and its local execution policy.

    Usage is accounted under ``model.model_name``, the provider model ID;
    ``pricing`` comes from the model profile and estimates each request's cost.
    """

    model: Model
    settings: ModelSettings
    admission: CapacityLimiter
    output_mode: Literal["native", "tool"]
    supports_text: bool = True
    supports_json_schema: bool = True
    supports_tools: bool = True
    # Populated by the provider factory without importing optional SDKs here.
    timeout_errors: tuple[type[Exception], ...] = ()
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    pricing: PricingPlan | None = None

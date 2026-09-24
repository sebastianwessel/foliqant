"""Explicit deployment model profiles with protected credential values."""

import math
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    SecretStr,
    ValidationInfo,
    WithJsonSchema,
    model_validator,
)

from foliqant.core.json import JsonValue
from foliqant.core.pricing import PriceTier, PricingPlan

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .environment import (
    ENVIRONMENT_FIELD,
    EnvironmentCredential,
    EnvironmentText,
    is_environment_reference,
)
from .identifiers import Id
from .retry import RetryConfig

Duration = Annotated[float, Field(gt=0, le=3600)]
_MAX_PRICE = Decimal(1_000_000)


def _price(value: object) -> Decimal:
    """Read a YAML/JSON number exactly as written (``0.2`` is ``Decimal("0.2")``)."""
    if isinstance(value, Decimal):
        price = value
    elif type(value) is int:
        price = Decimal(value)
    elif type(value) is float and math.isfinite(value):
        price = Decimal(repr(value))
    else:
        raise ValueError("price must be a number")
    try:
        if not price.is_finite() or not 0 <= price <= _MAX_PRICE:
            raise ValueError("price must be between 0 and 1,000,000")
    except InvalidOperation:
        raise ValueError("price must be a number") from None
    return price


Price = Annotated[
    Decimal,
    BeforeValidator(_price),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1_000_000}),
]
"""A price per one million tokens in the pricing currency."""


class LongContextPricing(BoundaryModel):
    """Prices for every token of a request whose input exceeds the threshold."""

    model_config = ConfigDict(frozen=True)

    threshold_input_tokens: Annotated[int, Field(strict=True, ge=1, le=100_000_000)]
    input_per_million: Price
    cached_input_per_million: Price | None = None
    output_per_million: Price


class ModelPricing(BoundaryModel):
    """Configured prices used to estimate cost; an estimate, not a provider invoice.

    Example (GPT-5.6 Terra list prices at the time of writing; prices change)::

        pricing:
          currency: USD
          input_per_million: 2.00
          cached_input_per_million: 0.20
          output_per_million: 12.00
          long_context:
            threshold_input_tokens: 272000
            input_per_million: 4.00
            cached_input_per_million: 0.40
            output_per_million: 18.00
    """

    model_config = ConfigDict(frozen=True)

    currency: Literal["USD"]
    input_per_million: Price
    cached_input_per_million: Price | None = None
    output_per_million: Price
    reasoning_billed_as: Literal["output", "input"] = "output"
    long_context: LongContextPricing | None = None
    reference_model: (
        Annotated[
            str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
        ]
        | None
    ) = None
    """Another model whose prices are used as a reference estimate for this one."""

    def plan(self) -> PricingPlan:
        """The engine's immutable pricing value."""
        long_context = self.long_context
        return PricingPlan(
            currency=self.currency,
            base=PriceTier(
                self.input_per_million, self.output_per_million, self.cached_input_per_million
            ),
            reasoning_billed_as=self.reasoning_billed_as,
            long_context_threshold=(
                long_context.threshold_input_tokens if long_context is not None else None
            ),
            long_context=(
                PriceTier(
                    long_context.input_per_million,
                    long_context.output_per_million,
                    long_context.cached_input_per_million,
                )
                if long_context is not None
                else None
            ),
            reference_model=self.reference_model,
        )

    def summary(self) -> dict[str, JsonValue]:
        """A JSON-ready view with prices as numbers, for offline explanation."""
        return cast(dict[str, JsonValue], self.model_dump(mode="json", exclude_none=True))


class GenerationOptions(BoundaryModel):
    """Common sampling options; absence means use the configured provider default."""

    model_config = ConfigDict(frozen=True)

    max_tokens: Annotated[int, Field(strict=True, ge=1, le=1_048_576)] = 4096
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    top_p: Annotated[float, Field(gt=0, le=1)] | None = None


class OpenAIOptions(GenerationOptions):
    seed: Annotated[int, Field(strict=True)] | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None


class AnthropicOptions(GenerationOptions):
    thinking: Literal["disabled", "adaptive"] | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    thinking_budget: Annotated[int, Field(strict=True, ge=1024)] | None = None

    @model_validator(mode="after")
    def compatible_thinking(self) -> Self:
        if self.effort is not None and self.thinking != "adaptive":
            raise ValueError("explicit effort requires adaptive thinking")
        if self.thinking_budget is not None:
            if self.thinking is not None:
                raise ValueError("select adaptive/disabled thinking or a fixed budget")
            if self.thinking_budget >= self.max_tokens:
                raise ValueError("thinking budget must be below maximum output tokens")
            if self.temperature is not None or self.top_p is not None:
                raise ValueError("thinking and explicit sampling options cannot be combined")
        return self


class _ModelConfig(BoundaryModel):
    model_config = ConfigDict(frozen=True)

    model: Annotated[EnvironmentText, Field(min_length=1, pattern=r"\S")] = Field(
        json_schema_extra=ENVIRONMENT_FIELD
    )
    output_mode: Literal["native", "tool"]
    supports_text: bool = True
    supports_json_schema: bool = True
    supports_tools: bool = True
    concurrency: Annotated[int, Field(strict=True, ge=1, le=1024)] = 4
    queue_limit: Annotated[int, Field(strict=True, ge=0, le=10_000)] = 16
    request_timeout: Duration = 60.0
    retry: RetryConfig = Field(default_factory=RetryConfig)
    pricing: ModelPricing | None = None

    @model_validator(mode="after")
    def at_least_one_output(self) -> Self:
        if not self.supports_text and not self.supports_json_schema:
            raise ValueError("model must support at least one output kind")
        if self.output_mode == "tool" and not self.supports_tools:
            raise ValueError("tool output requires tool support")
        return self


class OpenAIModelConfig(_ModelConfig):
    provider: Literal["openai"]
    api: Literal["chat", "responses"]
    api_key: EnvironmentCredential = Field(
        default_factory=lambda: SecretStr("$OPENAI_API_KEY"), json_schema_extra=ENVIRONMENT_FIELD
    )
    options: OpenAIOptions = Field(default_factory=OpenAIOptions)


class CompatibleModelConfig(_ModelConfig):
    provider: Literal["openai_compatible"]
    api: Literal["chat"] = "chat"
    base_url: EnvironmentText = Field(min_length=1, json_schema_extra=ENVIRONMENT_FIELD)
    api_key: EnvironmentCredential | None = Field(default=None, json_schema_extra=ENVIRONMENT_FIELD)
    allow_insecure_http: bool = False
    # Some compatible servers accept only the max_tokens field.
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    options: OpenAIOptions = Field(default_factory=OpenAIOptions)

    @model_validator(mode="after")
    def valid_endpoint(self, info: ValidationInfo) -> Self:
        if not (
            info.context and info.context.get("resolved_environment")
        ) and is_environment_reference(self.base_url):
            return self
        validate_http_endpoint(self.base_url, allow_insecure_http=self.allow_insecure_http)
        return self


class AzureModelConfig(_ModelConfig):
    provider: Literal["azure_openai"]
    api: Literal["chat", "responses"]
    api_flavor: Literal["versioned", "v1"]
    endpoint: EnvironmentText = Field(min_length=1, json_schema_extra=ENVIRONMENT_FIELD)
    api_version: EnvironmentText | None = Field(
        default=None, min_length=1, pattern=r".*\S.*", json_schema_extra=ENVIRONMENT_FIELD
    )
    api_key: EnvironmentCredential = Field(
        default_factory=lambda: SecretStr("$AZURE_OPENAI_API_KEY"),
        json_schema_extra=ENVIRONMENT_FIELD,
    )
    options: OpenAIOptions = Field(default_factory=OpenAIOptions)

    @model_validator(mode="after")
    def explicit_api_flavor(self, info: ValidationInfo) -> Self:
        if self.api_flavor == "versioned" and self.api_version is None:
            raise ValueError("versioned Azure requires an API version")
        if self.api_flavor == "v1" and self.api_version is not None:
            raise ValueError("Azure v1 cannot specify an API version")
        if not (
            info.context and info.context.get("resolved_environment")
        ) and is_environment_reference(self.endpoint):
            return self
        validate_http_endpoint(self.endpoint, allow_insecure_http=False)
        path = urlsplit(self.endpoint).path.rstrip("/")
        if self.api_flavor == "versioned":
            if self.api_version is None or path:
                raise ValueError("versioned Azure requires a resource root and API version")
        elif self.api_version is not None or not path.endswith("/openai/v1"):
            raise ValueError("Azure v1 requires an /openai/v1 endpoint without an API version")
        return self


class AnthropicModelConfig(_ModelConfig):
    provider: Literal["anthropic"]
    api_key: EnvironmentCredential = Field(
        default_factory=lambda: SecretStr("$ANTHROPIC_API_KEY"), json_schema_extra=ENVIRONMENT_FIELD
    )
    options: AnthropicOptions = Field(default_factory=AnthropicOptions)


class GoogleModelConfig(_ModelConfig):
    """Gemini Developer API profile with an explicit API key."""

    provider: Literal["google"]
    api_key: EnvironmentCredential = Field(
        default_factory=lambda: SecretStr("$GOOGLE_API_KEY"), json_schema_extra=ENVIRONMENT_FIELD
    )
    options: GenerationOptions = Field(default_factory=GenerationOptions)


class BedrockModelConfig(_ModelConfig):
    """Bedrock Converse profile using the host's AWS credential chain."""

    provider: Literal["bedrock"]
    region: Annotated[EnvironmentText, Field(min_length=1, pattern=r"\S")] = Field(
        json_schema_extra=ENVIRONMENT_FIELD
    )
    options: GenerationOptions = Field(default_factory=GenerationOptions)


ModelConfig = Annotated[
    OpenAIModelConfig
    | CompatibleModelConfig
    | AzureModelConfig
    | AnthropicModelConfig
    | GoogleModelConfig
    | BedrockModelConfig,
    Field(discriminator="provider"),
]


class ModelProfiles(BoundaryModel):
    """Named profiles have no reserved alias or implicit endpoint/model discovery."""

    models: Annotated[dict[Id, ModelConfig], Field(min_length=1, max_length=128)]


class ModelOptionOverrides(BoundaryModel):
    """Only supplied values replace profile options; null clears optional settings.

    Provider-specific fields are checked against the selected profile by the
    offline compiler after merging. Omitted values retain the profile setting.
    """

    max_tokens: Annotated[int, Field(strict=True, ge=1, le=1_048_576)] | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    top_p: Annotated[float, Field(gt=0, le=1)] | None = None
    seed: Annotated[int, Field(strict=True)] | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    thinking: Literal["disabled", "adaptive"] | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    thinking_budget: Annotated[int, Field(strict=True, ge=1024)] | None = None


class ModelProfileOverride(BoundaryModel):
    """Reuse a deployment profile and replace its model ID, options or pricing.

    Example: ``{profile: local, options: {max_tokens: 800}}`` retains the
    profile's provider, credentials, capabilities, timeout, and shared admission.
    ``pricing`` replaces the profile's pricing and ``null`` removes it. The
    profile's pricing describes its own model: an override that sets another
    ``model`` does not inherit it.
    """

    profile: Id
    model: Annotated[EnvironmentText, Field(min_length=1, pattern=r"\S")] | None = Field(
        default=None, json_schema_extra=ENVIRONMENT_FIELD
    )
    options: ModelOptionOverrides = Field(default_factory=ModelOptionOverrides)
    pricing: ModelPricing | None = None

    @model_validator(mode="after")
    def explicit_values_are_valid(self) -> Self:
        if "model" in self.model_fields_set and self.model is None:
            raise ValueError("model override must be a nonblank model ID")
        if "max_tokens" in self.options.model_fields_set and self.options.max_tokens is None:
            raise ValueError("maximum output tokens cannot be cleared")
        return self


def _inline_credentials_are_references(config: ModelConfig) -> ModelConfig:
    if isinstance(config, BedrockModelConfig):
        return config
    if config.api_key is not None and not is_environment_reference(
        config.api_key.get_secret_value()
    ):
        raise ValueError("inline model credentials must use an environment reference")
    return config


StepModel = (
    Id
    | ModelProfileOverride
    | Annotated[ModelConfig, AfterValidator(_inline_credentials_are_references)]
)

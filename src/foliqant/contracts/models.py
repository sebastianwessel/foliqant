"""Explicit deployment model profiles with protected credential values."""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, ConfigDict, Field, SecretStr, ValidationInfo, model_validator

from .base import BoundaryModel
from .endpoints import validate_http_endpoint
from .environment import (
    ENVIRONMENT_FIELD,
    EnvironmentCredential,
    EnvironmentText,
    is_environment_reference,
)
from .identifiers import Id

Duration = Annotated[float, Field(gt=0, le=3600)]


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
    # Some compatible servers accept only the legacy max_tokens field.
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


ModelConfig = Annotated[
    OpenAIModelConfig | CompatibleModelConfig | AzureModelConfig | AnthropicModelConfig,
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
    """Reuse a deployment profile and replace its model ID or generation options.

    Example: ``{profile: local, options: {max_tokens: 800}}`` retains the
    profile's provider, credentials, capabilities, timeout, and shared admission.
    """

    profile: Id
    model: Annotated[EnvironmentText, Field(min_length=1, pattern=r"\S")] | None = Field(
        default=None, json_schema_extra=ENVIRONMENT_FIELD
    )
    options: ModelOptionOverrides = Field(default_factory=ModelOptionOverrides)

    @model_validator(mode="after")
    def explicit_values_are_valid(self) -> Self:
        if "model" in self.model_fields_set and self.model is None:
            raise ValueError("model override must be a nonblank model ID")
        if "max_tokens" in self.options.model_fields_set and self.options.max_tokens is None:
            raise ValueError("maximum output tokens cannot be cleared")
        return self


def _inline_credentials_are_references(config: ModelConfig) -> ModelConfig:
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

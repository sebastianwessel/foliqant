"""Explicit, offline-safe construction of configured model provider clients."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import Protocol, cast

from pydantic_ai.models import Model
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.settings import ModelSettings

from foliqant.contracts.models import (
    AnthropicModelConfig,
    AzureModelConfig,
    BedrockModelConfig,
    CompatibleModelConfig,
    ModelConfig,
    ModelProfiles,
    OpenAIModelConfig,
)
from foliqant.core.errors import ErrorCode, ServiceError

from .binding import ModelBinding

_CLEANUP_TIMEOUT_SECONDS = 5.0
_OPENAI_AMBIENT_REQUEST_ENV = frozenset(
    {
        "OPENAI_ADMIN_KEY",
        "OPENAI_CUSTOM_HEADERS",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    }
)
_ANTHROPIC_AMBIENT_REQUEST_ENV = frozenset({"ANTHROPIC_CUSTOM_HEADERS"})


class _AsyncCloseable(Protocol):
    async def close(self) -> None: ...


def _invalid_configuration() -> ServiceError:
    return ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _secret(environment: Mapping[str, str], name: str | None, *, required: bool) -> str:
    if name is None:
        if required:
            raise _invalid_configuration()
        # The OpenAI SDK requires a nonempty value even when an explicitly
        # configured compatible endpoint performs no authentication.
        return "credential-not-required"
    value = environment.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_configuration()
    return value


def _reject_ambient_request_configuration(names: frozenset[str]) -> None:
    # The locked SDKs consult these process variables even when credentials and
    # endpoints are passed explicitly. Mutating os.environ around async startup
    # would race with other tasks, so fail closed instead.
    if any(os.environ.get(name) for name in names):
        raise _invalid_configuration()


def _profile(config: ModelConfig) -> ModelProfile:
    return ModelProfile(
        supports_text_output=config.supports_text,
        supports_json_schema_output=config.supports_json_schema,
        supports_tools=config.supports_tools,
        default_structured_output_mode=config.output_mode,
    )


def _common_settings(config: ModelConfig) -> ModelSettings:
    settings: ModelSettings = {
        "max_tokens": config.options.max_tokens,
        "timeout": config.request_timeout,
    }
    if config.options.temperature is not None:
        settings["temperature"] = config.options.temperature
    if config.options.top_p is not None:
        settings["top_p"] = config.options.top_p
    return settings


def _validate_openai_settings(
    config: OpenAIModelConfig | AzureModelConfig,
    profile: ModelProfile,
) -> None:
    options = config.options
    sampling_configured = options.temperature is not None or options.top_p is not None
    effort = options.reasoning_effort

    if config.api == "responses" and options.seed is not None:
        # PydanticAI 2.46 declares seed on the Responses settings TypedDict but
        # does not put it on the Responses API request.
        raise _invalid_configuration()

    if effort == "none" and profile.get("openai_supports_reasoning", False):
        if not profile.get("openai_supports_reasoning_effort_none", False):
            raise _invalid_configuration()
    if effort == "minimal" and not profile.get("openai_supports_minimal_reasoning_effort", True):
        # The unified adapter maps unsupported minimal effort to low. The
        # provider-specific setting should also fail early instead of relying
        # on an endpoint rejection or an eventual SDK normalization.
        raise _invalid_configuration()

    reasoning_active = effort is not None and effort != "none"
    if effort is None:
        reasoning_active = bool(
            profile.get("openai_supports_reasoning", False)
            and profile.get("openai_reasoning_enabled_by_default", False)
        )
    if sampling_configured and reasoning_active:
        # The installed adapter otherwise warns and silently removes sampling
        # fields. Configuration must be transmitted faithfully or rejected.
        raise _invalid_configuration()
    if config.output_mode == "tool":
        if not profile.get("openai_supports_tool_choice_required", True):
            raise _invalid_configuration()
        if reasoning_active and not profile.get(
            "openai_supports_forced_tool_choice_with_thinking", True
        ):
            raise _invalid_configuration()

    unsupported = set(cast(Sequence[str], profile.get("openai_unsupported_model_settings", ())))
    configured = {"max_tokens", "timeout", "openai_store"}
    if options.temperature is not None:
        configured.add("temperature")
    if options.top_p is not None:
        configured.add("top_p")
    if options.seed is not None:
        configured.add("seed")
    if options.reasoning_effort is not None:
        configured.add("openai_reasoning_effort")
    if configured & unsupported:
        raise _invalid_configuration()


def _openai_settings(
    config: OpenAIModelConfig | CompatibleModelConfig | AzureModelConfig,
) -> ModelSettings:
    settings = _common_settings(config)
    settings["openai_store"] = False  # type: ignore[typeddict-unknown-key]
    if config.options.seed is not None:
        settings["seed"] = config.options.seed
    if config.options.reasoning_effort is not None:
        settings["openai_reasoning_effort"] = config.options.reasoning_effort  # type: ignore[typeddict-unknown-key]
    return settings


def _validate_anthropic_settings(config: AnthropicModelConfig, profile: ModelProfile) -> None:
    options = config.options
    if config.output_mode == "tool" and not profile.get(
        "anthropic_supports_forced_tool_choice", True
    ):
        raise _invalid_configuration()
    if (options.temperature is not None or options.top_p is not None) and profile.get(
        "anthropic_disallows_sampling_settings", False
    ):
        raise _invalid_configuration()
    if options.thinking_budget is not None:
        if profile.get("anthropic_disallows_budget_thinking", False):
            raise _invalid_configuration()
        if config.output_mode == "tool":
            # Extended thinking is incompatible with the forced output tool.
            raise _invalid_configuration()

    thinking = options.thinking
    effort = options.effort
    if thinking == "adaptive":
        if not profile.get("anthropic_supports_adaptive_thinking", False):
            raise _invalid_configuration()
        if config.output_mode == "tool" and not profile.get(
            "anthropic_supports_forced_tool_choice", True
        ):
            raise _invalid_configuration()
    if effort is not None:
        if not profile.get("anthropic_supports_effort", False):
            raise _invalid_configuration()
        if effort == "xhigh" and not profile.get("anthropic_supports_xhigh_effort", False):
            # PydanticAI's unified mapper would downshift xhigh to max.
            raise _invalid_configuration()


def _binding(
    config: ModelConfig,
    model: Model,
    settings: ModelSettings,
    *,
    timeout_errors: tuple[type[Exception], ...],
) -> ModelBinding:
    from foliqant.core.admission import CapacityLimiter

    return ModelBinding(
        model=model,
        settings=settings,
        admission=CapacityLimiter(concurrency=config.concurrency, queue_limit=config.queue_limit),
        output_mode=config.output_mode,
        supports_text=config.supports_text,
        supports_json_schema=config.supports_json_schema,
        supports_tools=config.supports_tools,
        timeout_errors=timeout_errors,
    )


def _build_openai(
    config: OpenAIModelConfig,
    environment: Mapping[str, str],
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    client = AsyncOpenAI(
        api_key=_secret(environment, config.api_key_env, required=True),
        base_url="https://api.openai.com/v1",
        max_retries=0,
        timeout=config.request_timeout,
    )
    owned_clients.append(client)
    provider = OpenAIProvider(openai_client=client)
    model_type = OpenAIChatModel if config.api == "chat" else OpenAIResponsesModel
    model = model_type(config.model, provider=provider, profile=_profile(config))
    _validate_openai_settings(config, cast(ModelProfile, model.profile))
    return _binding(config, model, _openai_settings(config), timeout_errors=(APITimeoutError,))


def _build_compatible(
    config: CompatibleModelConfig,
    environment: Mapping[str, str],
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    client = AsyncOpenAI(
        api_key=_secret(environment, config.api_key_env, required=False),
        base_url=config.base_url,
        max_retries=0,
        timeout=config.request_timeout,
    )
    owned_clients.append(client)
    provider = OpenAIProvider(openai_client=client)
    # Do not infer reasoning behavior from an arbitrary compatible endpoint's
    # model name. Explicit native options are forwarded together; endpoint
    # conformance is established separately.
    profile = merge_profile(
        _profile(config),
        OpenAIModelProfile(
            openai_chat_supports_max_completion_tokens=config.max_tokens_field
            == "max_completion_tokens",
            openai_supports_reasoning=False,
            openai_reasoning_enabled_by_default=False,
            openai_unsupported_model_settings=(),
        ),
    )
    model = OpenAIChatModel(config.model, provider=provider, profile=profile)
    return _binding(config, model, _openai_settings(config), timeout_errors=(APITimeoutError,))


def _build_azure(
    config: AzureModelConfig,
    environment: Mapping[str, str],
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncAzureOpenAI, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.azure import AzureProvider
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    api_key = _secret(environment, config.api_key_env, required=True)
    profile: ModelProfile = _profile(config)
    if config.api_flavor == "versioned":
        assert config.api_version is not None
        azure_client = AsyncAzureOpenAI(
            azure_endpoint=config.endpoint,
            api_version=config.api_version,
            api_key=api_key,
            max_retries=0,
            timeout=config.request_timeout,
        )
        owned_clients.append(azure_client)
        azure_provider = AzureProvider(openai_client=azure_client)
        model_type = OpenAIChatModel if config.api == "chat" else OpenAIResponsesModel
        model = model_type(config.model, provider=azure_provider, profile=profile)
    else:
        # Azure's GA v1 endpoint rejects the api-version query parameter which
        # AsyncAzureOpenAI always injects. A plain async OpenAI client is the
        # supported SDK path for this explicit API flavor.
        openai_client = AsyncOpenAI(
            base_url=config.endpoint,
            api_key=api_key,
            max_retries=0,
            timeout=config.request_timeout,
        )
        owned_clients.append(openai_client)
        openai_provider = OpenAIProvider(openai_client=openai_client)
        profile = merge_profile(
            profile,
            OpenAIModelProfile(openai_chat_supports_document_input=False),
        )
        model_type = OpenAIChatModel if config.api == "chat" else OpenAIResponsesModel
        model = model_type(config.model, provider=openai_provider, profile=profile)

    _validate_openai_settings(config, cast(ModelProfile, model.profile))
    return _binding(config, model, _openai_settings(config), timeout_errors=(APITimeoutError,))


def _build_anthropic(
    config: AnthropicModelConfig,
    environment: Mapping[str, str],
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from anthropic import APITimeoutError, AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    _reject_ambient_request_configuration(_ANTHROPIC_AMBIENT_REQUEST_ENV)
    client = AsyncAnthropic(
        api_key=_secret(environment, config.api_key_env, required=True),
        base_url="https://api.anthropic.com",
        max_retries=0,
        timeout=config.request_timeout,
    )
    owned_clients.append(client)
    model = AnthropicModel(
        config.model,
        provider=AnthropicProvider(anthropic_client=client),
        profile=_profile(config),
    )
    _validate_anthropic_settings(config, cast(ModelProfile, model.profile))
    settings = _common_settings(config)
    if config.options.thinking_budget is not None:
        settings["anthropic_thinking"] = {  # type: ignore[typeddict-unknown-key]
            "type": "enabled",
            "budget_tokens": config.options.thinking_budget,
        }
    thinking = config.options.thinking
    if thinking is not None:
        settings["anthropic_thinking"] = {"type": thinking}  # type: ignore[typeddict-unknown-key]
    effort = config.options.effort
    if effort is not None:
        settings["anthropic_effort"] = effort  # type: ignore[typeddict-unknown-key]
    return _binding(config, model, settings, timeout_errors=(APITimeoutError,))


def _build_one(
    config: ModelConfig,
    environment: Mapping[str, str],
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    if isinstance(config, OpenAIModelConfig):
        return _build_openai(config, environment, owned_clients)
    if isinstance(config, CompatibleModelConfig):
        return _build_compatible(config, environment, owned_clients)
    if isinstance(config, AzureModelConfig):
        return _build_azure(config, environment, owned_clients)
    if isinstance(config, AnthropicModelConfig):
        return _build_anthropic(config, environment, owned_clients)
    if isinstance(config, BedrockModelConfig):
        # A region alone makes boto3 use its ambient credential provider chain,
        # which may perform EC2 metadata I/O during construction. The contract
        # needs explicit credentials or an explicit provider-chain opt-in plus a
        # separately bounded worker policy before this can be safe.
        raise _invalid_configuration()
    raise _invalid_configuration()


async def _close_clients(clients: list[_AsyncCloseable]) -> bool:
    async def close_one(client: _AsyncCloseable) -> bool:
        try:
            await client.close()
        except Exception:
            # Shutdown must not expose provider exception text or mask the
            # execution result. A later observation port can report a fixed
            # incomplete-drain event without retaining diagnostics here.
            return False
        return True

    async def bounded_cleanup() -> bool:
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
                results = await asyncio.gather(*(close_one(client) for client in reversed(clients)))
        except TimeoutError:
            return False
        return all(results)

    cleanup = asyncio.create_task(bounded_cleanup())
    try:
        return await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        # Preserve structured cancellation, but give owned clients the same
        # bounded opportunity to close before propagating it.
        try:
            await asyncio.shield(cleanup)
        finally:
            raise


@asynccontextmanager
async def open_model_bindings(
    profiles: ModelProfiles,
    *,
    environment: Mapping[str, str],
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Build immutable alias bindings without endpoint or model discovery.

    Provider clients are owned by this context and closed on partial
    construction failure, normal shutdown, exception, or cancellation.
    """
    owned_clients: list[_AsyncCloseable] = []
    try:
        try:
            validated = ModelProfiles.model_validate(
                profiles.model_dump(mode="python"),
                strict=True,
            )
            frozen_environment = dict(environment)
            bindings = {
                alias: _build_one(config, frozen_environment, owned_clients)
                for alias, config in validated.models.items()
            }
        except ServiceError:
            raise
        except Exception:
            raise _invalid_configuration() from None
        yield MappingProxyType(bindings)
    finally:
        active_exception = sys.exc_info()[0] is not None
        clean = await _close_clients(owned_clients)
        if not clean and not active_exception:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)

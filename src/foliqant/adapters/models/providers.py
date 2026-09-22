"""Explicit, offline-safe construction of configured model provider clients."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import Protocol, cast

import anyio
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.settings import ModelSettings

from foliqant.contracts.models import (
    AnthropicModelConfig,
    AzureModelConfig,
    BedrockModelConfig,
    CompatibleModelConfig,
    GoogleModelConfig,
    ModelConfig,
    ModelProfiles,
    OpenAIModelConfig,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.environment import EnvironmentResolver

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
_GOOGLE_AMBIENT_REQUEST_ENV = frozenset(
    {"GOOGLE_GENAI_CLIENT_MODE", "GOOGLE_GENAI_REPLAYS_DIRECTORY", "GOOGLE_GENAI_REPLAY_ID"}
)
_BEDROCK_AMBIENT_ENDPOINT_ENV = frozenset({"AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"})


class _AsyncCloseable(Protocol):
    async def close(self) -> None: ...


class _ClientOwner:
    """Close optional async and blocking SDK resources without blocking the loop."""

    def __init__(
        self,
        *,
        async_close: Callable[[], Awaitable[None]] | None = None,
        sync_close: Callable[[], None] | None = None,
    ) -> None:
        self._async_close = async_close
        self._sync_close = sync_close

    async def close(self) -> None:
        try:
            if self._async_close is not None:
                await self._async_close()
        finally:
            if self._sync_close is not None:
                # Closing a blocking SDK client has no interrupt primitive. The
                # outer cleanup deadline must still return a safe incomplete
                # status rather than wait indefinitely for a stuck close.
                await anyio.to_thread.run_sync(self._sync_close, abandon_on_cancel=True)


class _DrainBlockingRequest(WrapperModel):
    """Keep model admission owned until a started Bedrock thread has stopped."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        operation = asyncio.create_task(
            self.wrapped.request(messages, model_settings, model_request_parameters)
        )
        cancelled = False
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            # Consume a late SDK exception without exposing private diagnostics;
            # the caller still receives cancellation after real work stops.
            try:
                operation.result()
            except Exception:
                pass
            raise asyncio.CancelledError
        return operation.result()


def _invalid_configuration() -> ServiceError:
    return ServiceError(ErrorCode.INVALID_CONFIGURATION)


def _secret(value: SecretStr | None, *, required: bool) -> str:
    if value is None:
        if required:
            raise _invalid_configuration()
        return "credential-not-required"
    secret = value.get_secret_value()
    if not secret.strip():
        raise _invalid_configuration()
    return secret


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
        retry=config.retry.policy(),
    )


def _build_openai(
    config: OpenAIModelConfig,
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    client = AsyncOpenAI(
        api_key=_secret(config.api_key, required=True),
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
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    client = AsyncOpenAI(
        api_key=_secret(config.api_key, required=False),
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
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from openai import APITimeoutError, AsyncAzureOpenAI, AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.azure import AzureProvider
    from pydantic_ai.providers.openai import OpenAIProvider

    _reject_ambient_request_configuration(_OPENAI_AMBIENT_REQUEST_ENV)
    api_key = _secret(config.api_key, required=True)
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
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    from anthropic import APITimeoutError, AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.profiles.anthropic import AnthropicModelProfile
    from pydantic_ai.providers.anthropic import AnthropicProvider

    _reject_ambient_request_configuration(_ANTHROPIC_AMBIENT_REQUEST_ENV)
    client = AsyncAnthropic(
        api_key=_secret(config.api_key, required=True),
        base_url="https://api.anthropic.com",
        max_retries=0,
        timeout=config.request_timeout,
    )
    owned_clients.append(client)
    model = AnthropicModel(
        config.model,
        provider=AnthropicProvider(anthropic_client=client),
        # Disable SDK-level stale-thinking recovery: every retry must be visible
        # to our attempt budget, and HTTP 400 remains terminal.
        profile=merge_profile(
            _profile(config),
            AnthropicModelProfile(
                anthropic_binds_thinking_blocks=False,
            ),
        ),
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


def _validate_provider_capabilities(config: ModelConfig, profile: ModelProfile) -> None:
    for claimed, field in (
        (config.supports_text, "supports_text_output"),
        (config.supports_tools, "supports_tools"),
    ):
        if claimed and profile.get(field) is False:
            raise _invalid_configuration()
    # ToolOutput supplies a JSON Schema through a forced function tool. Native
    # structured-output support is required only for native mode.
    if (
        config.output_mode == "native"
        and config.supports_json_schema
        and profile.get("supports_json_schema_output") is False
    ):
        raise _invalid_configuration()


def _build_google(
    config: GoogleModelConfig,
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    import httpx2
    from google.genai.types import HttpRetryOptions
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    _reject_ambient_request_configuration(_GOOGLE_AMBIENT_REQUEST_ENV)
    http_client = httpx2.AsyncClient(timeout=config.request_timeout, trust_env=False)
    owner = _ClientOwner(async_close=http_client.aclose)
    owned_clients.append(owner)
    provider = GoogleProvider(
        api_key=_secret(config.api_key, required=True),
        http_client=http_client,
        # Pin the official Gemini API host. The Google SDK otherwise accepts
        # GOOGLE_GEMINI_BASE_URL or a process-global default set by other code.
        base_url="https://generativelanguage.googleapis.com/",
        retry_options=HttpRetryOptions(attempts=1),
    )
    # The SDK constructs a sync client too, even though Foliqant only calls its
    # async API. Close both at shutdown.
    owner._sync_close = provider.client.close
    _validate_provider_capabilities(config, provider.model_profile(config.model) or {})
    model = GoogleModel(config.model, provider=provider, profile=_profile(config))
    return _binding(
        config, model, _common_settings(config), timeout_errors=(httpx2.TimeoutException,)
    )


async def _build_bedrock(
    config: BedrockModelConfig,
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]
    from botocore.exceptions import (  # type: ignore[import-untyped]
        ConnectTimeoutError,
        ReadTimeoutError,
    )
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    _reject_ambient_request_configuration(_BEDROCK_AMBIENT_ENDPOINT_ENV)

    def create_client() -> object:
        # Credential discovery may perform blocking I/O (including metadata
        # service access). Keep all of it off the event loop. The host's AWS
        # credential chain remains authoritative; no workflow secret is stored.
        session = boto3.Session(region_name=config.region)
        return session.client(
            "bedrock-runtime",
            config=Config(
                connect_timeout=config.request_timeout,
                read_timeout=config.request_timeout,
                # A profile in ~/.aws/config may declare endpoint_url. Region
                # selection must not silently redirect the model request.
                ignore_configured_endpoint_urls=True,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )

    # Raw asyncio cancellation can interrupt an AnyIO worker await even when
    # the worker itself cannot stop. Keep the creator task owned until it
    # returns, then register the client before cancellation propagates.
    creation = asyncio.create_task(anyio.to_thread.run_sync(create_client))
    cancelled = False
    while not creation.done():
        try:
            await asyncio.shield(creation)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled and creation.exception() is not None:
        raise asyncio.CancelledError
    client = creation.result()
    # boto3's close method is synchronous. Register ownership before any model
    # construction that could fail.
    sync_close = cast(Callable[[], None], client.close)  # type: ignore[attr-defined]
    owned_clients.append(_ClientOwner(sync_close=sync_close))
    if cancelled:
        raise asyncio.CancelledError
    provider = BedrockProvider(bedrock_client=client)
    discovered = provider.model_profile(config.model) or {}
    _validate_provider_capabilities(config, discovered)
    if (
        config.options.temperature is not None or config.options.top_p is not None
    ) and discovered.get("anthropic_disallows_sampling_settings", False):
        # The locked PydanticAI Bedrock adapter otherwise drops these fields
        # with a warning, so authored options would not reach Converse.
        raise _invalid_configuration()
    if (
        config.output_mode == "native"
        and config.supports_json_schema
        and discovered.get("supports_json_schema_output") is not True
    ):
        raise _invalid_configuration()
    if config.output_mode == "tool" and not discovered.get("bedrock_supports_tool_choice", False):
        raise _invalid_configuration()
    model = BedrockConverseModel(config.model, provider=provider, profile=_profile(config))
    return _binding(
        config,
        _DrainBlockingRequest(model),
        _common_settings(config),
        timeout_errors=(ConnectTimeoutError, ReadTimeoutError),
    )


async def _build_one(
    config: ModelConfig,
    owned_clients: list[_AsyncCloseable],
) -> ModelBinding:
    if isinstance(config, OpenAIModelConfig):
        return _build_openai(config, owned_clients)
    if isinstance(config, CompatibleModelConfig):
        return _build_compatible(config, owned_clients)
    if isinstance(config, AzureModelConfig):
        return _build_azure(config, owned_clients)
    if isinstance(config, AnthropicModelConfig):
        return _build_anthropic(config, owned_clients)
    if isinstance(config, GoogleModelConfig):
        return _build_google(config, owned_clients)
    if isinstance(config, BedrockModelConfig):
        return await _build_bedrock(config, owned_clients)
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
            validated = EnvironmentResolver(environment).resolve(profiles)
            bindings = {
                alias: await _build_one(config, owned_clients)
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

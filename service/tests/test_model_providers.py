import json
from collections.abc import Mapping
from typing import Any

import pytest

from foliqant.adapters.models.providers import open_model_bindings
from foliqant.contracts.models import ModelProfiles
from foliqant.core.errors import ErrorCode, ServiceError


def _profiles(models: Mapping[str, dict[str, Any]]) -> ModelProfiles:
    return ModelProfiles.model_validate({"models": models})


@pytest.mark.asyncio
async def test_constructs_explicit_async_providers_without_requests_and_closes_clients() -> None:
    profiles = _profiles(
        {
            "openai_chat": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-4o-mini",
                "output_mode": "tool",
                "request_timeout": 11,
                "options": {"max_tokens": 123, "temperature": 0.2, "seed": 7},
            },
            "openai_responses": {
                "provider": "openai",
                "api": "responses",
                "model": "gpt-4o-mini",
                "output_mode": "native",
            },
            "local_chat": {
                "provider": "openai_compatible",
                "model": "custom-model",
                "output_mode": "tool",
                "base_url": "http://127.0.0.1:11434/v1",
                "allow_insecure_http": True,
                "max_tokens_field": "max_tokens",
            },
            "azure_versioned": {
                "provider": "azure_openai",
                "api": "chat",
                "api_flavor": "versioned",
                "endpoint": "https://unit.openai.azure.com",
                "api_version": "2026-01-01",
                "model": "deployment-chat",
                "output_mode": "tool",
            },
            "azure_v1": {
                "provider": "azure_openai",
                "api": "responses",
                "api_flavor": "v1",
                "endpoint": "https://unit.openai.azure.com/openai/v1",
                "model": "deployment-responses",
                "output_mode": "native",
            },
            "anthropic_messages": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_mode": "native",
                "options": {"max_tokens": 4096, "thinking_budget": 1024},
            },
        }
    )

    clients: list[Any] = []
    async with open_model_bindings(
        profiles,
        environment={
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "AZURE_OPENAI_API_KEY": "azure-secret",
        },
    ) as bindings:
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

        assert isinstance(bindings["openai_chat"].model, OpenAIChatModel)
        assert isinstance(bindings["openai_responses"].model, OpenAIResponsesModel)
        assert isinstance(bindings["local_chat"].model, OpenAIChatModel)
        assert isinstance(bindings["azure_versioned"].model, OpenAIChatModel)
        assert isinstance(bindings["azure_v1"].model, OpenAIResponsesModel)
        assert isinstance(bindings["anthropic_messages"].model, AnthropicModel)

        clients = [binding.model.client for binding in bindings.values()]  # type: ignore[attr-defined]
        assert all(client.max_retries == 0 for client in clients)
        assert all(
            (client.timeout.read if hasattr(client.timeout, "read") else client.timeout)
            == bindings[name].settings["timeout"]
            for name, client in zip(bindings, clients, strict=True)
        )
        assert str(bindings["openai_chat"].model.client.base_url) == "https://api.openai.com/v1/"  # type: ignore[attr-defined]
        assert (
            str(bindings["anthropic_messages"].model.client.base_url) == "https://api.anthropic.com"
        )  # type: ignore[attr-defined]
        assert bindings["openai_chat"].settings == {
            "max_tokens": 123,
            "temperature": 0.2,
            "timeout": 11.0,
            "openai_store": False,
            "seed": 7,
        }
        assert bindings["anthropic_messages"].settings["anthropic_thinking"] == {
            "type": "enabled",
            "budget_tokens": 1024,
        }
        assert bindings["azure_versioned"].model.system == "azure"
        assert bindings["azure_v1"].model.system == "openai"
        assert (
            bindings["local_chat"].model.profile["openai_chat_supports_max_completion_tokens"]
            is False
        )

        with pytest.raises(TypeError):
            bindings["another"] = bindings["openai_chat"]  # type: ignore[index]

    assert all(client.is_closed() for client in clients)


@pytest.mark.asyncio
async def test_rejects_configuration_the_installed_adapter_would_silently_drop() -> None:
    reasoning_sampling = _profiles(
        {
            "model": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-5",
                "output_mode": "tool",
                "options": {"temperature": 0.3},
            }
        }
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(
            reasoning_sampling, environment={"OPENAI_API_KEY": "secret"}
        ):
            pytest.fail("invalid provider settings were accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION

    responses_seed = _profiles(
        {
            "model": {
                "provider": "openai",
                "api": "responses",
                "model": "gpt-4o-mini",
                "output_mode": "native",
                "options": {"seed": 7},
            }
        }
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(responses_seed, environment={"OPENAI_API_KEY": "secret"}):
            pytest.fail("untransmitted Responses setting was accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.asyncio
async def test_compatible_profile_preserves_explicit_sampling_and_reasoning() -> None:
    profiles = _profiles(
        {
            "local": {
                "provider": "openai_compatible",
                "model": "gpt-5-local",
                "output_mode": "tool",
                "base_url": "https://models.example.test/v1",
                "max_tokens_field": "max_completion_tokens",
                "options": {"temperature": 0.4, "reasoning_effort": "high"},
            }
        }
    )
    async with open_model_bindings(profiles, environment={}) as bindings:
        binding = bindings["local"]
        assert binding.settings["temperature"] == 0.4
        assert binding.settings["openai_reasoning_effort"] == "high"
        assert binding.model.profile["openai_supports_reasoning"] is False
        assert binding.model.profile["openai_chat_supports_max_completion_tokens"] is True


@pytest.mark.asyncio
async def test_compatible_wire_request_has_bounded_settings_and_no_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2
    import openai
    from pydantic_ai import Agent

    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "local-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    original_client = openai.AsyncOpenAI

    def client_with_mock_transport(**kwargs: Any) -> openai.AsyncOpenAI:
        return original_client(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", client_with_mock_transport)
    profiles = _profiles(
        {
            "local": {
                "provider": "openai_compatible",
                "model": "local-model",
                "output_mode": "tool",
                "base_url": "https://local.example.test/v1",
                "options": {"max_tokens": 37, "temperature": 0.25},
            }
        }
    )
    async with open_model_bindings(profiles, environment={}) as bindings:
        binding = bindings["local"]
        result = await Agent(
            binding.model,
            output_type=str,
            model_settings=binding.settings,
            retries=0,
        ).run("hello")
        assert result.output == "ok"

    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://local.example.test/v1/chat/completions"
    assert request.headers["x-stainless-retry-count"] == "0"
    body = json.loads(request.content)
    assert body["model"] == "local-model"
    assert body["max_tokens"] == 37
    assert body["temperature"] == 0.25
    assert body["store"] is False
    assert "max_completion_tokens" not in body
    assert all("models" not in item.url.path for item in requests)


@pytest.mark.asyncio
async def test_anthropic_adaptive_settings_are_transmitted_or_rejected_from_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import httpx2
    from pydantic_ai import Agent

    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    original_client = anthropic.AsyncAnthropic

    def client_with_mock_transport(**kwargs: Any) -> anthropic.AsyncAnthropic:
        return original_client(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client_with_mock_transport)
    supported = _profiles(
        {
            "model": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_mode": "native",
                "options": {"thinking": "adaptive", "effort": "high"},
            }
        }
    )
    async with open_model_bindings(
        supported, environment={"ANTHROPIC_API_KEY": "secret"}
    ) as bindings:
        binding = bindings["model"]
        assert binding.settings["anthropic_thinking"] == {"type": "adaptive"}
        assert binding.settings["anthropic_effort"] == "high"
        result = await Agent(
            binding.model,
            output_type=str,
            model_settings=binding.settings,
            retries=0,
        ).run("hello")
        assert result.output == "ok"

    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"

    unsupported = _profiles(
        {
            "model": {
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "output_mode": "native",
                "options": {"thinking": "adaptive", "effort": "high"},
            }
        }
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(unsupported, environment={"ANTHROPIC_API_KEY": "secret"}):
            pytest.fail("unsupported adaptive thinking reached runtime")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.asyncio
async def test_missing_secret_and_ambient_headers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_profile = _profiles(
        {
            "model": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-4o-mini",
                "output_mode": "tool",
            }
        }
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(openai_profile, environment={}):
            pytest.fail("missing secret was accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION

    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Unsafe: process-value")
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(openai_profile, environment={"OPENAI_API_KEY": "secret"}):
            pytest.fail("ambient request headers were accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.asyncio
async def test_explicit_cloud_base_url_ignores_ambient_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unsafe.example.test/v1")
    profiles = _profiles(
        {
            "model": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-4o-mini",
                "output_mode": "tool",
            }
        }
    )
    async with open_model_bindings(profiles, environment={"OPENAI_API_KEY": "secret"}) as bindings:
        assert str(bindings["model"].model.client.base_url) == "https://api.openai.com/v1/"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_partial_construction_failure_closes_prior_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openai import AsyncOpenAI

    original_close = AsyncOpenAI.close
    closed: list[AsyncOpenAI] = []

    async def tracked_close(client: AsyncOpenAI) -> None:
        closed.append(client)
        await original_close(client)

    monkeypatch.setattr(AsyncOpenAI, "close", tracked_close)
    profiles = _profiles(
        {
            "first": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-4o-mini",
                "output_mode": "tool",
            },
            "missing_anthropic_secret": {
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "output_mode": "native",
            },
        }
    )
    with pytest.raises(ServiceError):
        async with open_model_bindings(profiles, environment={"OPENAI_API_KEY": "secret"}):
            pytest.fail("partial construction did not fail")
    assert len(closed) == 1
    assert closed[0].is_closed()


@pytest.mark.asyncio
async def test_cleanup_failure_is_fixed_safe_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openai import AsyncOpenAI

    original_close = AsyncOpenAI.close

    async def failing_close(client: AsyncOpenAI) -> None:
        await original_close(client)
        raise RuntimeError("provider close diagnostics must not escape")

    monkeypatch.setattr(AsyncOpenAI, "close", failing_close)
    profiles = _profiles(
        {
            "model": {
                "provider": "openai",
                "api": "chat",
                "model": "gpt-4o-mini",
                "output_mode": "tool",
            }
        }
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(profiles, environment={"OPENAI_API_KEY": "secret"}):
            pass
    assert error.value.code == ErrorCode.DEPENDENCY_FAILURE
    assert "diagnostics" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("timed_out", [True, False])
async def test_sdk_transport_timeout_is_classified_without_losing_attempt(
    monkeypatch: pytest.MonkeyPatch, provider: str, timed_out: bool
) -> None:
    import asyncio

    import anthropic
    import httpx2
    import openai

    from foliqant.adapters.models import ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.budget import StepBudget
    from foliqant.core.execution import CallerContext, TokenUsage
    from foliqant.core.identity import Identity
    from foliqant.core.plan import LlmStepPlan, SourceLocation, WorkflowPlan
    from foliqant.ports.execution import StepContext

    attempts = 0
    private_message = "PRIVATE provider timeout diagnostics"

    async def fail_request(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        error_type = httpx2.ReadTimeout if timed_out else httpx2.ConnectError
        raise error_type(private_message, request=request)

    sdk = openai if provider == "openai" else anthropic
    client_name = "AsyncOpenAI" if provider == "openai" else "AsyncAnthropic"
    original_client = getattr(sdk, client_name)

    def client_with_mock_transport(**kwargs: Any) -> Any:
        return original_client(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(fail_request)),
        )

    monkeypatch.setattr(sdk, client_name, client_with_mock_transport)
    profile: dict[str, Any] = {
        "provider": provider,
        "model": "gpt-4o-mini" if provider == "openai" else "claude-sonnet-4-6",
        "output_mode": "native",
    }
    if provider == "openai":
        profile["api"] = "chat"
    location = SourceLocation("steps/generate.yaml", 1, 1)
    step = LlmStepPlan(
        name="generate",
        type="llm",
        location=location,
        model="configured",
        instructions="Return a concise result.",
        output_kind="text",
    )
    plan = WorkflowPlan(
        name="timeout_test",
        revision="a" * 64,
        start=step.name,
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=(),
        output=None,
        steps=(step,),
        location=location,
    )
    budget = StepBudget(model_requests=1, tool_calls=0)
    context = StepContext(
        execution_id="timeout-test",
        workflow=plan.name,
        revision=plan.revision,
        step_id=step.name,
        caller=CallerContext(Identity(), {}),
        deadline=asyncio.get_running_loop().time() + 5,
        model_timeout=5,
        tool_timeout=1,
        budget=budget,
    )
    async with open_model_bindings(
        _profiles({"configured": profile}),
        environment={"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret"},
    ) as bindings:
        executor = ModelExecutor(bindings, WorkflowSchemas(plan))
        with pytest.raises(ServiceError) as error:
            await executor.execute(step, {}, context)
        assert bindings["configured"].admission.active == 0

    assert error.value.code == (ErrorCode.TIMEOUT if timed_out else ErrorCode.DEPENDENCY_FAILURE)
    assert private_message not in str(error.value)
    assert error.value.__cause__ is None
    assert attempts == budget.snapshot().model_requests == 1
    assert budget.snapshot().tokens == TokenUsage()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "tool"])
async def test_anthropic_authored_schema_is_preserved_or_rejected_before_inference(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import asyncio
    from typing import cast

    import anthropic
    import httpx2

    from foliqant.adapters.models import ModelExecutor
    from foliqant.adapters.validation import WorkflowSchemas
    from foliqant.core.budget import StepBudget
    from foliqant.core.execution import CallerContext, TokenUsage
    from foliqant.core.identity import Identity
    from foliqant.core.json import FrozenObject, freeze_json, thaw_json
    from foliqant.core.plan import LlmStepPlan, SchemaResourcePlan, SourceLocation, WorkflowPlan
    from foliqant.ports.execution import StepContext

    authored_schema = {
        "type": "object",
        "additionalProperties": {"type": "integer"},
        "minProperties": 1,
    }
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        body = json.loads(request.content)
        output_tool = body["tools"][0]
        assert output_tool["input_schema"]["properties"]["value"] == authored_schema
        assert output_tool.get("strict") is not True
        assert body["tool_choice"]["type"] == "any"
        return httpx2.Response(
            200,
            request=request,
            json={
                "id": "msg-schema-test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-result",
                        "name": output_tool["name"],
                        "input": {"value": {"amount": 42}},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    original_client = anthropic.AsyncAnthropic

    def client_with_mock_transport(**kwargs: Any) -> anthropic.AsyncAnthropic:
        return original_client(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", client_with_mock_transport)
    location = SourceLocation("steps/extract.yaml", 1, 1)
    schema = cast(FrozenObject, freeze_json(authored_schema))
    step = LlmStepPlan(
        name="extract",
        type="llm",
        location=location,
        model="configured",
        instructions="Return named integer amounts.",
        output_kind="schema",
        output_schema_path="schemas/amounts.json",
        output_schema=schema,
    )
    plan = WorkflowPlan(
        name="schema_test",
        revision="a" * 64,
        start=step.name,
        default_model=None,
        input_schema_path=None,
        input_schema=None,
        schema_resources=(SchemaResourcePlan("schemas/amounts.json", schema),),
        output=None,
        steps=(step,),
        location=location,
    )
    budget = StepBudget(model_requests=1, tool_calls=0)
    context = StepContext(
        execution_id="schema-test",
        workflow=plan.name,
        revision=plan.revision,
        step_id=step.name,
        caller=CallerContext(Identity(), {}),
        deadline=asyncio.get_running_loop().time() + 5,
        model_timeout=5,
        tool_timeout=1,
        budget=budget,
    )
    profiles = _profiles(
        {
            "configured": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "output_mode": mode,
            }
        }
    )
    async with open_model_bindings(
        profiles, environment={"ANTHROPIC_API_KEY": "secret"}
    ) as bindings:
        executor = ModelExecutor(bindings, WorkflowSchemas(plan))
        if mode == "native":
            with pytest.raises(ServiceError) as error:
                await executor.execute(step, {}, context)
            assert error.value.code == ErrorCode.INVALID_CONFIGURATION
            assert requests == []
            assert budget.snapshot().model_requests == 0
            assert budget.snapshot().tokens == TokenUsage.zero()
        else:
            result = await executor.execute(step, {}, context)
            assert thaw_json(result.result) == {"amount": 42}
            assert len(requests) == budget.snapshot().model_requests == 1
            assert budget.snapshot().tokens.input_tokens == 2
            assert budget.snapshot().tokens.output_tokens == 3

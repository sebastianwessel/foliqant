"""Native Google and Bedrock bindings are explicit, bounded, and offline-testable."""

import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, NativeOutput, ToolOutput
from pydantic_ai.exceptions import ModelHTTPError

from foliqant.adapters.models.binding import ModelBinding
from foliqant.adapters.models.executor import _InvocationModel
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.contracts.models import ModelProfiles
from foliqant.core.budget import StepBudget
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import CallerContext
from foliqant.core.identity import Identity
from foliqant.ports.execution import StepContext


def _profiles(provider: str, **settings: object) -> ModelProfiles:
    return ModelProfiles.model_validate(
        {"models": {"native": {"provider": provider, **settings}}}, strict=True
    )


def _invocation_model(binding: ModelBinding) -> _InvocationModel:
    context = StepContext(
        execution_id="native-provider-test",
        workflow="provider_test",
        revision="a" * 64,
        step_id="generate",
        flow_id="main",
        caller=CallerContext(Identity(), {}),
        deadline=asyncio.get_running_loop().time() + 10,
        model_timeout=10,
        tool_timeout=1,
        budget=StepBudget(model_requests=1, tool_calls=0),
    )
    return _InvocationModel(binding, context)


def _google_response(request: Any, part: dict[str, object]) -> Any:
    import httpx2

    return httpx2.Response(
        200,
        request=request,
        json={
            "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        },
    )


@pytest.mark.asyncio
async def test_google_native_text_json_tool_wire_and_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2
    from google.genai import _base_url

    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://env-redirect.example/")
    monkeypatch.setattr(_base_url, "_default_base_gemini_url", "https://global-redirect.example/")

    requests: list[httpx2.Request] = []
    clients: list[httpx2.AsyncClient] = []
    original_init = httpx2.AsyncClient.__init__

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        body = json.loads(request.content)
        if "tools" in body:
            name = body["tools"][0]["functionDeclarations"][0]["name"]
            part: dict[str, object] = {
                "functionCall": {"name": name, "args": {"response": {"label": "ok"}}}
            }
        elif body.get("generationConfig", {}).get("responseMimeType") == "application/json":
            part = {"text": '{"response":{"label":"ok"}}'}
        else:
            part = {"text": "ok"}
        return _google_response(request, part)

    def mock_init(self: httpx2.AsyncClient, **kwargs: Any) -> None:
        original_init(self, **kwargs, transport=httpx2.MockTransport(respond))
        clients.append(self)

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", mock_init)
    profiles = _profiles(
        "google",
        model="gemini-2.5-flash",
        output_mode="native",
        options={"max_tokens": 77, "temperature": 0.4, "top_p": 0.8},
    )
    async with open_model_bindings(
        profiles, environment={"GOOGLE_API_KEY": "unit-key"}
    ) as bindings:
        binding = bindings["native"]
        for output, expected in (
            (str, "ok"),
            (NativeOutput(dict[str, str]), {"label": "ok"}),
            (ToolOutput(dict[str, str]), {"label": "ok"}),
        ):
            result = await Agent(
                binding.model, output_type=output, model_settings=binding.settings, retries=0
            ).run("hello")
            assert result.output == expected

    assert len(requests) == 3
    assert clients and all(client.is_closed for client in clients)
    assert all(request.url.path.endswith(":generateContent") for request in requests)
    assert all(request.url.host == "generativelanguage.googleapis.com" for request in requests)
    assert all(request.headers["x-goog-api-key"] == "unit-key" for request in requests)
    bodies = [json.loads(request.content) for request in requests]
    assert all(body["generationConfig"]["maxOutputTokens"] == 77 for body in bodies)
    assert all(body["generationConfig"]["temperature"] == 0.4 for body in bodies)
    assert all(body["generationConfig"]["topP"] == 0.8 for body in bodies)
    assert bodies[1]["generationConfig"]["responseMimeType"] == "application/json"
    assert bodies[2]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    assert "responseMimeType" not in bodies[2]["generationConfig"]


@pytest.mark.asyncio
async def test_google_sdk_does_not_retry_failed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2

    requests: list[httpx2.Request] = []
    original_init = httpx2.AsyncClient.__init__

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(503, request=request, json={"error": {"message": "private"}})

    def mock_init(self: httpx2.AsyncClient, **kwargs: Any) -> None:
        original_init(self, **kwargs, transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", mock_init)
    profiles = _profiles("google", model="gemini-2.5-flash", output_mode="native")
    async with open_model_bindings(
        profiles, environment={"GOOGLE_API_KEY": "unit-key"}
    ) as bindings:
        with pytest.raises(ModelHTTPError) as error:
            await Agent(
                bindings["native"].model,
                output_type=str,
                model_settings=bindings["native"].settings,
                retries=0,
            ).run("hello")
        assert error.value.status_code == 503
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "variable"),
    [("google", "GOOGLE_GENAI_CLIENT_MODE"), ("bedrock", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME")],
)
async def test_native_providers_reject_ambient_request_or_endpoint_overrides(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, "unexpected-ambient-configuration")
    settings: dict[str, object] = {
        "model": "gemini-2.5-flash" if provider == "google" else "amazon.nova-lite-v1:0",
        "output_mode": "tool",
    }
    if provider == "bedrock":
        settings["region"] = "us-east-1"
    profiles = _profiles(provider, **settings)
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(profiles, environment={"GOOGLE_API_KEY": "unit-key"}):
            pytest.fail("ambient provider override was accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [("auth", ErrorCode.UNAUTHENTICATED), ("timeout", ErrorCode.REQUEST_TIMEOUT)],
)
async def test_google_auth_and_timeout_are_safe_and_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected: ErrorCode,
) -> None:
    import httpx2

    requests: list[httpx2.Request] = []
    original_init = httpx2.AsyncClient.__init__

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if kind == "auth":
            return httpx2.Response(
                401,
                request=request,
                json={"error": {"code": 401, "message": "PRIVATE", "status": "UNAUTHENTICATED"}},
            )
        raise httpx2.ReadTimeout("PRIVATE", request=request)

    def mock_init(self: httpx2.AsyncClient, **kwargs: Any) -> None:
        original_init(self, **kwargs, transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", mock_init)
    profiles = _profiles("google", model="gemini-2.5-flash", output_mode="native")
    async with open_model_bindings(
        profiles, environment={"GOOGLE_API_KEY": "unit-key"}
    ) as bindings:
        binding = bindings["native"]
        with pytest.raises(ServiceError) as error:
            await Agent(
                _invocation_model(binding),
                output_type=str,
                model_settings=binding.settings,
                retries=0,
            ).run("hello")
        assert binding.admission.active == 0
    assert error.value.code == expected
    assert "PRIVATE" not in str(error.value)
    assert len(requests) == 1


def _mock_bedrock_session(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[Any]:
    import boto3  # type: ignore[import-untyped]

    clients: list[Any] = []
    original_session = boto3.Session

    def session(*args: Any, **kwargs: Any) -> Any:
        result = original_session(
            aws_access_key_id="unit-access",
            aws_secret_access_key="unit-secret",
            region_name=kwargs["region_name"],
        )
        original_client = result.client

        def client(*args: Any, **kwargs: Any) -> Any:
            created = original_client(*args, **kwargs)
            created.converse = lambda **params: responder(params)
            clients.append(created)
            return created

        result.client = client
        return result

    monkeypatch.setattr(boto3, "Session", session)
    return clients


@pytest.mark.asyncio
async def test_bedrock_ignores_endpoint_from_aws_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import boto3  # type: ignore[import-untyped]

    config_file = tmp_path / "aws-config"
    config_file.write_text(
        "[default]\nregion = us-east-1\nendpoint_url = https://profile-redirect.example\n"
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))
    # Prove the fixture would redirect an ordinary SDK client. No request is sent.
    baseline = boto3.Session(
        aws_access_key_id="unit-access",
        aws_secret_access_key="unit-secret",
        region_name="us-east-1",
    ).client("bedrock-runtime")
    assert baseline.meta.endpoint_url == "https://profile-redirect.example"
    baseline.close()

    clients = _mock_bedrock_session(
        monkeypatch, lambda params: pytest.fail("startup called Converse")
    )
    profiles = _profiles(
        "bedrock", model="amazon.nova-lite-v1:0", region="us-east-1", output_mode="tool"
    )
    async with open_model_bindings(profiles, environment={}):
        assert len(clients) == 1
        assert clients[0].meta.endpoint_url == "https://bedrock-runtime.us-east-1.amazonaws.com"


@pytest.mark.asyncio
async def test_bedrock_converse_native_and_tool_wire_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def respond(params: dict[str, Any]) -> dict[str, Any]:
        requests.append(params)
        if "toolConfig" in params:
            name = params["toolConfig"]["tools"][0]["toolSpec"]["name"]
            content = [
                {
                    "toolUse": {
                        "toolUseId": "unit-1",
                        "name": name,
                        "input": {"response": {"label": "ok"}},
                    }
                }
            ]
            stop = "tool_use"
        else:
            content = [{"text": '{"response":{"label":"ok"}}'}]
            stop = "end_turn"
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": stop,
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }

    clients = _mock_bedrock_session(monkeypatch, respond)
    profiles = _profiles(
        "bedrock",
        model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region="$AWS_REGION",
        output_mode="native",
        options={"max_tokens": 89, "temperature": 0.3, "top_p": 0.7},
    )
    async with open_model_bindings(profiles, environment={"AWS_REGION": "us-east-1"}) as bindings:
        binding = bindings["native"]
        for output in (NativeOutput(dict[str, str]), ToolOutput(dict[str, str])):
            result = await Agent(
                binding.model, output_type=output, model_settings=binding.settings, retries=0
            ).run("hello")
            assert result.output == {"label": "ok"}

    assert len(requests) == 2
    assert clients[0].meta.region_name == "us-east-1"
    assert clients[0]._client_config.retries["total_max_attempts"] == 1
    assert all(request["modelId"] == profiles.models["native"].model for request in requests)
    assert all(
        request["inferenceConfig"] == {"maxTokens": 89, "temperature": 0.3, "topP": 0.7}
        for request in requests
    )
    assert requests[0]["outputConfig"]["textFormat"]["type"] == "json_schema"
    assert requests[1]["toolConfig"]["toolChoice"] == {"any": {}}

    # Nova has no native JSON Schema output but can return structured data
    # through the required output tool. The common schema capability remains
    # true so the compiler can admit schema steps in tool mode.
    nova = _profiles(
        "bedrock",
        model="amazon.nova-lite-v1:0",
        region="us-east-1",
        output_mode="tool",
    )
    async with open_model_bindings(nova, environment={}) as bindings:
        binding = bindings["native"]
        result = await Agent(
            binding.model,
            output_type=ToolOutput(dict[str, str]),
            model_settings=binding.settings,
            retries=0,
        ).run("hello")
        assert result.output == {"label": "ok"}
    assert requests[2]["modelId"] == "amazon.nova-lite-v1:0"
    assert "outputConfig" not in requests[2]
    assert requests[2]["toolConfig"]["toolChoice"] == {"any": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [("auth", ErrorCode.FORBIDDEN), ("timeout", ErrorCode.REQUEST_TIMEOUT)],
)
async def test_bedrock_auth_and_timeout_are_safe_and_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected: ErrorCode,
) -> None:
    from botocore.exceptions import ClientError, ReadTimeoutError  # type: ignore[import-untyped]

    attempts = 0

    def respond(params: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if kind == "auth":
            raise ClientError(
                {
                    "Error": {"Code": "AccessDeniedException", "Message": "PRIVATE"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "Converse",
            )
        raise ReadTimeoutError(endpoint_url="https://bedrock.example", error="PRIVATE")

    _mock_bedrock_session(monkeypatch, respond)
    profiles = _profiles(
        "bedrock",
        model="amazon.nova-lite-v1:0",
        region="us-east-1",
        output_mode="tool",
    )
    async with open_model_bindings(profiles, environment={}) as bindings:
        binding = bindings["native"]
        with pytest.raises(ServiceError) as error:
            await Agent(
                _invocation_model(binding),
                output_type=str,
                model_settings=binding.settings,
                retries=0,
            ).run("hello")
        assert binding.admission.active == 0
    assert error.value.code == expected
    assert "PRIVATE" not in str(error.value)
    assert attempts == 1


@pytest.mark.asyncio
async def test_bedrock_rejects_options_that_sdk_would_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_bedrock_session(
        monkeypatch, lambda params: pytest.fail("unsupported settings reached model I/O")
    )
    profiles = _profiles(
        "bedrock",
        model="us.anthropic.claude-opus-4-7-v1:0",
        region="us-east-1",
        output_mode="tool",
        options={"temperature": 0.3},
    )
    with pytest.raises(ServiceError) as error:
        async with open_model_bindings(profiles, environment={}):
            pytest.fail("unsupported sampling was accepted")
    assert error.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_bedrock_interruption_keeps_started_thread_and_admission_until_done(
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def respond(params: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(5)
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }

    _mock_bedrock_session(monkeypatch, respond)
    profiles = _profiles(
        "bedrock",
        model="amazon.nova-lite-v1:0",
        region="us-east-1",
        output_mode="tool",
        supports_json_schema=False,
        concurrency=1,
        queue_limit=0,
    )
    async with open_model_bindings(profiles, environment={}) as bindings:
        binding = bindings["native"]
        loop = asyncio.get_running_loop()
        context = StepContext(
            execution_id="bedrock-cancel-test",
            workflow="cancel_test",
            revision="a" * 64,
            step_id="generate",
            flow_id="main",
            caller=CallerContext(Identity(), {}),
            deadline=loop.time() + (0.1 if stop == "timeout" else 10),
            model_timeout=10,
            tool_timeout=1,
            budget=StepBudget(model_requests=1, tool_calls=0),
        )
        model = _InvocationModel(binding, context)

        async def run() -> None:
            await Agent(model, output_type=str, model_settings=binding.settings, retries=0).run(
                "hello"
            )

        task = asyncio.create_task(run())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if stop == "cancel":
                task.cancel()
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.2)
            assert not task.done()
            assert binding.admission.active == 1
            with pytest.raises(ServiceError) as error:
                async with binding.admission.slot(deadline=asyncio.get_running_loop().time() + 1):
                    pytest.fail("cancelled Bedrock request freed admission early")
            assert error.value.code == ErrorCode.CAPACITY_EXCEEDED
        finally:
            release.set()
        if stop == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ServiceError) as error:
                await task
            # The step's run deadline (0.1 s) is the bound, not the 10 s model timeout.
            assert error.value.code == ErrorCode.RUN_TIMEOUT
        assert binding.admission.active == 0


@pytest.mark.asyncio
async def test_bedrock_cancel_during_client_construction_closes_created_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3  # type: ignore[import-untyped]

    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    original_session = boto3.Session

    def session(*args: Any, **kwargs: Any) -> Any:
        result = original_session(
            aws_access_key_id="unit-access",
            aws_secret_access_key="unit-secret",
            region_name=kwargs["region_name"],
        )
        original_client = result.client

        def client(*client_args: Any, **client_kwargs: Any) -> Any:
            entered.set()
            assert release.wait(5)
            created = original_client(*client_args, **client_kwargs)
            original_close = created.close

            def close() -> None:
                original_close()
                closed.set()

            created.close = close
            return created

        result.client = client
        return result

    monkeypatch.setattr(boto3, "Session", session)
    profiles = _profiles(
        "bedrock", model="amazon.nova-lite-v1:0", region="us-east-1", output_mode="tool"
    )

    async def construct() -> None:
        async with open_model_bindings(profiles, environment={}):
            pytest.fail("cancellation did not stop startup")

    task = asyncio.create_task(construct())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_blocking_client_close_reports_incomplete_at_cleanup_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foliqant.adapters.models import providers

    entered = threading.Event()
    release = threading.Event()

    def close() -> None:
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(providers, "_CLEANUP_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    try:
        clean = await providers._close_clients([providers._ClientOwner(sync_close=close)])
        assert not clean
        assert entered.is_set()
        assert time.monotonic() - started < 1
    finally:
        release.set()

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import ValidationError

from foliqant_model.contracts import ChatMessage, canonical_digest
from foliqant_model.curation.endpoint import (
    EndpointModelIdentity,
    GenerationRejected,
    GenerationResponse,
    LocalEndpointConfig,
    _select_model,
    discover_models,
    generate_json,
)
from foliqant_model.errors import ModelError


class _State:
    model_entries: list[dict[str, object]]
    native_models: list[dict[str, object]]
    completion: dict[str, object]
    completion_bytes: bytes | None
    delay_seconds: float
    counts: dict[str, int]
    request: dict[str, object] | None
    request_bytes: bytes
    redirect_models: bool

    def __init__(self) -> None:
        self.model_entries = [{"id": "local-model", "object": "model", "owned_by": "local"}]
        self.native_models = [
            {
                "type": "llm",
                "publisher": "local",
                "key": "local-model",
                "architecture": "qwen",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "size_bytes": 1024,
                "loaded_instances": [{"id": "local-model", "config": {"context_length": 4096}}],
                "max_context_length": 32768,
                "format": "gguf",
                "capabilities": {"vision": False, "trained_for_tool_use": False},
            }
        ]
        self.completion = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "model": "local-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"result":"ok"}'},
                }
            ],
            "usage": {"prompt_tokens": 999999, "completion_tokens": 999999},
        }
        self.completion_bytes = None
        self.delay_seconds = 0
        self.counts = {}
        self.request = None
        self.request_bytes = b""
        self.redirect_models = False


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    state: _State


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(
        self, body: bytes, *, status: int = 200, content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self) -> None:
        state = self.server.state
        state.counts[self.path] = state.counts.get(self.path, 0) + 1
        if self.path == "/v1/models" and state.redirect_models:
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.end_headers()
        elif self.path == "/v1/models":
            self._send(json.dumps({"object": "list", "data": state.model_entries}).encode())
        elif self.path == "/api/v1/models":
            self._send(json.dumps({"models": state.native_models}).encode())
        else:
            self._send(b'{"error":"missing"}', status=404)

    def do_POST(self) -> None:
        state = self.server.state
        state.counts[self.path] = state.counts.get(self.path, 0) + 1
        size = int(self.headers.get("Content-Length", "0"))
        state.request_bytes = self.rfile.read(size)
        state.request = json.loads(state.request_bytes)
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        body = state.completion_bytes
        if body is None:
            body = json.dumps(state.completion, separators=(",", ":")).encode()
        self._send(body)


@contextmanager
def _server() -> Iterator[tuple[_State, str]]:
    state = _State()
    server = _Server(("127.0.0.1", 0), _Handler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _config(base_url: str, **overrides: Any) -> LocalEndpointConfig:
    return LocalEndpointConfig(baseUrl=base_url, timeoutSeconds=5, **overrides)


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
        "additionalProperties": False,
    }


def _generate(
    config: LocalEndpointConfig, *, observed_identity: EndpointModelIdentity | None = None
) -> GenerationResponse:
    return generate_json(
        config,
        model_id="local-model",
        messages=[ChatMessage(role="user", content="Return a test object")],
        schema=_schema(),
        seed=7,
        observed_identity=observed_identity,
    )


def test_invalid_output_retains_complete_large_final_assistant_response() -> None:
    content = json.dumps({"wrong": "x" * 40_000}, separators=(",", ":"))
    with _server() as (state, base_url):
        state.completion["choices"] = [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ]
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    assert rejected.value.rejection.finalAssistantResponse == content
    assert len(rejected.value.rejection.finalAssistantResponse or "") > 32_768


def test_length_finish_without_final_content_records_absence() -> None:
    with _server() as (state, base_url):
        state.completion["choices"] = [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {"role": "assistant", "content": None},
            }
        ]
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    assert rejected.value.message == "Local endpoint truncated the generated output"
    assert rejected.value.rejection.finalAssistantResponse is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1234/v1",
        "http://example.org:1234/v1",
        "http://192.168.2.101:1234/v1",
        "http://127.0.0.1:1234/other",
        "http://user:secret@127.0.0.1:1234/v1",
        "http://127.0.0.1/v1",
    ],
)
def test_config_rejects_nonlocal_or_ambiguous_endpoint(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalEndpointConfig(baseUrl=url)


def test_localhost_is_normalized_without_dns() -> None:
    config = LocalEndpointConfig(baseUrl="http://localhost:1234/v1/")
    assert config.baseUrl == "http://127.0.0.1:1234/v1"


def test_config_allows_explicit_rfc1918_endpoint_only() -> None:
    config = LocalEndpointConfig(baseUrl="http://192.168.2.101:1234/v1", allowPrivateNetwork=True)
    assert config.baseUrl == "http://192.168.2.101:1234/v1"

    with pytest.raises(ValidationError):
        LocalEndpointConfig(baseUrl="http://8.8.8.8:1234/v1", allowPrivateNetwork=True)


@pytest.mark.parametrize("reasoning_effort", ["low", "medium", "xhigh"])
def test_config_accepts_bounded_reasoning_effort(reasoning_effort: str) -> None:
    config = LocalEndpointConfig.model_validate({"reasoningEffort": reasoning_effort})

    assert config.reasoningEffort == reasoning_effort
    assert config.model_dump(mode="json")["reasoningEffort"] == reasoning_effort


@pytest.mark.parametrize("reasoning_effort", [None, "", "high", "none", 1])
def test_config_rejects_null_or_unsupported_reasoning_effort(
    reasoning_effort: object,
) -> None:
    with pytest.raises(ValidationError):
        LocalEndpointConfig.model_validate({"reasoningEffort": reasoning_effort})


def test_config_omits_unspecified_reasoning_effort() -> None:
    assert "reasoningEffort" not in LocalEndpointConfig().model_dump(mode="json")


def test_discovery_uses_only_the_openai_compatible_models_endpoint() -> None:
    with _server() as (state, base_url):
        models = discover_models(_config(base_url))
    assert state.counts == {"/v1/models": 1}
    assert len(models) == 1
    model = models[0]
    assert model.model_dump(mode="json") == {
        "modelId": "local-model",
        "modelType": "unknown",
        "publisher": "local",
        "architecture": None,
        "format": None,
        "quantization": None,
        "sizeBytes": None,
        "maxContextLength": None,
        "metadataSha256": model.metadataSha256,
        "immutableRevision": None,
        "structuredOutput": "unknown",
    }


def test_selection_never_chooses_an_ambiguous_first_model() -> None:
    with _server() as (state, base_url):
        state.model_entries.append({"id": "second", "object": "model"})
        models = discover_models(_config(base_url))
        with pytest.raises(ModelError) as raised:
            _select_model(_config(base_url), models)
    assert raised.value.code == "CONFIG_INVALID"


def test_generation_uses_schema_and_returns_validated_provenance() -> None:
    with _server() as (state, base_url):
        response = _generate(_config(base_url))
    assert response.output == {"result": "ok"}
    assert response.finishReason == "stop"
    assert response.model.modelId == "local-model"
    assert response.model.immutableRevision is None
    assert response.model.structuredOutput == "verified-for-request"
    assert len(response.requestSha256) == len(response.schemaSha256) == 64
    assert len(response.rawResponseSha256) == 64
    assert state.counts == {
        "/v1/models": 1,
        "/v1/chat/completions": 1,
    }
    assert state.request is not None
    assert state.request["stream"] is False
    assert state.request["seed"] == 7
    assert "reasoning_effort" not in state.request
    response_format = state.request["response_format"]
    assert isinstance(response_format, dict)
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    assert "usage" not in response.model.model_dump(mode="json")


def test_generation_reuses_observed_identity_without_rediscovery() -> None:
    with _server() as (state, base_url):
        config = _config(base_url)
        identity = discover_models(config)[0]
        state.counts.clear()
        response = _generate(config, observed_identity=identity)
    assert response.model.modelId == identity.modelId
    assert state.counts == {"/v1/chat/completions": 1}
    assert state.request is not None
    assert "expectedModel" not in state.request
    assert "observed_identity" not in state.request


def test_generation_rejects_mismatched_observed_identity_before_http() -> None:
    with _server() as (state, base_url):
        config = _config(base_url)
        identity = discover_models(config)[0].model_copy(update={"modelId": "other-model"})
        state.counts.clear()
        with pytest.raises(ModelError) as raised:
            _generate(config, observed_identity=identity)
    assert raised.value.code == "CONFIG_INVALID"
    assert state.counts == {}


@pytest.mark.parametrize("structured_output", ["json-schema", "prompt"])
def test_reasoning_effort_is_wired_and_bound_to_request_identity(
    structured_output: str,
) -> None:
    with _server() as (state, base_url):
        direct = _generate(_config(base_url, structuredOutput=structured_output))
        reasoned_config = _config(
            base_url,
            structuredOutput=structured_output,
            reasoningEffort="medium",
        )
        reasoned = _generate(reasoned_config)

    assert state.request is not None
    assert state.request["reasoning_effort"] == "medium"
    assert reasoned.requestSha256 == hashlib.sha256(state.request_bytes).hexdigest()
    assert reasoned.requestSha256 != direct.requestSha256
    assert canonical_digest(reasoned_config.model_dump(mode="json")) != canonical_digest(
        _config(base_url, structuredOutput=structured_output).model_dump(mode="json")
    )


def test_prompt_structured_output_omits_guided_format_and_binds_effective_request() -> None:
    with _server() as (state, base_url):
        response = _generate(_config(base_url, structuredOutput="prompt"))
    assert response.output == {"result": "ok"}
    assert state.request is not None
    assert "response_format" not in state.request
    messages = state.request["messages"]
    assert isinstance(messages, list) and len(messages) == 1
    final = messages[0]
    assert isinstance(final, dict)
    assert final["role"] == "user"
    content = final["content"]
    assert isinstance(content, str)
    assert content.startswith("Return a test object")
    assert "Return only one strict JSON object" in content
    assert '"required":["result"]' in content
    assert response.requestSha256 == hashlib.sha256(state.request_bytes).hexdigest()


@pytest.mark.parametrize("structured_output", ["json-schema", "prompt"])
def test_declared_schema_order_survives_worker_and_is_bound_to_request(
    structured_output: str,
) -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"zulu": {"type": "string"}, "alpha": {"type": "string"}},
        "required": ["zulu", "alpha"],
        "additionalProperties": False,
    }
    with _server() as (state, base_url):
        state.completion["choices"] = [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"zulu":"ok","alpha":"ok"}'},
            }
        ]
        response = generate_json(
            _config(base_url, structuredOutput=structured_output),
            model_id="local-model",
            messages=[ChatMessage(role="user", content="Return JSON")],
            schema=schema,
            seed=1,
        )
        first_body = state.request_bytes
        reversed_schema = {
            **schema,
            "properties": {
                "alpha": {"type": "string"},
                "zulu": {"type": "string"},
            },
        }
        reordered = generate_json(
            _config(base_url, structuredOutput=structured_output),
            model_id="local-model",
            messages=[ChatMessage(role="user", content="Return JSON")],
            schema=reversed_schema,
            seed=1,
        )
    wire = json.loads(first_body)
    if structured_output == "json-schema":
        assert list(wire["response_format"]["json_schema"]["schema"]["properties"]) == [
            "zulu",
            "alpha",
        ]
    else:
        assert '"properties":{"zulu":' in wire["messages"][0]["content"]
    assert response.requestSha256 == hashlib.sha256(first_body).hexdigest()
    assert response.requestSha256 != reordered.requestSha256
    assert response.schemaSha256 == reordered.schemaSha256


def test_prompt_structured_output_never_accepts_reasoning_as_final_content() -> None:
    with _server() as (state, base_url):
        choices = state.completion["choices"]
        assert isinstance(choices, list) and isinstance(choices[0], dict)
        choices[0]["message"] = {
            "role": "assistant",
            "content": "",
            "reasoning_content": '{"result":"ok"}',
        }
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url, structuredOutput="prompt"))
    assert raised.value.code == "OUTPUT_INVALID"
    assert "no structured output" in raised.value.message


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"finish_reason": "length"}, "truncated"),
        (
            {"finish_reason": "stop", "message": {"refusal": "cannot", "content": None}},
            "refused",
        ),
        (
            {"finish_reason": "stop", "message": {"content": '{"result":1}'}},
            "does not match",
        ),
        (
            {
                "finish_reason": "stop",
                "message": {"content": '{"result":"a","result":"b"}'},
            },
            "strict JSON",
        ),
    ],
)
def test_generation_rejects_terminal_output_failures(
    update: dict[str, object], message: str
) -> None:
    with _server() as (state, base_url):
        choice = state.completion["choices"]
        assert isinstance(choice, list) and isinstance(choice[0], dict)
        choice[0].update(update)
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))
    assert raised.value.code == "OUTPUT_INVALID"
    assert message in raised.value.message
    assert state.counts["/v1/chat/completions"] == 1


def test_generation_rejects_oversized_response_without_retry() -> None:
    with _server() as (state, base_url):
        state.completion_bytes = b"{" + b" " * 2048 + b"}"
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url, maxResponseBytes=1024))
    assert raised.value.code == "OUTPUT_INVALID"
    assert state.counts["/v1/chat/completions"] == 1


def test_generation_hard_deadline_stops_waiting_without_retry() -> None:
    with _server() as (state, base_url):
        state.delay_seconds = 5
        started = time.monotonic()
        with pytest.raises(ModelError) as raised:
            _generate(LocalEndpointConfig(baseUrl=base_url, timeoutSeconds=1))
        elapsed = time.monotonic() - started
    assert raised.value.code == "TIMEOUT"
    assert elapsed < 3
    assert state.counts["/v1/chat/completions"] == 1


def test_invalid_schema_fails_before_chat_request() -> None:
    with _server() as (state, base_url):
        with pytest.raises(ModelError) as raised:
            generate_json(
                _config(base_url),
                model_id="local-model",
                messages=[ChatMessage(role="user", content="test")],
                schema={"type": "unsupported"},
                seed=1,
            )
    assert raised.value.code == "ARGUMENT_INVALID"
    assert state.counts.get("/v1/chat/completions", 0) == 0


def test_discovery_rejects_redirects() -> None:
    with _server() as (state, base_url):
        state.redirect_models = True
        with pytest.raises(ModelError) as raised:
            discover_models(_config(base_url))
    assert raised.value.code == "NETWORK_FAILED"
    assert state.counts == {"/v1/models": 1}


def test_generation_rejects_changed_model_identity() -> None:
    with _server() as (state, base_url):
        state.completion["model"] = "different-model"
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))
    assert raised.value.code == "OUTPUT_INVALID"
    assert "different model" in raised.value.message
    assert state.counts["/v1/chat/completions"] == 1


def test_external_schema_reference_fails_before_chat_request() -> None:
    with _server() as (state, base_url):
        with pytest.raises(ModelError) as raised:
            generate_json(
                _config(base_url),
                model_id="local-model",
                messages=[ChatMessage(role="user", content="test")],
                schema={"$ref": "https://example.org/schema.json"},
                seed=1,
            )
    assert raised.value.code == "ARGUMENT_INVALID"
    assert state.counts.get("/v1/chat/completions", 0) == 0

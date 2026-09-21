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
    GenerationRejection,
    GenerationResponse,
    GenerationTokenUsage,
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
    response_bytes: bytes
    response_fragments: list[bytes] | None
    fragment_delay_seconds: float
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
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 32,
                "total_tokens": 152,
                "completion_tokens_details": {"reasoning_tokens": 24},
            },
        }
        self.completion_bytes = None
        self.delay_seconds = 0
        self.counts = {}
        self.request = None
        self.request_bytes = b""
        self.response_bytes = b""
        self.response_fragments = None
        self.fragment_delay_seconds = 0
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
            body = _completion_stream(state.completion)
        state.response_bytes = body
        if state.response_fragments is None:
            self._send(body, content_type="text/event-stream")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Content-Length", str(sum(map(len, state.response_fragments))))
        self.end_headers()
        for fragment in state.response_fragments:
            try:
                self.wfile.write(fragment)
                self.wfile.flush()
            except BrokenPipeError:
                break
            if state.fragment_delay_seconds:
                time.sleep(state.fragment_delay_seconds)


def _sse_bytes(
    events: list[dict[str, object]], *, newline: bytes = b"\n", done: bool = True
) -> bytes:
    encoded = [
        b"data: "
        + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        + newline
        + newline
        for event in events
    ]
    if done:
        encoded.append(b"data: [DONE]" + newline + newline)
    return b"".join(encoded)


def _stream_chunk(
    delta: dict[str, object],
    *,
    finish_reason: object = None,
    stream_id: str = "chatcmpl-local",
    model: str = "local-model",
    index: int = 0,
) -> dict[str, object]:
    return {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": index, "delta": delta, "finish_reason": finish_reason}],
    }


def _completion_stream(completion: dict[str, object], *, newline: bytes = b"\n") -> bytes:
    stream_id = completion.get("id", "chatcmpl-local")
    model = completion.get("model")
    choices = completion.get("choices")
    assert isinstance(choices, list) and len(choices) == 1
    choice = choices[0]
    assert isinstance(choice, dict)
    index = choice.get("index", 0)
    message = choice.get("message")
    assert isinstance(message, dict)
    events: list[dict[str, object]] = [
        {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": index, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    ]
    delta = {
        key: message[key]
        for key in (
            "content",
            "reasoning_content",
            "refusal",
            "tool_calls",
            "function_call",
        )
        if key in message and message[key] is not None
    }
    if delta:
        events.append(
            {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": index, "delta": delta, "finish_reason": None}],
            }
        )
    events.append(
        {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {"index": index, "delta": {}, "finish_reason": choice.get("finish_reason")}
            ],
        }
    )
    if "usage" in completion:
        events.append(
            {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [],
                "usage": completion["usage"],
            }
        )
    return _sse_bytes(events, newline=newline)


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


def test_length_finish_retains_content_usage_and_degenerate_diagnostic() -> None:
    content = '{"result":"' + "x" * 2_000
    with _server() as (state, base_url):
        state.completion["choices"] = [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "private reasoning must not be retained",
                },
            }
        ]
        state.completion["usage"] = {
            "prompt_tokens": 450,
            "completion_tokens": 8_192,
            "total_tokens": 8_642,
            "completion_tokens_details": {"reasoning_tokens": 7_311},
        }
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    retained = rejected.value.rejection
    assert retained.finalAssistantResponse == content
    assert retained.finishReason == "length"
    assert retained.tokenUsage == GenerationTokenUsage(
        promptTokens=450,
        completionTokens=8_192,
        totalTokens=8_642,
        reasoningTokens=7_311,
    )
    assert retained.contentDiagnostic == "long-repeated-character-run"
    assert "private reasoning" not in retained.model_dump_json()


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
    assert rejected.value.rejection.finishReason == "length"
    assert rejected.value.rejection.tokenUsage == GenerationTokenUsage(
        promptTokens=120,
        completionTokens=32,
        totalTokens=152,
        reasoningTokens=24,
    )


def test_stop_finish_without_final_content_retains_rejection_metadata() -> None:
    with _server() as (state, base_url):
        state.completion["choices"] = [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": None},
            }
        ]
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    retained = rejected.value.rejection
    assert retained.message == "Local endpoint returned no structured output"
    assert retained.finalAssistantResponse is None
    assert retained.finishReason == "stop"
    assert retained.tokenUsage == GenerationTokenUsage(
        promptTokens=120,
        completionTokens=32,
        totalTokens=152,
        reasoningTokens=24,
    )
    assert retained.contentDiagnostic is None


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
    assert response.tokenUsage == GenerationTokenUsage(
        promptTokens=120,
        completionTokens=32,
        totalTokens=152,
        reasoningTokens=24,
    )
    assert response.contentDiagnostic is None
    assert len(response.requestSha256) == len(response.schemaSha256) == 64
    assert len(response.rawResponseSha256) == 64
    assert response.rawResponseSha256 == hashlib.sha256(state.response_bytes).hexdigest()
    assert state.counts == {
        "/v1/models": 1,
        "/v1/chat/completions": 1,
    }
    assert state.request is not None
    assert state.request["stream"] is True
    assert state.request["stream_options"] == {"include_usage": True}
    assert state.request["seed"] == 7
    assert "reasoning_effort" not in state.request
    response_format = state.request["response_format"]
    assert isinstance(response_format, dict)
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    assert "usage" not in response.model.model_dump(mode="json")


def test_sse_reader_handles_crlf_and_fragmented_multibyte_content() -> None:
    content = '{"result":"café 🌿"}'
    with _server() as (state, base_url):
        choice = state.completion["choices"]
        assert isinstance(choice, list) and isinstance(choice[0], dict)
        choice[0]["message"] = {
            "role": "assistant",
            "content": content,
            "reasoning_content": "private chain of thought",
        }
        body = _completion_stream(state.completion, newline=b"\r\n")
        marker = "🌿".encode()
        marker_start = body.index(marker)
        state.completion_bytes = body
        state.response_fragments = [
            body[: marker_start + 1],
            body[marker_start + 1 : marker_start + 3],
            body[marker_start + 3 :],
        ]
        response = _generate(_config(base_url))

    assert response.output == {"result": "café 🌿"}
    assert response.finalAssistantResponse == content
    assert response.rawResponseSha256 == hashlib.sha256(body).hexdigest()
    assert "private chain of thought" not in response.model_dump_json()


def test_sse_reader_handles_cr_only_lines_and_fragmented_initial_bom() -> None:
    content = '{"result":"ok"}'
    with _server() as (state, base_url):
        body = b"\xef\xbb\xbf" + _completion_stream(state.completion, newline=b"\r")
        state.completion_bytes = body
        state.response_fragments = [body[:1], body[1:2], body[2:19], body[19:]]
        response = _generate(_config(base_url))

    assert response.output == {"result": "ok"}
    assert response.finalAssistantResponse == content
    assert response.rawResponseSha256 == hashlib.sha256(body).hexdigest()


def test_sse_reader_rejects_long_unquoted_json_whitespace_with_exact_partial() -> None:
    content = '{"result":' + " " * 1_024
    first_half = content[: len(content) - 512]
    second_half = content[len(content) - 512 :]
    events = [
        _stream_chunk({"role": "assistant"}),
        _stream_chunk({"reasoning_content": "private chain of thought"}),
        _stream_chunk({"content": first_half}),
        _stream_chunk({"content": second_half}),
        _stream_chunk({}, finish_reason="stop"),
    ]
    with _server() as (state, base_url):
        state.completion_bytes = _sse_bytes(events)
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    retained = rejected.value.rejection
    assert retained.finalAssistantResponse == content
    assert retained.contentDiagnostic == "long-json-whitespace-run"
    assert retained.finishReason is None
    assert retained.tokenUsage is None
    assert retained.rawResponseSha256 == hashlib.sha256(state.response_bytes).hexdigest()
    assert "private chain of thought" not in retained.model_dump_json()


@pytest.mark.parametrize(
    "content",
    [
        '{"result":' + " " * 1_023 + '"ok"}',
        '{"result":"' + " " * 1_100 + '"}',
        '{"result":"escaped \\"' + " " * 1_100 + '\\" text"}',
    ],
)
def test_sse_whitespace_guard_only_counts_unquoted_json_whitespace(content: str) -> None:
    with _server() as (state, base_url):
        choice = state.completion["choices"]
        assert isinstance(choice, list) and isinstance(choice[0], dict)
        choice[0]["message"] = {"role": "assistant", "content": content}
        response = _generate(_config(base_url))

    assert response.output["result"]


def test_sse_reader_allows_one_post_finish_usage_event() -> None:
    events = [
        _stream_chunk({"role": "assistant"}),
        _stream_chunk({"content": '{"result":"ok"}'}),
        _stream_chunk({}, finish_reason="stop"),
        {
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "model": "local-model",
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        },
    ]
    with _server() as (state, base_url):
        state.completion_bytes = _sse_bytes(events)
        response = _generate(_config(base_url))

    assert response.output == {"result": "ok"}
    assert response.tokenUsage == GenerationTokenUsage(
        promptTokens=2, completionTokens=3, totalTokens=5
    )


def test_sse_reader_rejects_content_after_finish() -> None:
    events = [
        _stream_chunk({"role": "assistant"}),
        _stream_chunk({"content": '{"result":"ok"}'}),
        _stream_chunk({}, finish_reason="stop"),
        _stream_chunk({"content": "unexpected"}),
    ]
    with _server() as (state, base_url):
        state.completion_bytes = _sse_bytes(events)
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))

    assert raised.value.code == "BACKEND_FAILED"
    assert "choices" in raised.value.message


@pytest.mark.parametrize(
    "body",
    [
        _sse_bytes(
            [
                _stream_chunk({"role": "assistant"}),
                _stream_chunk({"content": '{"result":"ok"}'}),
                _stream_chunk({}, finish_reason="stop"),
            ],
            done=False,
        ),
        _sse_bytes(
            [
                _stream_chunk({"role": "assistant"}),
                _stream_chunk({"content": '{"result":"ok"}'}),
            ]
        ),
    ],
)
def test_sse_reader_requires_finish_and_done(body: bytes) -> None:
    with _server() as (state, base_url):
        state.completion_bytes = body
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))

    assert raised.value.code == "BACKEND_FAILED"


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (
            _stream_chunk({"role": "assistant"}, stream_id="changed-id"),
            "INTEGRITY_FAILED",
        ),
        (
            _stream_chunk({"role": "assistant"}, model="different-model"),
            "INTEGRITY_FAILED",
        ),
        (
            {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "model": "local-model",
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None},
                    {"index": 1, "delta": {}, "finish_reason": None},
                ],
            },
            "BACKEND_FAILED",
        ),
        (_stream_chunk({"role": "user"}), "BACKEND_FAILED"),
    ],
)
def test_sse_reader_rejects_inconsistent_protocol_identity(
    event: dict[str, object], code: str
) -> None:
    initial = _stream_chunk({"role": "assistant"})
    with _server() as (state, base_url):
        state.completion_bytes = _sse_bytes([initial, event])
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))

    assert raised.value.code == code


def test_malformed_optional_usage_is_ignored_without_masking_valid_output() -> None:
    with _server() as (state, base_url):
        state.completion["usage"] = {
            "prompt_tokens": True,
            "completion_tokens": "32",
            "total_tokens": -1,
            "completion_tokens_details": {
                "reasoning_tokens": 1_000_000_001,
                "reasoning_content": "must not be retained",
            },
        }
        response = _generate(_config(base_url))

    assert response.output == {"result": "ok"}
    assert response.tokenUsage is None
    assert "reasoning_content" not in response.model_dump_json()


def test_absent_optional_usage_is_accepted() -> None:
    with _server() as (state, base_url):
        state.completion.pop("usage")
        response = _generate(_config(base_url))

    assert response.output == {"result": "ok"}
    assert response.tokenUsage is None


def test_partial_valid_usage_retains_only_bounded_integer_counts() -> None:
    with _server() as (state, base_url):
        state.completion["usage"] = {
            "prompt_tokens": 9,
            "completion_tokens": "7",
            "total_tokens": 16,
            "completion_tokens_details": {"reasoning_tokens": None},
        }
        response = _generate(_config(base_url))

    assert response.tokenUsage == GenerationTokenUsage(promptTokens=9, totalTokens=16)
    assert response.tokenUsage.model_dump(mode="json") == {
        "promptTokens": 9,
        "totalTokens": 16,
    }


def test_diagnostics_are_optional_for_old_strict_cache_payloads() -> None:
    identity = EndpointModelIdentity(
        modelId="local-model",
        modelType="unknown",
        publisher=None,
        architecture=None,
        format=None,
        quantization=None,
        sizeBytes=None,
        maxContextLength=None,
        metadataSha256="0" * 64,
        immutableRevision=None,
        structuredOutput="verified-for-request",
    )
    old_response = {
        "model": identity.model_dump(mode="json"),
        "output": {"result": "ok"},
        "finishReason": "stop",
        "requestSha256": "1" * 64,
        "schemaSha256": "2" * 64,
        "rawResponseSha256": "3" * 64,
        "elapsedSeconds": 0.5,
        "finalAssistantResponse": '{"result":"ok"}',
    }
    old_rejection = {
        "message": "Generated structured output is not strict JSON",
        "requestSha256": "1" * 64,
        "rawResponseSha256": "3" * 64,
        "finalAssistantResponse": "{",
    }

    response = GenerationResponse.model_validate(old_response, strict=True)
    rejection = GenerationRejection.model_validate(old_rejection, strict=True)

    assert response.tokenUsage is None
    assert response.contentDiagnostic is None
    assert rejection.finishReason is None
    assert rejection.tokenUsage is None
    assert rejection.contentDiagnostic is None


@pytest.mark.parametrize(
    "usage",
    [
        {"promptTokens": -1},
        {"completionTokens": 1_000_000_001},
        {"totalTokens": True},
        {"reasoningTokens": 1, "unexpected": 2},
    ],
)
def test_token_usage_contract_is_strict_and_bounded(usage: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GenerationTokenUsage.model_validate(usage, strict=True)


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


def test_response_diagnostics_do_not_change_request_identity_or_trigger_discovery() -> None:
    with _server() as (state, base_url):
        config = _config(base_url)
        identity = discover_models(config)[0]
        state.counts.clear()
        first = _generate(config, observed_identity=identity)
        state.completion["usage"] = {
            "prompt_tokens": 121,
            "completion_tokens": 31,
            "total_tokens": 152,
        }
        second = _generate(config, observed_identity=identity)

    assert first.requestSha256 == second.requestSha256
    assert first.tokenUsage != second.tokenUsage
    assert state.counts == {"/v1/chat/completions": 2}


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
    ("update", "message", "code"),
    [
        ({"finish_reason": "length"}, "truncated", "OUTPUT_INVALID"),
        (
            {"finish_reason": "stop", "message": {"refusal": "cannot", "content": None}},
            "refused",
            "BACKEND_FAILED",
        ),
        (
            {"finish_reason": "stop", "message": {"content": '{"result":1}'}},
            "does not match",
            "OUTPUT_INVALID",
        ),
        (
            {
                "finish_reason": "stop",
                "message": {"content": '{"result":"a","result":"b"}'},
            },
            "strict JSON",
            "OUTPUT_INVALID",
        ),
        ({"finish_reason": {"unexpected": True}}, "finish reason", "BACKEND_FAILED"),
    ],
)
def test_generation_rejects_terminal_output_failures(
    update: dict[str, object], message: str, code: str
) -> None:
    with _server() as (state, base_url):
        choice = state.completion["choices"]
        assert isinstance(choice, list) and isinstance(choice[0], dict)
        choice[0].update(update)
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url))
    assert raised.value.code == code
    assert message in raised.value.message
    assert state.counts["/v1/chat/completions"] == 1


def test_unknown_finish_reason_is_bounded_to_generic_diagnostic() -> None:
    with _server() as (state, base_url):
        choice = state.completion["choices"]
        assert isinstance(choice, list) and isinstance(choice[0], dict)
        choice[0]["finish_reason"] = "provider-internal-" + "x" * 10_000
        with pytest.raises(GenerationRejected) as rejected:
            _generate(_config(base_url))

    assert rejected.value.rejection.finishReason == "unknown"
    assert "provider-internal" not in rejected.value.rejection.model_dump_json()


def test_generation_rejects_oversized_response_without_retry() -> None:
    with _server() as (state, base_url):
        state.completion_bytes = b"{" + b" " * 2048 + b"}"
        with pytest.raises(ModelError) as raised:
            _generate(_config(base_url, maxResponseBytes=1024))
    assert raised.value.code == "BACKEND_FAILED"
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


def test_generation_midstream_timeout_is_fatal_without_partial_rejection() -> None:
    role_event = _sse_bytes([_stream_chunk({"role": "assistant"})], done=False)
    remainder = _sse_bytes(
        [
            _stream_chunk({"content": '{"result":"ok"}'}),
            _stream_chunk({}, finish_reason="stop"),
        ]
    )
    with _server() as (state, base_url):
        state.response_fragments = [role_event, remainder]
        state.fragment_delay_seconds = 2
        started = time.monotonic()
        with pytest.raises(ModelError) as raised:
            _generate(LocalEndpointConfig(baseUrl=base_url, timeoutSeconds=1))
        elapsed = time.monotonic() - started

    assert type(raised.value) is ModelError
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
    assert raised.value.code == "INTEGRITY_FAILED"
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

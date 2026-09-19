"""Bounded local OpenAI-compatible endpoint access for automated curation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from types import FrameType
from typing import Annotated, Any, Literal, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import (  # type: ignore[import-untyped]
    SchemaError,
)
from jsonschema.exceptions import (
    ValidationError as JsonSchemaValidationError,
)
from pydantic import (
    Field,
    StrictFloat,
    StrictInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from ..contracts.base import (
    CliErrorCode,
    ContractModel,
    Digest,
    JsonValue,
    NonEmptyStr,
    NonNegativeFloat,
    NonNegativeInt,
    Omitted,
    UInt32,
    _reject_explicit_null,
    canonical_digest,
)
from ..contracts.inputs import ChatMessage
from ..errors import ModelError
from ..execution import _terminate_process_group, _worker_environment

_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_MAX_EXCHANGE_BYTES = 16 * 1024 * 1024
_MAX_DISCOVERED_MODELS = 1024
_READ_CHUNK_BYTES = 64 * 1024
_JSON_CONTENT_TYPES = {"application/json", "application/problem+json"}
type _SignalHandler = Callable[[int, FrameType | None], object] | int | None


class LocalEndpointConfig(ContractModel):
    """Validated limits and location for one local generation endpoint."""

    baseUrl: NonEmptyStr = "http://127.0.0.1:1234/v1"
    model: Omitted[NonEmptyStr] = Field(default=None, exclude_if=lambda value: value is None)
    timeoutSeconds: Annotated[StrictInt, Field(ge=1, le=600)] = 120
    maxTokens: Annotated[StrictInt, Field(ge=1, le=32_768)] = 2048
    temperature: Annotated[StrictFloat, Field(ge=0, le=2, allow_inf_nan=False)] = 0.3
    maxResponseBytes: Annotated[StrictInt, Field(ge=1024, le=16_777_216)] = 8_388_608
    structuredOutput: Literal["json-schema", "prompt"] = "json-schema"

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "model")

    @field_validator("baseUrl")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return _validated_base_url(value)


class EndpointModelIdentity(ContractModel):
    """Runtime-observed model identity; it is not a verified weight revision."""

    modelId: NonEmptyStr
    modelType: Literal["llm", "embedding", "unknown"]
    publisher: str | None
    architecture: str | None
    format: Literal["gguf", "mlx"] | None
    quantization: str | None
    sizeBytes: NonNegativeInt | None
    maxContextLength: NonNegativeInt | None
    metadataSha256: Digest
    immutableRevision: None = None
    structuredOutput: Literal["unknown", "verified-for-request"] = "unknown"


class GenerationResponse(ContractModel):
    """Locally validated structured output with bounded endpoint provenance."""

    model: EndpointModelIdentity
    output: dict[str, JsonValue]
    finishReason: Literal["stop"]
    requestSha256: Digest
    schemaSha256: Digest
    rawResponseSha256: Digest
    elapsedSeconds: NonNegativeFloat


class _GenerationRequest(ContractModel):
    config: LocalEndpointConfig
    modelId: NonEmptyStr
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=255)]
    schema_: dict[str, JsonValue] = Field(alias="schema")
    seed: UInt32


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise ModelError("NETWORK_FAILED", "Local endpoint redirect rejected")


def discover_models(config: LocalEndpointConfig) -> list[EndpointModelIdentity]:
    """List visible models without loading a model or running inference."""

    deadline = time.monotonic() + config.timeoutSeconds
    compatibility = _request_json(
        _endpoint_url(config.baseUrl, "/v1/models"),
        method="GET",
        body=None,
        max_bytes=config.maxResponseBytes,
        deadline=deadline,
    )
    entries = _compatibility_entries(compatibility)
    native = _native_models(config, deadline=deadline)
    identities = [_model_identity(entry, native) for entry in entries]
    ids = [identity.modelId for identity in identities]
    if len(ids) != len(set(ids)):
        raise ModelError("OUTPUT_INVALID", "Local endpoint returned duplicate model identifiers")
    return sorted(identities, key=lambda identity: identity.modelId)


def generate_json(
    config: LocalEndpointConfig,
    *,
    model_id: str,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
) -> GenerationResponse:
    """Generate one JSON object in an isolated child with a hard deadline."""

    try:
        request = _GenerationRequest.model_validate(
            {
                "config": config.model_dump(mode="json"),
                "modelId": model_id,
                "messages": [message.model_dump(mode="json") for message in messages],
                "schema": schema,
                "seed": seed,
            }
        )
        request_bytes = _canonical_bytes(request.model_dump(mode="json", by_alias=True))
    except (ValidationError, TypeError, ValueError) as error:
        raise ModelError("ARGUMENT_INVALID", "Local generation request is invalid") from error
    if len(request_bytes) > _MAX_REQUEST_BYTES:
        raise ModelError("ARGUMENT_INVALID", "Local generation request exceeds the size limit")

    argv = [sys.executable, "-m", "foliqant_model.curation.endpoint_worker"]
    process: subprocess.Popen[bytes] | None = None
    interrupted_during_spawn = False

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted_during_spawn
        if process is None:
            interrupted_during_spawn = True
            return
        raise _EndpointInterruption

    previous_handler = _install_sigterm_handler(handle_sigterm)
    try:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_worker_environment(),
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            if interrupted_during_spawn:
                raise _EndpointInterruption from error
            raise ModelError("BACKEND_FAILED", "Cannot start local endpoint worker") from error
        if interrupted_during_spawn:
            raise _EndpointInterruption
        try:
            output, _ = process.communicate(
                input=request_bytes + b"\n", timeout=config.timeoutSeconds
            )
        except subprocess.TimeoutExpired as error:
            _terminate_process_group(process)
            raise ModelError("TIMEOUT", "Local generation exceeded its deadline") from error
        if len(output) > _MAX_EXCHANGE_BYTES:
            raise ModelError(
                "OUTPUT_INVALID", "Local endpoint worker result exceeds the size limit"
            )
        if process.returncode != 0:
            raise ModelError("BACKEND_FAILED", "Local endpoint worker failed")
        return _parse_worker_result(output)
    except (KeyboardInterrupt, _EndpointInterruption) as error:
        if process is not None:
            _terminate_process_group(process)
        raise ModelError("INTERRUPTED", "Local generation was interrupted") from error
    finally:
        _restore_sigterm_handler(previous_handler)
        if process is not None and process.poll() is None:
            _terminate_process_group(process)


def _generate_json_direct(request: _GenerationRequest) -> GenerationResponse:
    """Execute one request inside the isolated endpoint worker."""

    started = time.monotonic()
    if request.config.model is not None and request.config.model != request.modelId:
        raise ModelError("CONFIG_INVALID", "Configured model does not match the requested model")
    identities = discover_models(request.config)
    identity = _select_model(request.config, identities)
    if identity.modelId != request.modelId:
        raise ModelError("CONFIG_INVALID", "Resolved model does not match the requested model")

    schema = cast(dict[str, object], request.schema_)
    _reject_external_schema_references(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ModelError("ARGUMENT_INVALID", "Structured output schema is invalid") from error

    try:
        payload = _generation_payload(
            request.config,
            model_id=request.modelId,
            messages=request.messages,
            schema=schema,
            seed=request.seed,
        )
        body = _canonical_bytes(payload)
        schema_bytes = _canonical_bytes(schema)
    except (TypeError, ValueError) as error:
        raise ModelError("ARGUMENT_INVALID", "Structured generation input is not JSON") from error
    if len(body) > _MAX_REQUEST_BYTES:
        raise ModelError("ARGUMENT_INVALID", "Local generation request exceeds the size limit")

    deadline = started + request.config.timeoutSeconds
    raw = _request_bytes(
        _endpoint_url(request.config.baseUrl, "/v1/chat/completions"),
        method="POST",
        body=body,
        max_bytes=request.config.maxResponseBytes,
        deadline=deadline,
    )
    envelope = _strict_json(raw, label="Local endpoint response")
    content = _completion_content(envelope, expected_model=request.modelId)
    generated = _strict_json(content.encode("utf-8"), label="Generated structured output")
    if not isinstance(generated, dict):
        raise ModelError("OUTPUT_INVALID", "Generated structured output must be an object")
    try:
        Draft202012Validator(schema).validate(generated)
    except JsonSchemaValidationError as error:
        raise ModelError("OUTPUT_INVALID", "Generated output does not match its schema") from error

    verified_identity = EndpointModelIdentity.model_validate(
        {**identity.model_dump(mode="json"), "structuredOutput": "verified-for-request"}
    )
    return GenerationResponse(
        model=verified_identity,
        output=cast(dict[str, JsonValue], generated),
        finishReason="stop",
        requestSha256=_generation_request_sha256(
            request.config,
            model_id=request.modelId,
            messages=request.messages,
            schema=schema,
            seed=request.seed,
        ),
        schemaSha256=hashlib.sha256(schema_bytes).hexdigest(),
        rawResponseSha256=hashlib.sha256(raw).hexdigest(),
        elapsedSeconds=time.monotonic() - started,
    )


def _generation_payload(
    config: LocalEndpointConfig,
    *,
    model_id: str,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
) -> dict[str, object]:
    effective_messages = _effective_messages(config, messages=messages, schema=schema)
    payload: dict[str, object] = {
        "model": model_id,
        "messages": [message.model_dump(mode="json") for message in effective_messages],
        "seed": seed,
        "temperature": config.temperature,
        "max_tokens": config.maxTokens,
        "stream": False,
    }
    if config.structuredOutput == "json-schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "foliqant_curation",
                "strict": True,
                "schema": schema,
            },
        }
    return payload


def _effective_messages(
    config: LocalEndpointConfig,
    *,
    messages: list[ChatMessage],
    schema: dict[str, object],
) -> list[ChatMessage]:
    if config.structuredOutput == "json-schema":
        return messages
    if not messages or messages[-1].role != "user":
        raise ModelError(
            "ARGUMENT_INVALID", "Prompt structured output requires a final user message"
        )
    instruction = (
        "\n\nStructured output requirement: Return only one strict JSON object that conforms "
        "exactly to the JSON Schema below. Do not include Markdown, prose, or reasoning outside "
        "the JSON object.\nJSON Schema:\n" + _canonical_bytes(schema).decode("utf-8")
    )
    try:
        final = ChatMessage(role="user", content=messages[-1].content + instruction)
    except ValidationError as error:
        raise ModelError(
            "ARGUMENT_INVALID", "Prompt structured output request is too large"
        ) from error
    return [*messages[:-1], final]


def _generation_request_sha256(
    config: LocalEndpointConfig,
    *,
    model_id: str,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
) -> str:
    """Hash the exact canonical endpoint request, including prompt-mode augmentation."""

    return canonical_digest(
        _generation_payload(
            config,
            model_id=model_id,
            messages=messages,
            schema=schema,
            seed=seed,
        )
    )


def _reject_external_schema_references(value: object) -> None:
    """Reject schema references that could trigger nonlocal retrieval."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(nested, str) or not nested.startswith("#")
            ):
                raise ModelError(
                    "ARGUMENT_INVALID", "Structured output schema contains an external reference"
                )
            _reject_external_schema_references(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_external_schema_references(nested)


def _select_model(
    config: LocalEndpointConfig, identities: list[EndpointModelIdentity]
) -> EndpointModelIdentity:
    """Resolve configuration selection without arbitrary first-model behavior."""

    candidates = [identity for identity in identities if identity.modelType != "embedding"]
    if config.model is None:
        if len(candidates) != 1:
            raise ModelError(
                "CONFIG_INVALID", "Configure a model because local model discovery is ambiguous"
            )
        return candidates[0]
    matches = [identity for identity in identities if identity.modelId == config.model]
    if len(matches) != 1 or matches[0].modelType == "embedding":
        raise ModelError("CONFIG_INVALID", "Configured model is not a generation candidate")
    return matches[0]


def _validated_base_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError("baseUrl is invalid") from error
    if parts.scheme != "http" or parts.username is not None or parts.password is not None:
        raise ValueError("baseUrl must use unauthenticated HTTP on loopback")
    if parts.query or parts.fragment or parts.path.rstrip("/") != "/v1" or port is None:
        raise ValueError("baseUrl must have an explicit port and the /v1 path")
    host = parts.hostname
    if host is None or "%" in host:
        raise ValueError("baseUrl must use a loopback host")
    if host.lower() == "localhost":
        host = "127.0.0.1"
    else:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError("baseUrl must use a numeric loopback address") from error
        if not address.is_loopback:
            raise ValueError("baseUrl must use a loopback address")
        host = address.compressed
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urlunsplit(SplitResult("http", netloc, "/v1", "", ""))


def _endpoint_url(base_url: str, path: str) -> str:
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _request_json(
    url: str,
    *,
    method: Literal["GET", "POST"],
    body: bytes | None,
    max_bytes: int,
    deadline: float,
) -> object:
    return _strict_json(
        _request_bytes(url, method=method, body=body, max_bytes=max_bytes, deadline=deadline),
        label="Local endpoint response",
    )


def _request_bytes(
    url: str,
    *,
    method: Literal["GET", "POST"],
    body: bytes | None,
    max_bytes: int,
    deadline: float,
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ModelError("TIMEOUT", "Local endpoint request exceeded its deadline")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener().open(request, timeout=remaining) as response:
            final_url = response.geturl()
            if final_url != url:
                raise ModelError("NETWORK_FAILED", "Local endpoint redirect rejected")
            encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise ModelError("OUTPUT_INVALID", "Local endpoint used unsupported encoding")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in _JSON_CONTENT_TYPES:
                raise ModelError("OUTPUT_INVALID", "Local endpoint response is not JSON")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as error:
                    raise ModelError(
                        "OUTPUT_INVALID", "Local endpoint response size is invalid"
                    ) from error
                if declared_size < 0 or declared_size > max_bytes:
                    raise ModelError(
                        "OUTPUT_INVALID", "Local endpoint response exceeds the size limit"
                    )
            chunks: list[bytes] = []
            received = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ModelError("TIMEOUT", "Local endpoint request exceeded its deadline")
                chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes - received + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > max_bytes:
                    raise ModelError(
                        "OUTPUT_INVALID", "Local endpoint response exceeds the size limit"
                    )
            return b"".join(chunks)
    except ModelError:
        raise
    except urllib.error.HTTPError as error:
        raise ModelError("NETWORK_FAILED", "Local endpoint returned an HTTP error") from error
    except TimeoutError as error:
        raise ModelError("TIMEOUT", "Local endpoint request timed out") from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise ModelError("TIMEOUT", "Local endpoint request timed out") from error
        raise ModelError("NETWORK_FAILED", "Cannot reach the local endpoint") from error
    except (ConnectionError, OSError) as error:
        raise ModelError("NETWORK_FAILED", "Cannot reach the local endpoint") from error


def _compatibility_entries(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise ModelError("OUTPUT_INVALID", "Local model discovery response is invalid")
    data = value["data"]
    if len(data) > _MAX_DISCOVERED_MODELS:
        raise ModelError("OUTPUT_INVALID", "Local model discovery returned too many models")
    entries: list[Mapping[str, object]] = []
    for item in data:
        if not isinstance(item, dict) or not _optional_text(item.get("id")):
            raise ModelError("OUTPUT_INVALID", "Local model discovery response is invalid")
        entries.append(item)
    return entries


def _native_models(config: LocalEndpointConfig, *, deadline: float) -> list[Mapping[str, object]]:
    try:
        value = _request_json(
            _endpoint_url(config.baseUrl, "/api/v1/models"),
            method="GET",
            body=None,
            max_bytes=config.maxResponseBytes,
            deadline=deadline,
        )
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            return []
        models = value["models"]
        if len(models) > _MAX_DISCOVERED_MODELS:
            return []
        return [item for item in models if isinstance(item, dict)]
    except ModelError:
        return []


def _model_identity(
    compatibility: Mapping[str, object], native_models: list[Mapping[str, object]]
) -> EndpointModelIdentity:
    model_id = cast(str, compatibility["id"])
    matches: list[Mapping[str, object]] = []
    for native in native_models:
        keys = {_optional_text(native.get("key"))}
        loaded = native.get("loaded_instances")
        if isinstance(loaded, list):
            keys.update(_optional_text(item.get("id")) for item in loaded if isinstance(item, dict))
        if model_id in keys:
            matches.append(native)
    native = matches[0] if len(matches) == 1 else {}
    model_type = native.get("type") if native.get("type") in {"llm", "embedding"} else "unknown"
    quantization_value = native.get("quantization")
    quantization = None
    if isinstance(quantization_value, dict):
        quantization = _optional_text(quantization_value.get("name"))
    elif isinstance(quantization_value, str):
        quantization = quantization_value or None
    format_value = native.get("format")
    model_format = format_value if format_value in {"gguf", "mlx"} else None
    publisher = _optional_text(native.get("publisher")) or _optional_text(
        compatibility.get("owned_by")
    )
    stable = {
        "architecture": _optional_text(native.get("architecture")),
        "format": model_format,
        "id": model_id,
        "maxContextLength": _optional_nonnegative_int(native.get("max_context_length")),
        "modelType": model_type,
        "publisher": publisher,
        "quantization": quantization,
        "sizeBytes": _optional_nonnegative_int(native.get("size_bytes")),
    }
    return EndpointModelIdentity.model_validate(
        {
            "modelId": model_id,
            "modelType": model_type,
            "publisher": publisher,
            "architecture": stable["architecture"],
            "format": model_format,
            "quantization": quantization,
            "sizeBytes": stable["sizeBytes"],
            "maxContextLength": stable["maxContextLength"],
            "metadataSha256": canonical_digest(stable),
            "immutableRevision": None,
            "structuredOutput": "unknown",
        }
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_nonnegative_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _completion_content(value: object, *, expected_model: str) -> str:
    if not isinstance(value, dict) or value.get("model") != expected_model:
        raise ModelError("OUTPUT_INVALID", "Local endpoint returned a different model identity")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ModelError("OUTPUT_INVALID", "Local endpoint completion envelope is invalid")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise ModelError("OUTPUT_INVALID", "Local endpoint truncated the generated output")
    if finish_reason != "stop":
        raise ModelError("OUTPUT_INVALID", "Local endpoint did not complete structured output")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelError("OUTPUT_INVALID", "Local endpoint completion message is invalid")
    refusal = message.get("refusal")
    if refusal is not None and refusal != "":
        raise ModelError("OUTPUT_INVALID", "Local endpoint refused structured generation")
    tool_calls = message.get("tool_calls")
    if tool_calls is not None and tool_calls != []:
        raise ModelError("OUTPUT_INVALID", "Local endpoint returned unsupported tool calls")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise ModelError("OUTPUT_INVALID", "Local endpoint returned no structured output")
    return content


def _strict_json(data: bytes, *, label: str) -> object:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite number")

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ModelError("OUTPUT_INVALID", f"{label} is not strict JSON") from error


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _parse_worker_result(data: bytes) -> GenerationResponse:
    value = _strict_json(data, label="Local endpoint worker result")
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ModelError("OUTPUT_INVALID", "Local endpoint worker result is invalid")
    if value["ok"] is True:
        try:
            return GenerationResponse.model_validate(value.get("response"))
        except ValidationError as error:
            raise ModelError("OUTPUT_INVALID", "Local endpoint worker result is invalid") from error
    error_value = value.get("error")
    if not isinstance(error_value, dict):
        raise ModelError("OUTPUT_INVALID", "Local endpoint worker failure is invalid")
    code = error_value.get("code")
    message = error_value.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise ModelError("OUTPUT_INVALID", "Local endpoint worker failure is invalid")
    try:
        validated_code: CliErrorCode = TypeAdapter(CliErrorCode).validate_python(code)
        candidate = ModelError(validated_code, message)
        _ = candidate.exit_code
    except (KeyError, ValidationError) as error:
        raise ModelError("OUTPUT_INVALID", "Local endpoint worker failure is invalid") from error
    raise candidate


class _EndpointInterruption(BaseException):
    """Internal control flow for SIGTERM while a generation child is active."""


def _install_sigterm_handler(
    handler: Callable[[int, FrameType | None], object],
) -> _SignalHandler:
    if threading.current_thread() is not threading.main_thread():
        return None
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handler)
    return previous


def _restore_sigterm_handler(previous: _SignalHandler) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def _worker_main() -> int:
    """Read one private-pipe request and emit one redacted result envelope."""

    try:
        data = sys.stdin.buffer.read(_MAX_EXCHANGE_BYTES + 1)
        if len(data) > _MAX_EXCHANGE_BYTES:
            raise ModelError("ARGUMENT_INVALID", "Local endpoint worker request is too large")
        value = _strict_json(data, label="Local endpoint worker request")
        request = _GenerationRequest.model_validate(value)
        response = _generate_json_direct(request)
        result: dict[str, object] = {
            "ok": True,
            "response": response.model_dump(mode="json"),
        }
    except ModelError as error:
        result = {"ok": False, "error": {"code": error.code, "message": error.message}}
    except ValidationError:
        result = {
            "ok": False,
            "error": {
                "code": "ARGUMENT_INVALID",
                "message": "Local endpoint worker request is invalid",
            },
        }
    except BaseException:
        result = {
            "ok": False,
            "error": {"code": "INTERNAL_ERROR", "message": "Local endpoint worker failed"},
        }
    output = _canonical_bytes(result) + b"\n"
    if len(output) > _MAX_EXCHANGE_BYTES:
        output = (
            _canonical_bytes(
                {
                    "ok": False,
                    "error": {
                        "code": "OUTPUT_INVALID",
                        "message": "Local endpoint worker result exceeds the size limit",
                    },
                }
            )
            + b"\n"
        )
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0

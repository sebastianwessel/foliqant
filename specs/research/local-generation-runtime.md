# Local structured-generation runtime

Date: 2026-09-19. Status: researched design; no endpoint adapter or generation run is implemented.

## Decision

Add a small Python adapter for local, schema-constrained generation. Use the standard library HTTP stack plus the already pinned Pydantic and `jsonschema` packages. Do not add the OpenAI SDK, `requests`, or `httpx` for this slice. The adapter targets LM Studio's OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions`; its output remains unreviewed model output until a later curation and review stage accepts it.

The initial configuration is local only:

- `baseUrl`: `http://127.0.0.1:1234/v1`
- `model`: optional; omission is accepted only when discovery yields exactly one generation candidate
- `timeoutSeconds`: `120`
- `maxTokens`: `2048`
- `temperature`: `0.3`
- `maxResponseBytes`: `8 MiB`

There is no cloud fallback, implicit model download/load, hidden retry, streaming, tool call, or automatic data publication. A runner above this adapter owns job budgets, caching, retries, cancellation isolation, and review state.

## Existing conventions to retain

The repository uses CPython 3.12, strict Pydantic contract objects, canonical JSON SHA-256 digests, redacted `ModelError` failures, bounded reads, monotonic deadlines, and explicit process-group cancellation for isolated workers. Public failures do not contain customer text, prompts, generated content, credentials, response bodies, or arbitrary backend exception text. The new adapter should follow those conventions.

`ChatMessage` already bounds one message to 1 MiB. The adapter must also bound the canonical serialized request, for example at 8 MiB, before opening a connection. External LM Studio envelopes may gain fields, so parse the required wire fields deliberately and normalize them into a closed Foliqant contract instead of treating the provider response as a public contract.

## What LM Studio currently exposes

LM Studio documents `/v1/models` as an OpenAI-compatible discovery endpoint. It returns models visible to the server and may include all downloaded models when just-in-time loading is enabled. Discovery therefore does not mean a model is already loaded, is an LLM, or can satisfy this request. [OpenAI-compatible model listing](https://lmstudio.ai/docs/developer/openai-compat/models)

The native `GET /api/v1/models` endpoint exposes richer metadata: model type, key, format, quantization, size, maximum context, loaded instances and their context configuration, plus vision, tool-use and reasoning capabilities. It does not document an immutable source revision or weight digest, and its capabilities object does not advertise structured JSON output. Query it only as same-origin, read-only enrichment; `/v1/models` remains the compatibility discovery check. Failure of the enrichment endpoint must not invent metadata. [Native model listing](https://lmstudio.ai/docs/developer/rest/list)

LM Studio documents JSON Schema output through `response_format.type=json_schema` on `/v1/chat/completions`. The JSON value is returned as a string in `choices[0].message.content`. Its documentation warns that not every model can produce structured output. The metadata endpoints cannot prove this capability. [Structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output), [chat completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)

No inference probe was run during this research. `http://127.0.0.1:1234/v1/models` was not listening at the time of the read-only check. When generation is authorized, the first real schema-constrained request should validate that exact runtime, model and schema combination. A separate synthetic probe spends inference and still cannot prove support for every schema.

LM Studio defaults its CLI server bind to `127.0.0.1`; it warns that other binds expose the server beyond localhost and recommends authentication. Keep the adapter loopback-only until remote endpoint authentication and transport security receive a separate design. [server start](https://lmstudio.ai/docs/cli/serve/server-start), [server settings](https://lmstudio.ai/docs/developer/core/server/settings)

## Typed surface

Place the adapter under `model/src/foliqant_model/curation/endpoint.py`. A name such as `EndpointModelIdentity` is clearer than `ModelIdentity`, because `contracts.details.ModelIdentity` already denotes verified artifact files and tokenizer/template hashes.

```python
JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

class LocalEndpointConfig(ContractModel):
    baseUrl: str = "http://127.0.0.1:1234/v1"
    model: str | None = None
    timeoutSeconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 120
    maxTokens: Annotated[StrictInt, Field(ge=1, le=32768)] = 2048
    temperature: Annotated[StrictFloat, Field(ge=0, le=2, allow_inf_nan=False)] = 0.3
    maxResponseBytes: Annotated[StrictInt, Field(ge=1024, le=64 * 1024 * 1024)] = 8 * 1024 * 1024

class EndpointModelIdentity(ContractModel):
    requestedModelId: NonEmptyStr
    returnedModelId: NonEmptyStr | None
    modelKey: NonEmptyStr | None
    modelType: Literal["llm", "embedding", "unknown"]
    format: Literal["gguf", "mlx"] | None
    quantization: NonEmptyStr | None
    loadedInstanceIds: list[NonEmptyStr]
    discoveryMetadataSha256: Digest
    immutableRevision: None = None
    structuredOutput: Literal["unknown", "verified-for-request"]

class GenerationResponse(ContractModel):
    model: EndpointModelIdentity
    output: dict[str, JsonValue]
    finishReason: Literal["stop"]
    requestSha256: Digest
    schemaSha256: Digest
    rawResponseSha256: Digest
    elapsedSeconds: NonNegativeFloat


def discover_models(config: LocalEndpointConfig) -> list[EndpointModelIdentity]: ...

def generate_json(
    config: LocalEndpointConfig,
    *,
    model_id: str,
    messages: list[ChatMessage],
    schema: dict[str, object],
    seed: int,
) -> GenerationResponse: ...
```

If implementation retains the shorter `ModelIdentity` name within the `curation` package, it must not be re-exported under the same public name as the artifact contract. Reported token counters may be retained in private diagnostics, but they are endpoint claims and must not be treated as independently verified provenance or accounting.

## Discovery and identity

Validate the base URL before any I/O. Accept only `http`, a literal loopback IP (`127.0.0.0/8` or `::1`), an explicit valid port, and the exact `/v1` base path. Reject user information, query, fragment, encoded host tricks and hostnames, including `localhost`; a literal address avoids DNS and rebinding ambiguity. Endpoint paths are constants and are never supplied by model data.

Call `/v1/models` with a short bounded GET. The same-origin `/api/v1/models` request may enrich matching identifiers. Do not call load, download, chat, responses, MCP, or other management endpoints during discovery. If `config.model` is absent, require exactly one candidate after excluding a model explicitly identified by native metadata as an embedding model. Ambiguity is a configuration error, not a reason to pick the first result.

A server model ID, returned model field, native key, quantization label, or digest of discovery JSON is not an immutable artifact revision. `discoveryMetadataSha256` identifies the normalized metadata observed for the call and detects some runtime changes; it does not prove weight bytes. Durable provenance must later bind the selected endpoint model to a verified Foliqant artifact ID or an independently recorded weight digest. Until then, describe identity as runtime-observed and do not claim reproducibility across reloads or machines.

## Request and response rules

Validate the supplied schema with `Draft202012Validator.check_schema`. Canonically encode this request with `allow_nan=False`, `sort_keys=True`, compact separators and UTF-8. Send `stream=false`, the explicit model ID, messages, seed, configured sampling limits, and:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "foliqant_curation",
      "strict": true,
      "schema": {}
    }
  }
}
```

Do not silently rewrite the caller's schema. A schema feature accepted for one request does not establish support for every JSON Schema keyword.

Require an HTTP success with JSON content and exactly one choice. A non-null/nonempty `message.refusal`, any tool call, missing content, multiple choices, or an unknown envelope is an explicit output failure. `finish_reason=length` is truncation and must never be parsed as a valid partial result. Only `finish_reason=stop` proceeds.

Parse `message.content` as strict JSON: reject duplicate object keys, `NaN`, infinities and trailing data. Require a JSON object and validate it locally against the exact supplied schema. Endpoint-side constrained decoding is defense in depth, not a substitute for local validation. Hash the exact bounded response bytes before parsing, but never log or place those bytes in an error message.

Map invalid configuration/schema to `ARGUMENT_INVALID`, connection and redacted non-success HTTP failures to `NETWORK_FAILED`, blocking expiration to `TIMEOUT`, and oversized, refused, truncated, malformed or schema-invalid responses to `OUTPUT_INVALID`. The messages must identify the failure class without response content. No credentials are needed for the default local server; a later authentication option must use a secret source and exclude tokens from model dumps, cache keys, exceptions and logs.

## HTTP, retries and cancellation

Construct a dedicated `urllib` opener with `ProxyHandler({})`; environment proxy variables must not reroute local data. Install a redirect handler that rejects every 3xx. Send `Accept-Encoding: identity`, reject an encoded response, reject an oversized `Content-Length`, and read no more than `maxResponseBytes + 1` bytes. Close every response promptly.

`urllib` timeouts bound individual blocking operations; they are not a perfect hard wall-clock deadline across several reads. Check a monotonic deadline between bounded reads. For reliable cancellation and a strict overall job deadline, the runner should execute generation in an isolated child and terminate its process group using the repository's existing worker pattern. Closing the client connection is best-effort cancellation of server work, not rollback.

The adapter performs no automatic retry. A failed POST may have reached the server even when the client did not receive a response, and LM Studio does not document an idempotency key for chat completions. The runner may make a bounded new attempt only under its remaining job budget and must record the attempt separately. Validation failures, refusal, truncation and other completed responses are not transient transport retries. A 429 or selected 5xx response may be classified as retryable advice to the runner, but must not cause a hidden second request.

## Cache and provenance boundary

The runner, not the endpoint adapter, owns caching. Its cache key should be the canonical digest of:

- cache contract version and normalized endpoint origin;
- selected runtime model identity and verified artifact ID when available;
- normalized discovery metadata digest;
- exact messages, schema and schema digest;
- seed, temperature, token limit and other generation parameters;
- adapter behavior version.

Never include an authorization token. Persist only locally schema-validated terminal successes in the reusable result cache. A cache hit must retain the original generation provenance and add cache-access metadata rather than pretending a new inference occurred. Without an immutable model artifact identity and relevant runtime version, a durable cross-run cache cannot claim bit-reproducible generation; scope it accordingly and invalidate it when discovery metadata changes.

Each generated candidate must remain distinguishable from a human-reviewed or accepted record. Record attempt ID, request/schema digests, runtime-observed model identity, configured artifact identity when later available, response digest, local validation result, timestamps and cache source. Model-authored statements and endpoint-reported counters are content or diagnostics, never trusted provenance.

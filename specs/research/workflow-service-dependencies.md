# Workflow service dependency and API research

Date: 2026-09-21. Status: dependency research for
[specification 11](../11-workflow-service.md), not an implementation contract or
provider-conformance claim. The service lock and specification remain authoritative.
No endpoint was contacted while preparing this note.

## Recommendation

Use `pydantic-ai-slim` with only the selected provider extras. Keep the official
MCP SDK behind Foliqant's own MCP port and adapt discovered tools with
`Tool.from_schema`. Do not add PydanticAI's MCP integration, FastMCP, Logfire,
an agent framework, evaluation packages, UI packages, realtime packages, or
training dependencies to the service runtime.

This gives the service a small provider abstraction without transferring
workflow control, authorization, retries, idempotency, validation, or audit to
PydanticAI. Provider and MCP SDK objects belong in adapters; the workflow core
continues to depend on typed ports.

## Frozen dependency surface

The following versions are the versions currently pinned by
`service/pyproject.toml` and resolved by `service/uv.lock`. “Transitive” means
runtime code may import the SDK to configure a client, but the provider extra is
the direct dependency declaration. Any such import is coupled to the locked SDK
version and must be covered by adapter tests.

| Concern | Version | Installation role | Primary reference |
|---|---:|---|---|
| Python | `>=3.12,<3.13` | Service runtime | [Python 3.12 documentation](https://docs.python.org/3.12/) |
| Pydantic | `2.13.5` | Strict configuration and boundary models | [PyPI](https://pypi.org/project/pydantic/2.13.5/) |
| PydanticAI slim | `2.46.0` | Agent/model adapter only | [PyPI](https://pypi.org/project/pydantic-ai-slim/2.46.0/) |
| JSON Schema | `jsonschema==4.26.0` | Independent schema and value validation | [PyPI](https://pypi.org/project/jsonschema/4.26.0/) |
| Schema references | `referencing==0.37.0` | Closed in-memory registry; no runtime network or filesystem retrieval | [PyPI](https://pypi.org/project/referencing/0.37.0/) |
| YAML | `PyYAML==6.0.3` | Authoring input | [PyPI](https://pypi.org/project/PyYAML/6.0.3/) |
| Environment files | `python-dotenv==1.2.3` | Local configuration only | [PyPI](https://pypi.org/project/python-dotenv/1.2.3/) |
| OpenAI extra | `pydantic-ai-slim[openai]==2.46.0` | OpenAI, Azure OpenAI, and explicitly profiled OpenAI-compatible endpoints | [PydanticAI OpenAI models](https://pydantic.dev/docs/ai/models/openai/) |
| OpenAI SDK | `openai==3.16.2` | Transitive, locked client | [PyPI](https://pypi.org/project/openai/3.16.2/) |
| Anthropic extra | `pydantic-ai-slim[anthropic]==2.46.0` | Anthropic Messages adapter | [PydanticAI Anthropic models](https://pydantic.dev/docs/ai/models/anthropic/) |
| Anthropic SDK | `anthropic==1.7.0` | Transitive, locked client using `httpx2` | [PyPI](https://pypi.org/project/anthropic/1.7.0/) |
| Bedrock extra | `pydantic-ai-slim[bedrock]==2.46.0` | Bedrock Converse adapter | [PydanticAI Bedrock models](https://pydantic.dev/docs/ai/models/bedrock/) |
| AWS SDK | `boto3==1.43.98`, `botocore==1.43.98` | Transitive, locked Bedrock client | [Boto3 Bedrock Runtime](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime.html) |
| Azure identity | `azure-identity==1.25.3` | Optional Entra ID/managed identity; unnecessary for API-key-only Azure OpenAI | [PyPI](https://pypi.org/project/azure-identity/1.25.3/) |
| MCP SDK | `mcp==2.2.0` | Official low-level/high-level client behind the Foliqant port | [PyPI](https://pypi.org/project/mcp/2.2.0/) |
| MCP HTTP client | `httpx2==2.13.0` | MCP and Anthropic HTTP implementation | [PyPI](https://pypi.org/project/httpx2/2.13.0/) |
| MCP crypto | `cryptography==50.0.1` | MCP HTTP authentication support | [PyPI](https://pypi.org/project/cryptography/50.0.1/) |
| OTel | `opentelemetry-api==1.44.0`, `opentelemetry-sdk==1.44.0`, `opentelemetry-exporter-otlp-proto-http==1.44.0` | Optional telemetry adapter | [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/) |

The HTTP, Redis, PostgreSQL, and development groups are already isolated in
their own extras and are outside the model/MCP adapter. The lock must remain the
source of exact transitive versions; avoid a second hand-maintained provider SDK
pin list in runtime code.

## Dynamic output contract

PydanticAI 2.46.0 exposes these relevant APIs:

```python
from jsonschema.validators import validator_for
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.output import StructuredDict

validator_cls = validator_for(schema)
validator_cls.check_schema(schema)
validator = validator_cls(schema)

output_model = StructuredDict(schema, name=schema_name, description=description)
output_type = NativeOutput(
    output_model,
    name=schema_name,
    description=description,
    strict=True,
)

result = await agent.run(
    prompt,
    output_type=output_type,
    retries=0,
    usage_limits=limits,
)
validator.validate(result.output)
```

`StructuredDict(json_schema, name=None, description=None)` creates a dynamic
output type, and `NativeOutput(..., strict=True)` requests provider-native JSON
Schema output. A plain `str` output type is the text-output path. Native output
must be enabled only by a tested profile capability: it varies by provider,
API flavor, model, and sometimes deployment. A profile that cannot combine
native output with tools needs two bounded phases, a tool phase followed by a
schema-output phase. Never silently fall back to prompted JSON.

Two skipped-validation behaviors require an independent validator:

- `StructuredDict` deliberately trusts its schema and returns the received
  dictionary without validating its values.
- `Tool.from_schema` deliberately trusts its supplied input schema.

Validate every schema once during compilation with `validator_for()` and
`check_schema()`. Validate final output again at the Foliqant boundary. These
checks remain necessary even when a provider advertises strict structured
output. See the [PydanticAI output documentation](https://pydantic.dev/docs/ai/core-concepts/output/)
and [`jsonschema` validation API](https://python-jsonschema.readthedocs.io/en/stable/validate/).

PydanticAI defaults semantic tool/output retries to one. The workflow owns the
attempt ledger, so create agents/runs with `retries=0` and make every additional
model attempt an explicit workflow transition. Provider transport retries must
also be disabled as described below.

## MCP client and dynamic tools

MCP SDK 2.2.0 supports the 2026-07-28 MCP specification and offers stdio and
Streamable HTTP clients. Use the official high-level `Client` directly rather
than PydanticAI's MCP toolset:

```python
from mcp import Client, StdioServerParameters

http_client = Client("https://tools.example/mcp", read_timeout_seconds=30)
stdio_client = Client(
    StdioServerParameters(
        command="trusted-binary",
        args=["serve"],
        env={"EXPLICIT": "value"},
        cwd="/trusted/path",
    )
)

async with http_client as client:
    cursor = None
    while True:
        page = await client.list_tools(cursor=cursor)
        # Compile page.tools after policy filtering and schema validation.
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    call = await client.call_tool(tool_name, arguments)
    if call.is_error:
        raise ToolExecutionError(...)
```

The client is a one-use async context and cannot be re-entered after exit.
`list_tools()` is paginated. A normal MCP tool failure is generally represented
by `CallToolResult.is_error`; JSON-RPC failures raise `MCPError`. Treat both as
failed executions and validate `structured_content` or decoded text before it
enters workflow state. See the [MCP Python client documentation](https://py.sdk.modelcontextprotocol.io/client/).

Compile an allowed MCP tool to PydanticAI with the exact 2.46 API:

```python
from typing import Any

from pydantic_ai import RunContext, Tool

async def invoke(ctx: RunContext[Dependencies], **arguments: Any) -> Any:
    validator.validate(arguments)
    # The port repeats authorization, argument validation, deadline,
    # idempotency and audit checks immediately before the side effect.
    return await ctx.deps.mcp.call_tool(binding, arguments)

tool = Tool.from_schema(
    invoke,
    name=public_name,
    description=telemetry_safe_description,
    json_schema=input_schema,
    takes_ctx=True,
    sequential=effectful,
    args_validator=optional_fast_validator,
)
```

`from_schema` calls the function with keyword arguments. It has no authorization,
approval, idempotency, timeout, strictness, or per-tool retry contract. Its
`sequential=True` flag only prevents concurrent execution with other sequential
tools from the same model response. PydanticAI otherwise schedules multiple tool
calls concurrently. Cross-run serialization, effect classification, approval,
and replay protection remain in the MCP port and durable execution ledger. See
the [tool API](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/) and
[advanced tool execution documentation](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/).

Never treat MCP annotations or provider-managed tools as authorization. Bind a
client/session only to one equivalent credential, tenant, principal, and policy
scope. A run-scoped session may reuse a connection; an authenticated session
must not be shared across identities. Own stdio child-process lifetime explicitly.
Use Streamable HTTP for new remote integrations; SSE is a legacy compatibility
transport.

## Provider construction

Select an explicit provider and API class. Do not infer API flavor from a model
name and do not poll a provider's `/models` endpoint during compilation.

```python
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

client = AsyncOpenAI(api_key=secret, base_url=base_url, max_retries=0)
provider = OpenAIProvider(openai_client=client)
chat_model = OpenAIChatModel(model_id, provider=provider, profile=profile)
responses_model = OpenAIResponsesModel(model_id, provider=provider, profile=profile)
```

Use `OpenAIChatModel` for Chat Completions and explicitly certified local or
third-party compatible endpoints. Use `OpenAIResponsesModel` for Responses.
The compatibility profile is configuration, not a capability claim derived from
the endpoint URL.

```python
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.azure import AzureProvider

provider = AzureProvider(
    azure_endpoint=endpoint,
    api_version=api_version,
    api_key=secret,
)
model = OpenAIResponsesModel(deployment_name, provider=provider)
```

`AzureProvider` targets Azure OpenAI APIs. It is not blanket support for every
model and API in the Azure AI Foundry catalog. Choose Chat or Responses explicitly
and certify features per Azure deployment. `azure-identity` is needed only for
the selected Entra/managed-identity credential path.

```python
from anthropic import AsyncAnthropic
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

client = AsyncAnthropic(api_key=secret, max_retries=0)
model = AnthropicModel(
    model_id,
    provider=AnthropicProvider(anthropic_client=client),
)
```

Anthropic SDK 1.x uses `httpx2`; do not supply a legacy `httpx.AsyncClient`.

```python
import boto3
from botocore.config import Config
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

client = boto3.client(
    "bedrock-runtime",
    region_name=region,
    config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
)
model = BedrockConverseModel(
    model_id,
    provider=BedrockProvider(bedrock_client=client),
)
```

`total_max_attempts=1` means one total botocore attempt. PydanticAI 2.46 runs
the synchronous Bedrock call and streaming iterator through AnyIO's worker-thread
facility, so it does not directly block the event loop. The service must still
bound both model concurrency and worker-thread demand.

Keep provider clients in the application lifespan and close async clients on
shutdown. Never mutate a shared provider/model instance with request identity,
credentials, tenant state, or policy.

## Native settings compiler

Compile options into the TypedDict for the selected API class:

| Adapter | Settings type | Native extension namespace |
|---|---|---|
| OpenAI Chat | `OpenAIChatModelSettings` | documented `openai_*` fields and `extra_body` |
| OpenAI Responses | `OpenAIResponsesModelSettings` | documented Responses-specific `openai_*` fields and `extra_body` |
| Azure OpenAI | The selected OpenAI Chat or Responses settings type | same API-class settings, then deployment capability checks |
| Anthropic | `AnthropicModelSettings` | documented `anthropic_*` fields and `extra_body` |
| Bedrock Converse | `BedrockModelSettings` | documented `bedrock_*` fields; opaque model-native values only under `bedrock_additional_model_requests_fields` |

Common `ModelSettings` includes `max_tokens`, `temperature`, `top_p`, `top_k`,
`stop_sequences`, `timeout`, `parallel_tool_calls`, `tool_choice`, `thinking`,
`service_tier`, `extra_headers`, and `extra_body`, but inheritance does not prove
that a provider transmits or supports a key. Consult the provider settings
documentation and the Foliqant capability profile. Omit an option unless it is
configured and supported; there is no universal temperature, reasoning, or token
default. See the [model settings API](https://pydantic.dev/docs/ai/api/pydantic-ai/settings/).
The current provider documentation describes `extra_body` forwarding for OpenAI
and Anthropic; it does not establish that contract for Bedrock Converse. Use
`bedrock_additional_model_requests_fields` for documented Bedrock model-native
extensions.

Examples of typed native controls include OpenAI `openai_reasoning_effort`,
`openai_reasoning_summary`, `openai_reasoning_mode`, `openai_text_verbosity`, and
`openai_store`; Anthropic `anthropic_thinking`, `anthropic_effort`,
`anthropic_metadata`, and `anthropic_service_tier`; and Bedrock
`bedrock_guardrail_config`, `bedrock_request_metadata`,
`bedrock_performance_configuration`, and
`bedrock_additional_model_requests_fields`. The exact allowed set is tied to the
locked PydanticAI version and selected API class.

Native passthrough must use path-aware allowlists. Core-owned fields cannot be
overridden through typed settings, `extra_body`, headers, or nested Bedrock
fields. Reserve at least provider/model/deployment, endpoint and authentication,
messages/input/instructions, tool definitions and choice, response/output schema,
streaming, retries and deadlines, storage/privacy controls, and usage collection.
For Bedrock also reserve outer Converse fields such as `modelId`, `messages`,
`system`, `toolConfig`, `inferenceConfig`, `guardrailConfig`, prompt variables,
request metadata, and additional response paths. Reject collisions and unknown
top-level keys; do not recursively delete them and continue.

Tool choice supports `none`, `auto`, `required`, a list of allowed tool names,
and PydanticAI's `ToolOrOutput`. A static required choice can prevent the model
from returning its final answer. For “at least one tool, then final output”, set
required/specific choice for the first permitted round, relax only according to
the explicit profile in later rounds, and enforce the durable postcondition that
at least one allowed tool completed successfully. Anthropic thinking is
incompatible with required/specific tool choice. Fail closed instead of silently
changing either feature.

## Attempts, usage, and concurrency

- `AsyncOpenAI` and `AsyncAnthropic` default to two SDK retries, which can turn
  one workflow attempt into three network attempts. Construct the SDK clients
  with `max_retries=0`.
- Set PydanticAI `retries=0`; its retries are semantic output/tool correction
  attempts and belong in the workflow attempt ledger.
- Pass explicit `UsageLimits`. In 2.46.0 the constructor supports
  `request_limit` (default `50`), `tool_calls_limit`, input/output/total token
  limits, per-request input limits, cost limits, and optional pre-request token
  counting. Most token limits are observed after a response and are not a hard
  guarantee against provider overrun.
- Read `result.usage`, a `RunUsage` property containing requests, tool calls,
  input/output tokens, cache read/write tokens, audio token fields, details, and
  best-effort cost. Preserve unavailable provider measurements as unavailable;
  never synthesize zero.
- `ConcurrencyLimitedModel(model, ConcurrencyLimiter(max_running,
  max_queued=...))` can bound in-process model requests. The workflow admission
  controller, tenant limits, execution leases, and tool limits remain authoritative.
- PydanticAI can run tool calls from one response concurrently. Use
  `sequential=True` for effectful adapters plus a separate global/per-tenant
  bound. Avoid unbounded `gather` and a fresh event loop per request.

Timeout/cancellation is cooperative. Cancelling an awaiting coroutine does not
prove that a provider request or external side effect did not occur. Record an
idempotency key before execution and reconcile uncertain outcomes. Never hold a
database transaction open while awaiting a model or MCP server.

## OpenTelemetry defaults and privacy

The safe baseline uses the ordinary OTel SDK and exporter without Logfire:

```python
from pydantic_ai.models.instrumented import (
    InstrumentationSettings,
    InstrumentedModel,
)

settings = InstrumentationSettings(
    tracer_provider=tracer_provider,
    meter_provider=meter_provider,
    include_content=False,
    include_binary_content=False,
    include_model_request_parameters=False,
    version=6,
    use_aggregated_usage_attribute_names=True,
)
model = InstrumentedModel(model, options=settings)
```

The 2.46.0 defaults are unsafe for this service: all three `include_*` flags
default to `True`, and instrumentation schema version 5 is the default.
Even with `include_model_request_parameters=False`, PydanticAI always emits
`gen_ai.tool.definitions`; that attribute contains each tool's name,
description, and argument schema. Tool descriptions and schemas therefore need
to be telemetry-safe, or the telemetry adapter/export processor must remove the
attribute. Raw prompts, model responses, tool arguments, tool results, and
provider diagnostics remain private by default. See the
[instrumented model API](https://pydantic.dev/docs/ai/api/models/instrumented/).

The OpenTelemetry GenAI semantic conventions are still marked Development.
Keep stable Foliqant execution, workflow revision, tenant-safe correlation, and
outcome attributes in the service's own versioned telemetry contract rather
than making the public contract depend on experimental semantic-convention keys.
[OpenTelemetry GenAI conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

## Offline compatibility smoke

The installed `service/.venv` was inspected without opening a network connection.
It reported the exact versions listed above and successfully:

- imported PydanticAI, Pydantic, `jsonschema`, MCP, all four provider dependency
  families, Azure Identity, and the OTel API/SDK/exporter;
- checked a JSON Schema with `validator_for(...).check_schema(...)`, created a
  `StructuredDict`, `NativeOutput(strict=True)`, and `Tool.from_schema`, and
  confirmed that the compiled tool retained the supplied schema;
- constructed `OpenAIChatModel`, `OpenAIResponsesModel`, and `AnthropicModel`
  with inert custom clients configured with `max_retries=0`;
- constructed HTTP and stdio MCP `Client` objects without entering their async
  contexts; and
- constructed the botocore retry configuration with
  `total_max_attempts=1`.

Signature inspection also confirmed the APIs shown in this note, MCP pagination,
PydanticAI's skipped dynamic-schema validation, and the Bedrock AnyIO worker-thread
bridge. This is import and construction compatibility only. It does not establish
authentication, endpoint reachability, schema enforcement, tool-choice behavior,
streaming behavior, usage accuracy, error mapping, or production support for any
specific provider/model/deployment. Those require adapter contract tests and
opt-in endpoint conformance tests with no customer data.

## Model adapter wire regressions

The implemented adapter was checked against the locked SDK source and offline
`httpx2.MockTransport` responses. These observations refine the construction-only
smoke above:

- Responses usage may synthesize `details.reasoning_tokens=0` when the response
  omitted that measurement. Use presence of the normalized
  `output_reasoning_tokens` field, preserving unknown, explicit zero and positive
  counts separately.
- PydanticAI wraps provider SDK timeout exceptions in `ModelAPIError`. Classify
  the typed direct cause using the configured provider's timeout type; do not
  guess from exception text. Failed attempts remain counted.
- Strict schema transformation can close a dynamic dictionary and remove size
  assertions, making an authored nonempty dictionary impossible to satisfy.
  Authored schema steps use non-strict output and original host validation.
  Anthropic rejects native non-strict output locally; explicit tool mode works.
- `StructuredDict` inlines only some JSON Schema applicators and merges reference
  siblings by replacement. Fully inline frozen references before passing the
  schema to it; preserve siblings through `allOf`, literal annotations as data,
  and bounded expansion. Reject reachable recursion and dynamic references.
- Validate provider request parameters before admission and budget reservation.
  The actual SDK request still owns its wire preparation; preflight makes no I/O.

The tests establish local schema and request behavior, not live provider
acceptance of every JSON Schema keyword or financial decision accuracy.

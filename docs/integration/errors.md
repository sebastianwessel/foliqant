# Handle review and errors

Review is a valid workflow outcome. A decision can return
`not_answerable` with `no_supported_answer`, for example, and the authored
flow can end in `needs_review`. That result has no `execution.error` and still
contains useful business evidence. Send it to your review path or apply a
separately authored policy; do not retry the model just because it abstained.

A technical failure is never an outcome. When a model request, tool call or
handler fails, its step fails, its flow fails and the run ends
`execution.status == "failed"` with a safe `execution.error`. No `on_unresolved`
route, review path, `fallback` category, binding `default` or `first_of`
alternative, repeat `until` or `continue_when` condition, retry flow or later
collection item acts on it: the failure propagates like an HTTP 5xx, and the
records keep the completed work for diagnosis. There is no configuration that
turns a failure into a business result. Invalid admission input, capacity
rejection, and other pre-run failures raise `ServiceError` before a result
exists. Caller cancellation propagates and does not guarantee a returned result.

The one protocol outcome of a tool is an MCP server's request for human input
(elicitation): the step ends `needs_review` without a result, because the server
itself asked for a person rather than failing.

```python
from foliqant.core.errors import ServiceError


async def run_request(app, envelope):
    try:
        result = await app.run("demo", envelope)
    except ServiceError as error:
        return {"status": "rejected", "code": error.code.value, "retryable": error.retryable}
    if result.execution.status == "failed":
        error = result.execution.error
        assert error is not None
        return {"status": "failed", "code": error.code.value, "retryable": error.retryable}
    return result.model_dump(mode="json")
```

This is a minimal host decision, not a Foliqant API. A real service should
retain the failed result's ledger under its own data policy and keep a durable
request ID. Decide whether any external action can be repeated. `retryable` is
supplied by the failing boundary: `true` means the failure was a transient
condition that a later attempt may overcome. It is never inferred from the code
alone. A timeout or cancellation does not prove a remote operation stopped;
`uncertain_effect` requires reconciliation before retrying.

## Canonical error codes

`SafeError` contains `code`, its fixed safe `message` and `retryable`. It
contains no raw SDK exception. An `invalid_output` or `output_limit_reached`
failure can also carry a content-free explanation, see
[failure reasons](#failure-reasons). `ServiceError` carries the same fields
before an execution result exists.

| Code | Meaning | Typical cause | Retryable | What to change |
| --- | --- | --- | --- | --- |
| `invalid_configuration` | The workflow configuration is invalid. | Unknown profile, unsupported option, a host binding that disagrees with the configuration | no | Fix the configuration or registration |
| `invalid_input` | The input does not satisfy the required contract. | Payload or step input violates its schema | no | Fix the caller's input or the binding that builds it |
| `invalid_output` | An operation returned an invalid result. | Model output violates its schema or decision contract after all [output retries](#output-retries); handler result violates its contract | no | Read `reason`, `location` and `constraint`; clarify instructions, simplify the schema, or raise `output_retries` |
| `invalid_tool_call` | The model called an unknown tool or passed arguments that violate its schema. | A weak tool-use model, a confusing tool description | no | Improve the tool description or instructions, or use a stronger model |
| `output_limit_reached` | The model reached its output token limit before completing the result. | `max_tokens` too small for a reasoning model, or a runaway answer | no | See `reason`: raise `max_tokens` or lower reasoning effort (`reasoning_consumed_budget`); shorten or constrain the answer (`answer_exceeded_budget`) |
| `output_refused` | The model or its provider refused to produce the result. | Content filter, provider refusal, flagged prompt | no | Decide how your host routes refusals to people |
| `context_limit_exceeded` | The model request exceeds the model's context window. | Long input (e-mail thread), large tool results; on OpenAI-compatible servers the input plus `max_tokens` | no | Shorten or split the input, use a larger-context model, or lower `max_tokens` where the server counts it against the context |
| `request_rejected` | The model provider or tool server rejected the request. | HTTP 400/422: unsupported parameter, invalid schema, `max_tokens` above the model's limit | no | Fix the profile options or the schema |
| `model_not_found` | The configured model is not available at the provider. | HTTP 404: wrong model ID or deployment name | no | Fix `model` or the deployment |
| `unauthenticated` | Authentication is required. | HTTP 401: missing or expired credential | no | Fix the credential |
| `forbidden` | The operation is not authorized. | HTTP 403, a tool authorizer's refusal, an identity mismatch | no | Grant access or fix the identity |
| `not_found` | The requested resource was not found. | Unknown workflow, flow or step name at the API | no | Fix the name |
| `missing_binding` | A required input binding is unavailable. | A pointer without `default` finds no value at run time | no | Make the value required upstream or author a `default` |
| `request_timeout` | A model, tool or handler request exceeded its timeout. | A slow provider (`request_timeout`, `model_timeout`, `tool_timeout`), HTTP 408/504 | 408/504 after retries: yes; a client-side timeout: no | Raise the request timeout or reduce the output budget |
| `run_timeout` | The run exceeded its deadline. | `execution.run_timeout` or a caller deadline, including admission waits | no | Raise `run_timeout`, reduce requests, or give the run more capacity |
| `step_limit_reached` | The run reached its limit of executed steps. | `execution.max_steps` with many collection items or repeats | no | Raise `max_steps` or lower `max_items` / `max_attempts` |
| `model_request_limit_reached` | The step reached its limit of model requests. | `execution.model_requests_per_step` in a long tool loop | no | Raise `model_requests_per_step` |
| `iteration_limit_reached` | The step reached its limit of model turns before a final answer. | The step's `max_iterations` in a tool loop | no | Raise `max_iterations` or simplify the task |
| `tool_call_limit_reached` | The step reached its limit of tool calls. | `execution.tool_calls_per_step` | no | Raise `tool_calls_per_step` |
| `rate_limited` | A dependency rejected the request because of a rate limit. | HTTP 429 after the configured retries | yes (no when `Retry-After` was invalid) | Retry later; raise quota or lower concurrency |
| `dependency_overloaded` | A dependency is temporarily overloaded. | HTTP 503/529 after the configured retries | yes | Retry later |
| `dependency_failure` | A required dependency is unavailable. | HTTP 500/502 or a connection failure after retries (retryable); other 5xx, a provider error stop, an unexpected SDK failure (not retryable) | as reported | Check the dependency |
| `tool_error` | The tool reported an error. | An MCP result with `isError` | no | Check the tool server and its arguments |
| `tool_output_limit_exceeded` | The tool result exceeds its configured size limit. | A result larger than the server's `output_limit_bytes` | no | Raise `output_limit_bytes` or return less from the tool |
| `tool_catalog_mismatch` | The tool server's tools differ from the declared catalog. | The server changed a tool schema | no | Review and regenerate the declared catalog |
| `handler_failed` | A registered handler failed unexpectedly. | An uncaught exception in host code | no | Fix the handler; raise a `ServiceError` for an expected failure |
| `conflict` | The request conflicts with an existing operation. | Duplicate evaluation output, a reused ticket | no | Use a new name or ID |
| `uncertain_effect` | An external operation requires reconciliation. | An external effect with an unknown outcome | no | Reconcile before retrying |
| `cancelled` | The execution was cancelled. | Caller or shutdown cancellation | no | None; resubmit if intended |
| `capacity_exceeded` | The service has reached its admission limit. | `concurrency` plus `queue_limit` in use | yes | Retry later, or raise capacity |

A handler that raises `ServiceError` itself keeps that code and `retryable`
flag, so host code can report, for example, a retryable `dependency_failure`
of a service it calls.

## Retry decisions

Foliqant retries only within one model request or tool call, and only failures
that a repeat can overcome. `retry.max_attempts` on a model profile or MCP
server counts the first request; the default `4` is the first request plus up
to three retries, with capped exponential full-jitter backoff (`1` second
initially, at most `30` seconds). A valid `Retry-After` is a lower bound and a
longer one ends the retries. No retry outlasts the request's deadline, and
every retry is a model request or tool call against the step's limits.

| Failure | Repeated within the request | Why |
| --- | --- | --- |
| HTTP 408, 504 (`request_timeout`) | yes | The server or gateway timed out; nothing was completed |
| HTTP 429 (`rate_limited`) | yes, after `Retry-After` | The quota recovers |
| HTTP 500, 502 (`dependency_failure`) | yes | A transient server or gateway error |
| HTTP 503, 529 (`dependency_overloaded`) | yes | A temporary overload |
| Connection failure without a response (models only) | yes | A model request has no external effect |
| Invalid structured output (`invalid_output`) | yes, up to `output_retries` | The model gets its validation problems and corrects the output |
| Client-side timeout of the request | no | The provider may still be working; the same request is likely to time out again |
| HTTP 400/422, context window, 401, 403, 404 | no | Deterministic for the same request |
| `output_limit_reached`, `output_refused` | no | Recurs with the same input, model and options |
| Step, request, iteration and tool call limits | no | A configured bound, not a transient condition |
| A tool error, invalid tool call, handler failure, cancellation | no | Not transient |

When the step's request or tool call limit refuses a retry, the failure reports
the transient condition it would have retried (for example
`dependency_overloaded`, `retryable: true`), and a refused output correction
reports the `invalid_output` it would have corrected. The library never retries
a whole run; a host that owns durable delivery can repeat a run for failures
marked `retryable`, or for a `request_timeout` when its operations have no
external effect.

## Output retries

`output_retries` (profile field or step `model` override, `0`–`8`, default
`1`) bounds how often a model gets another request to correct an invalid
structured output: a schema step's result that violates its JSON Schema, a
decision that violates its contract, or unparseable JSON. The correction
request returns the validator's problems (locations, violated constraints and
messages about the model's own output) to the model only. Every correction is a
model request against `model_requests_per_step`, is counted in
`usage.output_retries`, and does not count as a loop turn for `max_iterations`.
A step that still fails after its corrections fails with `invalid_output`. A
length stop, a refusal and a provider error are never corrected this way.

```yaml
models:
  default:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    output_mode: native
    output_retries: 2
```

## Failure reasons

`SafeError` can explain an `invalid_output` or `output_limit_reached` failure
without content:

| Field | Values |
| --- | --- |
| `reason` | `json_parse_error`, `schema_violation`, `decision_contract`, `missing_output` (no usable output); for `output_limit_reached`: `reasoning_consumed_budget` (reasoning tokens used the whole budget) or `answer_exceeded_budget` (the answer itself was too long or repetitive) |
| `location` | A JSON pointer in schema vocabulary, such as `/units/0/intent`; object keys that are not declared names become `*`. `question:<id>` for a decision contract problem |
| `constraint` | The violated JSON Schema keyword (`enum`, `type`, `required`, `maxLength`, ...), pydantic error type (`json_invalid`, `string_too_long`, ...) or decision problem kind (`unknown_option`, `answer_required`, ...) |

A value outside its safe pattern is omitted, never reported. Evaluation reports
count failures by `reason` in `failures_by_reason`.

## Model stop reasons

When a provider reports that it stopped a response before completing it, the
step fails with a code for that stop reason instead of `invalid_output`:

| Provider stop | Code | `retryable` |
| --- | --- | --- |
| Output token limit (`length`) | `output_limit_reached` | `false` |
| Content filter, or a reported refusal | `output_refused` | `false` |
| Provider-side generation error | `dependency_failure` | `false` |

A limit stop is expected to recur with the same input, model and options,
especially at temperature 0: fix it by raising `max_tokens` or lowering the
reasoning effort of the profile or step (see
[output budget and reasoning](../steps/llm.md#set-the-output-budget-and-reasoning-effort)),
not by resending. The failure's `reason` says which applies when the provider
reported both token counts; the model span carries
`foliqant.response.reasoning_consumed_budget` ([observability](observability.md)).
A refusal is a policy outcome of the provider; decide explicitly whether your
host routes it to people.

## Map failures to your transport

These codes do not define HTTP status codes. An HTTP host should make a
documented mapping suited to its API: malformed transport or `invalid_input`
typically maps to a client error; a failed run is a server error (5xx), never
a successful response; failures marked `retryable` and `capacity_exceeded`
generally need a temporary failure response such as 503, timeouts 504;
`needs_review` is a valid response. Preserve the canonical error code in the
body, and avoid returning validation or provider exception text. The
[HTTP example](http.md) demonstrates one limited mapping; production hosts must
set their own. The CLI exits `2` for input and configuration codes
(`invalid_configuration`, `invalid_input`, `unauthenticated`, `forbidden`,
`not_found`, `conflict`, `model_not_found`, `request_rejected`,
`context_limit_exceeded`), `5` for any other failure marked `retryable`, `4`
for the remaining runtime failures and `130` for cancellation.

Every failed model request, tool call, step, flow and run span has error status
and the failure's code as `error.type`; a review outcome is not an error. See
[observability](observability.md), [Configure limits](../configuration/limits.md)
and the [exact result contract](../reference/inputs-and-results.md).

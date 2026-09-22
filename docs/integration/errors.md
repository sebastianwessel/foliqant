# Handle review and errors

Review is a valid workflow outcome. A decision can return
`not_answerable` with `no_supported_answer`, for example, and the authored
flow can end in `needs_review`. That result has no `execution.error` and still
contains useful business evidence. Send it to your review path or apply a
separately authored policy; do not retry the model just because it abstained.

Technical failures have a different shape. Once a run is admitted, many
failures return an `ExecutionResult` with `execution.status == "failed"` and a
safe `execution.error`. Invalid admission input, capacity rejection, and other
pre-run failures can raise `ServiceError` before a result exists. Caller
cancellation propagates and does not guarantee a returned result.

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
request ID. Decide whether any external action
can be repeated. `retryable` is supplied by the failing boundary and must not
be inferred from the code. A timeout or cancellation does not prove a remote
operation stopped; `uncertain_effect` requires reconciliation before retrying.

## Canonical error codes

`SafeError` contains `code`, its fixed safe `message`, `retryable`, and an
optional `location`. It contains no raw SDK exception. `ServiceError` carries
the same code and retry flag before an execution result exists.

| Code | Meaning |
| --- | --- |
| `invalid_configuration` | The workflow configuration is invalid. |
| `invalid_input` | The input does not satisfy the required contract. |
| `invalid_output` | An operation returned an invalid result. |
| `unauthenticated` | Authentication is required. |
| `forbidden` | The operation is not authorized. |
| `not_found` | The requested resource was not found. |
| `missing_binding` | A required input binding is unavailable. |
| `timeout` | The operation exceeded its deadline. |
| `budget_exhausted` | The execution budget is exhausted. |
| `dependency_failure` | A required dependency is unavailable. |
| `conflict` | The request conflicts with an existing operation. |
| `uncertain_effect` | An external operation requires reconciliation. |
| `cancelled` | The execution was cancelled. |
| `capacity_exceeded` | The service has reached its admission limit. |

These codes do not define HTTP status codes. An HTTP host should make a
documented mapping suited to its API: malformed transport or `invalid_input`
typically maps to a client error; capacity and dependency failures generally
need a temporary failure response; `needs_review` is a valid response. Preserve
the canonical error code in the body, and avoid returning validation or provider
exception text. The [HTTP example](http.md) demonstrates one limited mapping;
production hosts must set their own.

The library's model and MCP retry settings only repeat specifically eligible
transient calls within their configured bounds. They do not retry a whole
workflow or make an ambiguous external effect safe. See
[Configure limits](../configuration/limits.md) and the
[exact result contract](../reference/inputs-and-results.md).

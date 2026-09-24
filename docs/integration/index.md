# Run Foliqant from your application

Foliqant runs inside your Python process. Your application supplies one input,
awaits a workflow, and receives one structured result. The workflow configuration
chooses its steps and routes; your application decides when to call it and what to
do with the result.

## Run one request in Python

This example uses a configured workflow named `demo` in
`config/settings.yaml`. [Install and run](../getting-started/runtime.md) shows
how to create that configuration. Install the provider extra required by your
model profile before opening the application.

```python
import asyncio
import os
from pathlib import Path

from foliqant import Envelope, open_application, prepare_application


async def main() -> None:
    prepared = prepare_application(Path("config/settings.yaml"))
    async with open_application(prepared, environment=os.environ) as app:
        result = await app.run("demo", Envelope(payload={"message": "Please send my statement."}))
        if result.execution.status == "completed":
            print(result.payload)
        elif result.execution.status == "needs_review":
            print("A person or application policy must review this request.")
        else:
            print(result.execution.error)


asyncio.run(main())
```

`prepare_application` compiles the local configuration without opening clients.
`open_application` resolves marked environment values and owns client startup
and shutdown. Keep that context open for the lifetime of a server or worker and
call `app.run(...)` for each request. Each call has its own execution state; the
compiled plans and clients can be shared. Do not prepare and open again for every
HTTP request.

When the workflows use trusted [handler steps](../steps/handler.md), pass the
host's registrations: `prepare_application(path, handlers=HANDLERS)`. The
handlers' contracts are **declared** in `settings.yaml`; each registration only
supplies the callable (and, optionally, schemas that must match the
declaration). `prepared.diagnostics` lists the compiler warnings and infos;
`prepare_application(path, strict=True)` turns warnings into errors for tests.

The `Envelope.payload` is your business input. `Envelope.metadata` is optional
context. The workflow's input schema validates the payload at admission. To
decode untrusted HTTP bytes, use `decode_envelope`, which bounds and validates
the JSON input; see [Expose an HTTP endpoint](http.md).

## Choose the next path

| You need to… | Read |
| --- | --- |
| Interpret the returned business value and decision evidence | [Read results](results.md) |
| Add an HTTP route around the same application | [Expose an HTTP endpoint](http.md) |
| Distinguish review from technical failure | [Handle errors](errors.md) |
| Process several independent requests from one message | [Process multiple requests](multiple-requests.md) |
| Keep an application open in a service | [Deploy and operate](deployment.md) |
| Inspect safe runtime signals | [Observe a running application](observability.md) |

For exact Python and JSON fields, see the [input and result reference](../reference/inputs-and-results.md).

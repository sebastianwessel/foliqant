# Connect the support workflow to your application

Prepare the configuration and trusted handlers once at host startup. Keep the
application open while requests arrive, then run one envelope for each email:

```python
from examples.support_email_tutorial.handlers import HANDLERS
from examples.support_email_tutorial.run import CONFIG_PATH
from foliqant import Envelope, open_application, prepare_application

prepared = prepare_application(CONFIG_PATH, handlers=HANDLERS)
async with open_application(prepared, environment=your_environment) as app:
    result = await app.run(
        "support_email",
        Envelope(payload={"message": incoming_email}),
    )
    if result.execution.status == "completed":
        draft_for_agent = result.payload
```

The authored output projection makes `result.payload` a
`{queue, account_reference, reply}` object after the final flow completes.
For `needs_review`, the configured default payload is
`{disposition: needs_review}`; examine the flow records to understand why.
A technical `failed` result keeps its safe error and completed records for
diagnosis. Do not treat either state as a draft to send.

Your host authenticates the caller, handles email delivery and persistence,
and decides how an agent approves and sends the draft. Foliqant itself runs
one bounded invocation in memory. The [HTTP integration guide](../integration/http.md)
shows a small transport wrapper; [result handling](../integration/results.md)
explains statuses and output ownership in more detail.

# Build a support email workflow

This learning path starts with one synthetic email and grows it into a
reviewable support draft. You will author the files yourself, then compare
them with the runnable [final example](https://github.com/sebastianwessel/foliqant/tree/main/examples/support_email_tutorial/).

1. [Set up the project](setup.md).
2. [Classify one email](decision-basics.md).
3. [Extract the requested action](structured-extraction.md).
4. [Route to a branch](multiflow-routing.md).
5. [Look up an account](read-only-mcp.md).
6. [Retry a lookup with a corrected reference](retry-lookup.md).
7. [Try a bounded model tool loop](model-tool-loop.md).
8. [Handle several requests](multi-request-processing.md).
9. [Evaluate the workflow](evaluate.md).
10. [Connect it to your application](integrate.md).

The setting stays the same: a fictional subscription support team receives
`Please review the duplicate charge on invoice INV-7 for account A-100.`
The direct path classifies the email, extracts an account reference, looks up a
synthetic account, and drafts a reply for an agent to review. It never sends
email or changes an account.

The final example contains three workflows. `support_email` uses an authored
direct MCP call. `agent_reply` shows a bounded model tool loop for the same
read-only lookup; it is an alternative, not the next step in one invocation.
`support_multi` prepares independent requests in a bounded collection.
Offline scripted model responses prove wiring and validation. Live model
quality requires separate evaluation.

Foliqant runs one bounded invocation in memory. A host owns incoming HTTP,
identity checks, persistence, and sending a reviewed reply. See
[HTTP integration](../integration/http.md) for that boundary.

# Evaluate the support email workflow

The final example tracks eight synthetic cases in
[`evaluation/dataset.json`](https://github.com/sebastianwessel/foliqant/blob/main/examples/support_email_tutorial/evaluation/dataset.json):
billing, cancellation, two active queues, an unsupported email, a missing
account reference, a tool-loop reply, and two multi-request dispositions.
Run them without a model endpoint:

```sh
uv run --no-sync python -m examples.support_email_tutorial.evaluate
```

Expect `cases: 8`, `suites: 3`, and `passed_checks: 34` of `total_checks: 34`.
The evaluator writes a detailed report under untracked `.foliqant/evaluations/`;
its printed summary includes the actual path. The checks prove compiled
bindings, review routes, the real local MCP call, and output shape. They do
not measure live model accuracy. The scripted model is a fixture, separate
from the authored expectations.

When you have your own reviewed cases, add messages and expected public result
pointers to a private dataset. Check classification against queue labels,
extraction against source text and explicit references, review outcomes on
ambiguous mail, and draft claims against returned account facts. Keep real
customer messages and reports out of Git. See [ground-truth authoring](../evaluation/ground-truth.md)
and [task scoring](../evaluation/task-types.md).

After a local model is running and `MODEL_ID` and `MODEL_BASE_URL` are set,
`--live` runs the same case set against that endpoint. Its scores are evidence
for that model and configuration only:

```sh
uv run --no-sync python -m examples.support_email_tutorial.evaluate --live
```

Next, [connect the workflow to your application](integrate.md).

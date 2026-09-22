# Read a workflow result

`app.run(...)` returns one `ExecutionResult`. Read `execution.status` first,
then use `payload` for the business value and `flows` for detail. Do not infer
success from the presence of a payload: on failure it preserves the accepted
input rather than fabricating a successful output.

| Field | What it tells your application |
| --- | --- |
| `payload` | The workflow's configured output projection; without one, the accepted input payload. |
| `metadata` | Accepted caller context. |
| `flows` | Records by flow instance ID, with local `steps` and optional projected `result`. |
| `transitions` | Routes or terminal outcomes selected at flow boundaries. |
| `execution` | Run ID, workflow, revision, status, measured usage, and safe error on failure. |

A completed step result is at `result.flows[flow_id].steps[step_id].result`.
Its `result` can be JSON `null`; an omitted `result` means something else. Flow
and step records can be `completed`, `needs_review`, `failed`, `cancelled`, or
`skipped`. The root execution cannot be `skipped`.

## Read a decision

A decision step with one configured `question` exposes one result object. A
step with `questions` exposes `{"results": [...]}` in question order. A single
choice can look like this:

```json
{
  "questionId": "request_kind",
  "type": "choice",
  "answerability": {"status": "answerable", "issues": []},
  "answer": {"optionId": "statement"},
  "reason": "The message asks for a statement.",
  "evidence_strength": "strong"
}
```

Your code should branch on `answerability.status`, stable `issues`, and the
typed `answer`. `reason` explains the assessment to a person; its wording is
not a route key. `evidence_strength` describes support for the *whole reported
assessment*, including answerability and any abstention. `strong` can therefore
describe a well-supported `not_answerable` result. `limited` is a weaker but
permissible interpretation; `null` means strength was not assessed. These are
not probabilities, confidence scores, or automatic action thresholds.

`answerable` has a substantive answer. `partially_answerable` is available for
supported subsets of `multiselect` and `request_units`. `not_answerable` and
`undetermined` have issue codes and no substantive answer, except that a
predicate uses `{"value": "unknown"}`. For a singular choice,
`selection.origin: fallback` may record an authored fallback category while
the native answer remains `null` and the step remains `needs_review`.

Foliqant validates the structure and correspondence of model output. It does
not prove the explanation or strength rating is correct. Use [reviewed evaluation
cases](../evaluation/ground-truth.md) for that question. The exact question and
answer shapes are in the [reference](../reference/inputs-and-results.md); see
[decision policy](../guides/decision-contracts.md) for criteria and fallback
design.

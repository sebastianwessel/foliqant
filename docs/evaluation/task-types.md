# Score task types

Choose assertions that match the business claim. Exact equality is a good
default, but it is wrong for unordered labels and too brittle for extractions
that permit several source-faithful boundaries.

## Choose a comparison

| Comparison | Use for | Behavior |
| --- | --- | --- |
| `exact` | Scalar labels, booleans, structured objects, ordered arrays | Type-sensitive JSON equality; array order matters |
| `set` | Unordered label arrays | Ignores top-level order and duplicates; JSON types remain distinct |
| `one_of` | Several acceptable answers | Expected is an array of distinct values (the first is the primary gold); passes when the result equals one exactly |
| `text` | Names and short values copied from free text | String equality after Unicode case folding and whitespace collapsing |
| `contains` | A fact that a generated description must mention | The normalized gold string occurs in the normalized result string |
| `source_span` | Verbatim extraction | Candidate must be a source occurrence containing the required range and contained by the allowed range |
| `custom` | A deterministic domain rule unavailable in JSON configuration | Python-only async scorer registered by name and revision |

### Exact and set examples

These are expectation fragments:

```json
{
  "name": "category",
  "path": "/flows/triage/steps/classify/result/answer/optionId",
  "expected": "billing_dispute",
  "comparison": "exact"
}
```

```json
{
  "name": "labels",
  "path": "/flows/triage/steps/tag/result/answer/optionIds",
  "expected": ["billing", "urgent"],
  "comparison": "set"
}
```

`set` is only for a top-level JSON array. It does not recursively normalize
nested arrays or coerce `true`, `1`, and `"1"`.

### Text and contains examples

`text` and `contains` take string gold. Both fold case (`str.casefold`) and
collapse every whitespace run to one space before comparing; punctuation, word
order and spelling still matter. A non-string result fails.

```json
{
  "name": "client_name",
  "path": "/payload/fields/client_name",
  "expected": "Stadtwerke Musterstadt",
  "comparison": "text"
}
```

```json
{
  "name": "mentions_late_delivery",
  "path": "/payload/description",
  "expected": "not delivered",
  "comparison": "contains"
}
```

Use `contains` for a fact a generated text must mention, not for general writing
quality. Gold must be nonblank after normalization.

### Accept alternatives with `one_of`

When reviewers accept more than one answer, list them; the first is the primary
gold:

```json
{
  "name": "report_type",
  "path": "/payload/report_type_id",
  "expected": ["risk_exposure_breakdown", "exposure_analysis"],
  "comparison": "one_of"
}
```

A classification metric over a `one_of` assertion counts an accepted alternative
as correct and places it on the diagonal of its own label; any other prediction
is a confusion of the primary label. `one_of` gold cannot feed a multilabel
metric.

### Project the items of an array with `each`

Many results are arrays of objects: request units, line items, planned intents.
`each` is a relative JSON pointer applied to every item of the array at `path`;
the comparison then sees the array of projected values. It supports `exact`
(order matters), `set`, `one_of` and `custom`:

```json
{
  "name": "intent_types",
  "path": "/payload/intents",
  "each": "/intent",
  "expected": ["incident", "new_report"],
  "comparison": "set"
}
```

A result that is not an array, or an item without the member, fails the check
(`mismatch`); it never becomes a shorter array.

### Treat an absent value as null

Structured outputs often omit a field they did not find. By default an absent
path is a `missing` check, which is different from a present JSON `null`. When
"not found" is a valid answer, set `absent_as_null`:

```json
{
  "name": "identifier",
  "path": "/flows/extract/result/values/identifier",
  "expected": null,
  "absent_as_null": true
}
```

Absence counts as null only inside an owner that executed (`completed` or
`needs_review`): a skipped step stays `skipped`, a failed step or run stays an
error, and a flow or step record that does not exist stays `missing`. The check
details keep `actual_present: false`, so reports still show that the value was
absent rather than an explicit null.

### Source-span example

Suppose the case input message is `Cancel renewal for account C-1.`. Code-point
offsets `[0, 14]` identify the required words `Cancel renewal`, while `[0, 29]`
allows the returned extraction to include the account phrase:

```json
{
  "name": "requested_action",
  "path": "/payload/requested_action",
  "expected": {
    "input_path": "/payload/message",
    "required": [0, 14],
    "allowed": [0, 29]
  },
  "comparison": "source_span"
}
```

Ranges use Unicode code-point offsets and an exclusive end. `required` must be
nonempty, inside `allowed`, and both must fit the source string. The candidate
must occur verbatim; evaluation performs no case folding, normalization, or
semantic judging.

### Custom Python scorer

JSON datasets cannot import code or register scorers. Build or extend an
in-memory suite explicitly:

```python
from foliqant.core.json import FrozenJson
from foliqant.evaluation import Expectation, RegisteredScorer


async def same_casefolded(actual: FrozenJson, expected: FrozenJson) -> bool:
    return (
        isinstance(actual, str)
        and isinstance(expected, str)
        and actual.casefold() == expected.casefold()
    )


expectation = Expectation(
    "normalized_category",
    "/payload/category",
    "billing",
    "custom",
    "casefolded",
)
scorers = (RegisteredScorer("casefolded", "1", same_casefolded),)
```

Pass `scorers=scorers` to `evaluate`. Missing registration fails before the
pipeline runs. A scorer exception becomes a check error rather than an execution
failure. Change the scorer revision when its meaning changes.

## Match metrics to the output

Metrics are optional summaries over a declared path and label catalog. Gold
still comes from the matching expectation in each case.

### Choice, ordinal, and predicate

Use `classification` for one catalog value:

```json
{
  "name": "queue_quality",
  "path": "/flows/triage/steps/classify/result/answer/optionId",
  "kind": "classification",
  "labels": ["billing_dispute", "service_change", "cancellation"]
}
```

Ordinal levels and native decision predicate values use the same metric kind
with their complete string catalog. Predicate answers are the strings `"true"`
and `"false"` at `/answer/value`, not JSON booleans. For a generic boolean
output, use an exact expectation or project it to a string label before applying
a classification metric. A classification catalog may explicitly include `null`. If it
does, null gold and an actual null are a valid observed label. Otherwise an
actual null is an abstention, not a category.

### Several assertions at one path

A metric measures the one expectation at its `path` (and `each`) in each case.
When a case asserts the same path twice, for example an unordered set and an
ordered sequence of projected intents, name the measured one with
`expectation`:

```json
{
  "name": "intent_set",
  "path": "/payload/intents",
  "each": "/intent",
  "expectation": "intent_types",
  "kind": "multilabel",
  "labels": ["new_report", "update_report", "incident", "miscellaneous"]
}
```

### Multiselect

Use `multilabel` with string labels:

```json
{
  "name": "issue_quality",
  "path": "/flows/triage/steps/classify/result/answerability/issues",
  "kind": "multilabel",
  "labels": [
    "no_supported_answer",
    "conflicting_information",
    "multiple_valid_options"
  ]
}
```

Multilabel reports include exact-set accuracy and per-label TP, FP, FN, and TN,
plus micro and macro precision, recall, and F1. The label catalog is string-only.

### Structured extraction fields

Use a `fields` metric for an extraction that returns an object of fields. Its
`labels` are the field names; its gold is each case's expectation at
`path/<field>` (field names are escaped as JSON-pointer tokens). Author only the
fields a reviewer decided; `null` gold means the field must be absent or null.

```json
{
  "name": "extraction",
  "path": "/flows/extract/result/values",
  "kind": "fields",
  "labels": ["client_name", "identifier", "report_deadline"]
}
```

Pair it with one field assertion per labelled field, using `absent_as_null`
for null gold and `exact`, `set`, `text` or `contains` for values. Each field of
each attempt gets one outcome:

| Outcome | Gold | Result |
| --- | --- | --- |
| `correct_value` | a value | a value that matches under the field's comparison |
| `correct_null` | `null` | absent or `null` |
| `hallucinated` | `null` | any value |
| `missed` | a value | absent or `null` |
| `wrong_value` | a value | a value that does not match |
| `unavailable` | either | the owner was skipped or failed, or the run failed |

The report adds `per_field` counts and a pooled `field_totals` with accuracy,
hallucination rate (hallucinated / null gold) and miss rate (missed / value
gold), `field_accuracy` over all scored fields and `macro_field_accuracy` over
the fields with support. Its `correct` and `accuracy` count attempts whose
every gold field is correct. Unavailable fields stay in every denominator.
Field assertions must be built-in, unprojected comparisons.

## Score structured and operational behavior explicitly

For extraction and request units, make separate assertions for fields that have
separate business meanings. Use source spans for verbatim text and exact or set
checks for IDs, statuses, relationships, and counts. Do not reduce a request-unit
assessment to one label if unit identity or relations matter.

For decision fallbacks, native model correctness and application policy are
different paths:

```text
/flows/triage/steps/classify/result/answerability/status
/flows/triage/steps/classify/result/answer/optionId
/flows/triage/steps/classify/selection/category/id
/flows/triage/steps/classify/selection/origin
```

For handlers and MCP steps, assert the validated business result and expected
failure status separately. A failed run is an execution error in the report
(`error_code`, check outcome `error`), never a gold mismatch, so gold cannot
expect a failure as a business answer. For routing, assert both the selected transition or
terminal status and the final projected payload. For a flow collection, assert
ordered item identity, each relevant child result, and any partial ledger after
failure. Evaluation traverses only records marked as flow collections; an
ordinary business object that resembles a ledger is not treated as one.

## Evaluate text and tool-assisted answers

A good reply can have several correct phrasings. Do not use exact sentence
equality as a general measure of writing quality. Separate the claims you can
check deterministically from those a person must review:

| Output or behavior | Useful check | What it does not establish |
| --- | --- | --- |
| JSON answer fields | Exact values for references, statuses, and amounts | Correctness of unrelated prose |
| Free-form reply | A registered deterministic scorer for a precise requirement, plus human review | General factuality or helpfulness |
| Read-only lookup | Expected final facts and step completion | Which tool arguments the model selected |
| Required tool use | `/flows/{flow}/steps/{step}/usage/tool_calls` | Whether the call was useful or correct |
| Agent-loop limit | A scripted model that keeps asking for tools, expecting `iteration_limit_reached` or `tool_call_limit_reached` | Quality of a live model's stopping decision |

For example, this expectation checks the attempt count in a controlled fixture
that requires exactly one lookup:

```json
{
  "name": "one_lookup",
  "path": "/flows/answer/steps/draft/usage/tool_calls",
  "expected": 1
}
```

Choose counts from the business requirement, not from one observed model run.
Tool retries also consume attempts. If several valid strategies exist, an exact
count is too restrictive; use a registered scorer for an allowed bound, and
inspect the final answer independently. A standard result does not expose a full
tool-call transcript. Assert tool names and arguments in isolated integration
tests with a recording fake; do not infer them from the final reply or count.

For live text, review grounding in the provided input and tool facts, omissions,
unsupported promises, language, and requested tone. The built-in comparisons do
not call an LLM judge. The [support-email evaluation tutorial](../tutorials/evaluate.md)
shows separate checks for status, classification, extraction, and a scripted
reply; its exact draft expectation verifies wiring, not writing quality.

## Review reasons and evidence strength

Decision reasons and evidence strength are model assessments of the input.
They are not probabilities or evidence that the answer is correct. Gold can
assert a reviewed strength enum and answerability issues at their public paths,
but avoid exact prose matching for reasons: review whether the reason names the
actual supporting signal, conflict, or missing information.

Evaluate these assessments separately from category accuracy. A wrong category
with strong reported evidence is a particularly useful case to inspect. A
strong assessment that an input is not answerable can be correct when the input
clearly contains incompatible active requests. See
[reason and evidence strength](../reference/inputs-and-results.md#reason-and-evidence-strength)
for the precise meaning of each field.

## Understand metric limits

Classification confusion rows are expected labels and columns are predicted
labels, both in catalog order. Accuracy is `correct / support`; coverage is
`observed / support`. Missing, skipped, error, invalid, and abstained values stay
visible and do not become predictions. Multilabel exact-set accuracy also keeps
these unavailable cases in support.

Metrics describe the authored cases. They do not infer acceptance thresholds,
statistical significance, population accuracy, or the business cost of one kind
of error. Review confusion and per-label counts together with coverage before
changing a prompt or policy.

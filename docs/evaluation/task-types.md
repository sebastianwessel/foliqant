# Score task types

Choose assertions that match the business claim. Exact equality is a good
default, but it is wrong for unordered labels and too brittle for extractions
that permit several source-faithful boundaries.

## Choose a comparison

| Comparison | Use for | Behavior |
| --- | --- | --- |
| `exact` | Scalar labels, booleans, structured objects, ordered arrays | Type-sensitive JSON equality; array order matters |
| `set` | Unordered label arrays | Ignores top-level order and duplicates; JSON types remain distinct |
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
failure status separately. For routing, assert both the selected transition or
terminal status and the final projected payload. For a flow collection, assert
ordered item identity, each relevant child result, and any partial ledger after
failure. Evaluation traverses only records marked as flow collections; an
ordinary business object that resembles a ledger is not treated as one.

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

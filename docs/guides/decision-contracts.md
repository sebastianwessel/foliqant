# Use decision contracts

Native decisions answer typed questions over supplied state. Each result carries
an answerability status, stable issue codes, a concise explanation, and source
citations. Validate the complete result before applying application policy.

## Define category boundaries

Use `choice` for one category and `multiselect` for several independently valid
labels. Give every option a stable ID and a description that states what belongs
in the category, what is excluded, and how it differs from nearby categories.
Question-level criteria define the selection rule for the complete catalog.

Validate a new catalog before building questions:

```python
from foliqant.decisions.category_catalog import CategoryCatalog
from foliqant.decisions.contracts import ChoiceQuestion

catalog = CategoryCatalog.model_validate({
    "categories": [
        {
            "id": "incident",
            "description": "A reported malfunction that needs resolution.",
        },
        {
            "id": "information request",
            "description": "A request for facts, documents, or instructions.",
        },
    ],
})
question = ChoiceQuestion(
    id="request_kind",
    type="choice",
    prompt="Which request kind does the current message express?",
    criteria=["Use only the supplied message and category definitions."],
    allowedSourceIds=["message"],
    options=catalog.decision_options(),
)
```

`CategoryCatalog` normalizes printable ASCII IDs to lowercase snake case.
`Information Request` and `information-request` both become
`information_request`, so that pair is rejected as a collision. Canonical IDs
match `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`. Unicode IDs need an explicit caller-owned
mapping. See the [catalog schema](https://github.com/sebastianwessel/foliqant/blob/main/schemas/foliqant/decisions/category-catalog.schema.json).

Keep topic, request kind, priority, and other dimensions in separate questions.
Several labels for one request belong in a `multiselect`; repeated requests
belong in separate `request_units`. Priority needs an explicit rubric. Relative
deadlines need reference time and timezone; SLA arithmetic belongs in
application code. Resolve display text from the catalog instead of asking a
model to reproduce it. `catalog.resolve_id(raw_id)` can recover a formatting
variant through the same normalization, but still rejects IDs absent from the
catalog.

Descriptions and other prose may be English or German while the machine IDs,
enum values, and issue codes remain English and stable.

## Handle incomplete evidence

Use stable status and issue codes in code. Explanation text is for people:

| Status | Meaning |
| --- | --- |
| `answerable` | The allowed evidence supports a substantive answer. |
| `partially_answerable` | A collection has supported results and an unresolved part. |
| `not_answerable` | The evidence or question constraints do not support an answer. |
| `undetermined` | The assessment could not establish answerability. |

Issue codes distinguish different obstacles:

- `missing_information`: a required fact or referent is absent.
- `conflicting_information`: incompatible evidence has no stated resolution.
- `multiple_valid_options`: multiple positively supported answers exceed the
  question’s permitted cardinality.
- `no_matching_option`: a supported fact or request falls outside the catalog.

An unknown request is not an established out-of-catalog request. A missing
attachment or unclear reference uses `missing_information`. Several clear
requests are valid when a collection question permits them. A predicate uses `unknown` when the
evidence establishes neither true nor false; an explicit absence can support
`false` for a presence predicate.

A partially answerable collection must contain at least one supported item and
an evidence citation. A missing requested item keeps the collection partial even
when every returned item is clear. Each returned request unit needs evidence
from its allowed source; a non-null subject must appear verbatim in that unit's
evidence. Use `null` when the source contains no identifying reference.

Validate JSON against the
[output schema](https://github.com/sebastianwessel/foliqant/blob/main/schemas/foliqant/decisions/decision-output.schema.json),
then validate its IDs, allowed answers, and citations against the
[input contract](https://github.com/sebastianwessel/foliqant/blob/main/schemas/foliqant/decisions/decision-input.schema.json).
Python callers can use `DecisionInput`, `DecisionOutput`, and
`validate_decision_output` from `foliqant.decisions.contracts`.

After validation, apply your own policy:

```python
if result.answerability.status == "answerable":
    handle_proposed_answer(result.answer)
elif "missing_information" in result.answerability.issues:
    request_missing_information(result.explanation.missingFacts)
else:
    send_for_review(result)
```

These functions are application policy. Do not branch on the wording of an
explanation. Authentication, authorization, business prerequisites, and the
safety of an external action remain application responsibilities.

## Keep fallback separate from evidence

`missing_information` and `no_matching_option` are both declared issue codes.
They should remain distinct: the first can need more facts; the second can need
a different catalog or process. The runtime does not infer one from the other.

A workflow may deliberately map either to the same fallback category without
merging those facts. Configure an optional category object and allowed issues
on a single-choice decision, as shown in [workflow routing](build-workflows.md#classify-with-an-explicit-fallback).
The native answer stays unresolved; `selection.origin: fallback` records the
policy choice separately. Contradictions or multiple valid options remain
unresolved unless explicitly covered by that policy. A fallback cannot hide an
invalid response or turn a request needing review into a successful model answer.

## Treat explanations as evidence summaries

An explanation summary is a bounded human-readable reason, not a transcript of
hidden model reasoning. The runtime adds generic contract guidance alongside
your decision instructions in both native and tool output modes. It preserves
your criteria and rejects invalid responses instead of truncating or retrying
them. An explanation may contain at most 400 characters; generation aims
for 160 or fewer. Put exact quotations in citation fields, where text must match
the declared source exactly. The validator checks structure and declared
references; it cannot establish semantic truth or financial accuracy.

All authored, projected, and generated decision records are unreviewed research
data. Automatic checks are useful filters, but numerical confidence requires
independently labelled data, held-out calibration, and a separate audit. Never
relabel generated diagnostic partitions as human-gold evaluation data.

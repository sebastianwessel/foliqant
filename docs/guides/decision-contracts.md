# Use decision contracts

Decision steps answer typed questions over supplied state. Each result carries
an answerability status, stable issue codes, a short `reason`, and
`evidence_strength`. Validate the complete result before applying application policy.

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

Use stable status and issue codes in code. The reason is for people:

| Status | Meaning |
| --- | --- |
| `answerable` | The allowed evidence supports a substantive answer. |
| `partially_answerable` | A collection has supported results and an unresolved part. |
| `not_answerable` | The evidence or question constraints do not support an answer. |
| `undetermined` | The assessment could not establish answerability. |

Three stable issue codes describe obstacles to answering:

- `no_supported_answer`: the supplied information does not establish a substantive
  answer or category for a required part of the question. This includes absent
  facts or referents and clear requests that the supplied catalog cannot represent.
- `conflicting_information`: incompatible evidence has no stated resolution.
- `multiple_valid_options`: several positively supported answers exceed the
  question's permitted cardinality. Merely imaginable alternatives do not qualify.

Use `reason` to understand the particular case. Do not parse it to choose a
workflow route or infer that asking for more information will always resolve
`no_supported_answer`.
An overall request collection may still be `answerable` when `allowNoMatch`
permits a null category: the requests are known, but at least one has no supported
category, so `no_supported_answer` is still required. Do not assume that every
issue makes the entire result unanswerable.
Do not add `no_supported_answer` merely because a conflict or several supported
options prevent selection; an additional issue needs an independent obstacle.
Several clear requests are valid when a collection question permits them. A
predicate uses `unknown` when the evidence establishes neither true nor false;
an explicit absence can support `false` for a presence predicate.

A partially answerable collection must contain at least one supported item. A
missing requested item keeps the collection partial even when every returned
item is clear. A request unit's non-null `subject` must appear verbatim in an
allowed input source. Use `null` when the source contains no identifying reference.

Validate JSON against the
[output schema](https://github.com/sebastianwessel/foliqant/blob/main/schemas/foliqant/runtime/decision-output.schema.json),
then validate its IDs, allowed answers, and subjects against the
[input contract](https://github.com/sebastianwessel/foliqant/blob/main/schemas/foliqant/decisions/decision-input.schema.json).
Python callers use `DecisionInput` from `foliqant.decisions` and `DecisionOutput`
and `validate_decision_output` from `foliqant.contracts.decisions`.

After validation, apply your own policy:

```python
if result.answerability.status == "answerable":
    handle_proposed_answer(result.answer)
else:
    send_for_review(result)
```

These functions are application policy. Do not branch on the wording of a
`reason`. Authentication, authorization, business prerequisites, and the
safety of an external action remain application responsibilities.

## Keep fallback separate from evidence

A single-choice workflow can map `no_supported_answer` to a configured fallback
category such as `misc`. Configure the category object and allowed issues as shown
in [workflow routing](build-workflows.md#classify-with-an-explicit-fallback).
The model answer stays unresolved; `selection.origin: fallback` records the
policy choice separately. Contradictions or multiple valid options remain
unresolved unless explicitly covered by that policy. A fallback cannot hide an
invalid response or turn a request needing review into a successful model answer.

## Read evidence strength

`evidence_strength` describes the supplied support for the returned answer:

| Value | Meaning |
| --- | --- |
| `strong` | Decisive support under the question's criteria, including a valid inference from supplied facts. |
| `limited` | Weaker support for a permissible interpretation that still satisfies the criteria. |
| `null` | No substantive answer: the answer is null, or a predicate is `unknown`. |

`limited` never permits inventing an essential missing fact. A substantive
predicate `false` and an allowed empty collection still need a non-null strength.
For a collection, strength describes its weakest returned member. Answerability
separately describes completeness: a partial collection can have strong support
for every returned item.

Each result also requires a nonblank `reason` of at most 400 characters. Aim for
one concise sentence explaining the applied criterion, relevant facts, and any
decisive limitation. For example:

```json
{
  "questionId": "request_kind",
  "type": "choice",
  "answerability": {"status": "answerable", "issues": []},
  "answer": {"optionId": "incident"},
  "reason": "The message reports a failed payment that needs resolution.",
  "evidence_strength": "strong"
}
```

The runtime adds the same contract guidance in native and tool output modes and
rejects invalid responses. It does not truncate reasons or automatically retry.
Strength is a qualitative model assessment, not a calibrated probability or a
guarantee of correctness. It does not change routing or trigger an automatic
threshold; keep application policy explicit.

## Contract versions

Workflow input uses the existing `DecisionInput` with `schemaVersion: 2`.
Runtime `DecisionOutput` uses `schemaVersion: 3` and a `results` array. The
single-question step result exposes the result object illustrated above. For
multiple questions, the runtime returns results in supplied question order.
Runtime results and request units contain no citation arrays.

The separate model-development format in `foliqant.decisions` retains native
v2 output with explanations and citations for existing datasets and lifecycle
tools. See [native decision data](native-decision-data.md) for that format.

Try the focused [decision evidence example](https://github.com/sebastianwessel/foliqant/blob/main/examples/decision_evidence/README.md)
for choice versus multiselect, limited interpretations, substantive false,
empty collections, and request units. Its default evaluation checks authored
gold and configuration offline; `--live` executes the configured model.

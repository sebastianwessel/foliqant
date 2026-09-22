# Author ground truth

Ground truth is a reviewed statement of expected behavior, not output copied
from the system being evaluated. Start from the business rule, have a domain
owner review hard cases, and record both the expected value and the public
result path where it must appear.

## Build a balanced case set

Cover behavior families deliberately:

| Family | Example | What to review |
| --- | --- | --- |
| Positive | “Cancel renewal for account C-1049.” | Supported answer, extraction, route, and terminal status |
| Ambiguous | “Please help.” | Abstention or review rather than an invented category |
| Negative | “I read the cancellation policy; make no changes.” | Mentioned labels do not become active requests |
| Language | German and English versions of the same rule | Same machine outcome without translating IDs |
| Thread/history | Earlier request is explicitly corrected or withdrawn | Current intent wins only when the authored rule says so |

Keep related cases in a named family and track their source. Split development
cases from an untouched holdout before prompt or model selection. Translations
and paraphrases are useful coverage, but they are not automatically independent
evidence.

## Place the dataset

The default location is `evaluation/dataset.json` beside `config/`:

```text
config/
  settings.yaml
  support_triage/
    workflow.yaml
evaluation/
  dataset.json
  cases/
    pipeline.json
    classify-step.json
```

The evaluator reads this directory only when evaluation is requested. Runtime
preparation, validation, startup, and normal workflow calls do not read gold.
For another location, add a literal path relative to `config/settings.yaml`:

```yaml
evaluation:
  dataset: ../../reviewed-gold/support.json
```

The dataset path is not environment-expanded and does not affect the runtime
configuration digest.

## Write the manifest

This is a complete manifest using separate case files:

```json
{
  "name": "support_checks",
  "revision": "reviewed-2026-09-22",
  "suites": [
    {
      "name": "pipeline",
      "workflow": "support_triage",
      "cases": "cases/pipeline.json",
      "metrics": [
        {
          "name": "terminal_status",
          "path": "/execution/status",
          "kind": "classification",
          "labels": ["completed", "needs_review"]
        }
      ]
    },
    {
      "name": "classify_step",
      "workflow": "support_triage",
      "flow": "triage",
      "step": "classify",
      "cases": "cases/classify-step.json"
    }
  ]
}
```

`name` identifies the dataset, while `revision` identifies the reviewed meaning
of its current gold. Change the revision when cases or their interpretation
change. Foliqant also computes a content fingerprint from the ordered suite.

`cases` can instead be a nonempty inline array. A referenced file contains only
the JSON case array and resolves relative to the manifest. References are one
level deep; a case file cannot reference another case file.

## Write cases and expectations

This is a complete case-file fragment with positive and ambiguous cases:

```json
[
  {
    "id": "positive_cancellation_en",
    "input": {
      "payload": {
        "requestId": "eval-001",
        "message": "Cancel renewal for account C-1049."
      },
      "metadata": {"language": "en"}
    },
    "expectations": [
      {
        "name": "completed",
        "path": "/execution/status",
        "expected": "completed"
      },
      {
        "name": "queue",
        "path": "/flows/triage/steps/classify/result/answer/optionId",
        "expected": "cancellation"
      }
    ]
  },
  {
    "id": "ambiguous_help_en",
    "input": {
      "payload": {"requestId": "eval-002", "message": "Please help."},
      "metadata": {"language": "en"}
    },
    "expectations": [
      {
        "name": "review",
        "path": "/execution/status",
        "expected": "needs_review"
      },
      {
        "name": "issue",
        "path": "/flows/triage/steps/classify/result/answerability/issues",
        "expected": ["no_supported_answer"],
        "comparison": "set"
      }
    ]
  }
]
```

Every case has a unique `id`, one complete `Envelope` under `input`, and at
least one named expectation. An expectation has an RFC 6901 `path`, an explicit
JSON `expected` value, and optional `comparison`. Omitted comparison defaults to
`exact`.

Use public result paths, for example:

| Observation | Path |
| --- | --- |
| Terminal status | `/execution/status` |
| Workflow output | `/payload/...` |
| Flow projection | `/flows/{flow}/result/...` |
| Step result | `/flows/{flow}/steps/{step}/result/...` |
| Effective selection | `/flows/{flow}/steps/{step}/selection/category/id` |
| Collection child step | `/flows/{flow}/steps/{collection}/result/items/{index}/steps/{step}/result/...` |

For a failed collection ledger, use `partial_result` instead of `result`. Do not
use flow-local authoring paths such as `/steps/{step}` in evaluation gold.

Every case included by a metric needs one expectation at that metric path. Keep
a native category metric in a suite containing answerable classification cases,
or measure an always-present projection such as terminal status. For an
unanswered choice, `/result/answer` exists with JSON `null`, while
`/result/answer/optionId` is absent. Assert and measure those states separately;
do not treat a missing nested field as a null label.

## Protect privacy and review quality

Do not commit customer messages, private corpora, generated reports, or model
responses as gold. Synthetic examples can establish wiring and invariants; they
do not establish population accuracy. Store reviewer identity and decision
history in your own controlled process rather than embedding personal data in
case IDs.

Run `foliqant evaluate --check` after every gold edit. It proves structural
validity and known target references, but it cannot prove that a reviewer chose
the correct business outcome or that a dynamic result field will exist.

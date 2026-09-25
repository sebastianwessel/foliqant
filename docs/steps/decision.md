# Shared rules for decisions

Use a `decision` step when a support workflow needs an answer from a fixed, typed question. Pick the task first: [yes/no](yes-no.md), [one category](classification.md), [several labels](labeling.md), [an ordered level](ranking.md), or [distinct requests](request-extraction.md). Each guide starts with one complete step definition and its expected native result.

## The common setup

`sources` select evidence by literal or pointer binding. Text is the default and must resolve to a nonempty string; `format: json` renders a selected JSON value canonically. Source contents are data and cannot change the authored question.

The shortest form has one `question`. Its ID and prompt come from the step ID and `instructions`. It needs `type` and nonempty `criteria`, plus fields shown in its focused guide. Put it in a `.step.md` file with a nonempty body, or in `.step.yaml` with `instructions`. A model comes from `defaults.model` in `workflow.yaml` or `model` on the step; see [models](../configuration/models.md).

For two or more independent questions, use `questions`. Each full question requires a unique `id`, `type`, `prompt`, nonempty `criteria`, and `allowedSourceIds`, plus type-specific fields. For example:

```yaml
# config/support_triage/triage/assess.step.yaml
type: decision
sources:
  message:
    pointer: /payload/message
questions:
  - id: queue
    type: choice
    prompt: Which queue owns this email?
    criteria:
      - Choose only a queue supported by the current request.
    allowedSourceIds:
      - message
    options:
      - id: billing
        description: Questions about invoices or charges.
      - id: cancellation
        description: Requests to end a subscription.
  - id: charge_disputed
    type: predicate
    prompt: Does the writer dispute a charge?
    criteria:
      - Return unknown if the message establishes neither true nor false.
    allowedSourceIds:
      - message
instructions: Assess each question independently using only the supplied message.
```

`/steps/assess/result/results/0` is the choice result and `/steps/assess/result/results/1` is the predicate result. The runtime puts results in authored question order. A single `question` stores its native result directly at `/steps/<id>/result`.

## Read the assessment

Every result includes `questionId`, `type`, `answerability`, `answer`, `reason`, and `evidence_strength`. `reason` is nonblank; the runtime instructions ask for a concise reason of at most 400 characters, and the result accepts up to 2000. `evidence_strength` is `strong`, `limited`, or JSON `null`: support for the whole assessment, including abstention. It is not confidence, probability, urgency, or a routing threshold. Strong support can justify an unknown answer.

| `answerability.status` | Meaning | Step status |
| --- | --- | --- |
| `answerable` | Supported answer | `completed` if every question is answerable |
| `partially_answerable` | Supported subset; only labeling and request extraction | `needs_review` |
| `not_answerable` | No supported answer | `needs_review` |
| `undetermined` | Answerability itself is unsettled | `needs_review` |

Any non-answerable status needs at least one `answerability.issues` code: `no_supported_answer` (including missing details or outside-catalog requests), `conflicting_information`, or `multiple_valid_options`. Explain the particular obstacle in `reason`; these are the only machine-readable issue codes. An unanswered choice, ranking, labeling, or request extraction has `answer: null`. A predicate always has an answer object and uses `"unknown"` for uncertainty. An answerable collection may be empty when the source explicitly supports none.

An unresolved decision stops this flow and uses `on_unresolved` if configured. Malformed results, unknown categories and inconsistent answerability are returned to the model for correction up to `output_retries` (default `1`) and then fail with `invalid_output` (`reason: decision_contract`, with `location` `question:<id>` and a `constraint` such as `unknown_option`; see [output retries](../integration/errors.md#output-retries)). Output cut off at `max_tokens` (`output_limit_reached`; a reasoning model's reasoning counts toward it, see [output budget](llm.md#set-the-output-budget-and-reasoning-effort)), timeouts, limits and provider errors fail with their own [codes](../integration/errors.md#canonical-error-codes). A technical failure never takes `on_unresolved`, a `fallback` category or another review route: the run fails. Validation checks shape and membership, not business truth; use [reviewed evaluation cases](../evaluation/task-types.md). See [flow routing](../configuration/flows.md) for issue-specific unresolved routes.

## Advanced options

| Field | Use |
| --- | --- |
| `model` | Override the workflow default by profile name or supported profile override. |
| `sources.<id>.format: json` | Supply an object, array, or other JSON value as evidence. |
| `questions` | Ask 2–64 full questions, each with explicit allowed sources. |
| `fallback` | For one `choice` question only, attach a separate selection for allowed unresolved issues; see [classification](classification.md#fallback-selection). |

Write criteria that state what counts, what does not, and when to abstain. A selected category still depends on the model's assessment.

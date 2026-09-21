# Generate typed decision research data

Use this workflow to prepare examples of decisions over supplied state: choices,
multiple selections, predicates, ordinal scores and request extraction. Each
answer includes a question-specific answerability status and a short explanation
with source citations. Generation uses your local model endpoint, one request at
a time. It does not train a model or fit confidence scores.

## Start a generation run

Install uv and run a local OpenAI-compatible model server. Configure its address
and exact model name in the ignored root `.env`; [`.env.example`](../../.env.example)
shows the supported settings. Model weights must already be available to your
server. The endpoint must use a loopback address or an explicitly permitted
trusted private-network address.

The native recipes target standalone Splash with
`incoai/Qwen3.8-27B-Splash`. They use reasoning effort `low` (reasoning remains
enabled), temperature `0.1`, 8,192 output tokens and a 300-second request timeout.
The root environment overrides YAML; for a server on another machine, set its
actual private-network address and allow private-network access:

```dotenv
FOLIQANT_CURATION_ENDPOINT_URL=http://192.168.2.101:8000/v1
FOLIQANT_CURATION_MODEL=incoai/Qwen3.8-27B-Splash
FOLIQANT_CURATION_ALLOW_PRIVATE_NETWORK=true
FOLIQANT_CURATION_REASONING_EFFORT=low
FOLIQANT_CURATION_TEMPERATURE=0.1
FOLIQANT_CURATION_MAX_TOKENS=8192
FOLIQANT_CURATION_TIMEOUT_SECONDS=300
```

Use your server's IP, or the loopback address in `.env.example` when the server
runs on this machine. Other compatible servers can be configured explicitly;
they must support the selected reasoning setting. Foliqant does not silently
remove the setting or switch models if a request fails.

Each solver request includes only the output types its questions need. Unused
schema branches are omitted to reduce request complexity; the public result
format and full response validation stay unchanged.

Start the full bounded recipe from the repository root:

```sh
./scripts/generate-data
```

Progress appears on an interactive terminal while the final machine-readable
JSON remains on stdout. Use `--progress always` when stderr is redirected but
you still want progress, or `--progress never` to disable it. The display shows
the current phase and run path, completed and total candidates, accepted,
quarantined and reused outcome counts, request-cache entries present when
generation starts, current-candidate elapsed time and periodic heartbeats. It
does not provide an ETA.

The command installs the locked Python environment, downloads or reuses the five
pinned source datasets, prepares native task records, generates and checks local
model responses, and publishes datasets outside Git. No manual record editing is
required. Original source licenses and model-output restrictions stay attached.

The [full recipe](../../model/examples/native-full.yaml) selects up to 2,000
records per source, projects up to 500 compatible records per source, creates
up to four genuine authored cases per scenario and language and permits up to 10,000 candidate jobs, with
at most two attempts each. These are limits, not guaranteed accepted counts.
Examples are connected by semantic template before partitioning: value changes,
paraphrases, translations and counterfactual siblings from one template cannot
cross splits. Reports separate example, unique-task, template-variant and
connected-family counts. `examplesPerScenario` is a cap over the finite catalog;
raising it above the four available cases does not manufacture filler rows. The
report shows the requested cap, available cases and actual selected count. The actual finite plan and upper model-call budget are
written before inference.
A source annotation that cannot preserve the native semantics is excluded from
native projection and counted; it remains available in the auxiliary corpus.

To check your endpoint with a separate 16-candidate bilingual run first:

```sh
./scripts/generate-data --pilot
```

The pilot uses a different run identity and a smaller recipe. It selects eight priority scenarios per language, demonstrating
all five answer types in English and German, not full scenario coverage or financial accuracy. Inspect both accepted
and rejected results. A successful pilot is not a production qualification.

Prepare everything without contacting the model:

```sh
./scripts/generate-data --prepare-only
```

Then run the same command without `--prepare-only` to generate. Add `--offline`
to forbid dataset and environment downloads once both caches are available;
the configured local inference endpoint remains permitted. `--workspace DIR`
selects another data workspace. For custom sizes, copy the recipe and pass
`--config FILE`. Both native recipes select `languages: [en, de]`. To generate
only German, use a custom recipe with `generation.languages: [de]`. German cases
retain German source text, task wording, explanations, missing facts and request
descriptions in the published response dataset. JSON field names, IDs and enum
values remain stable across languages.

The five downloaded source datasets currently supply English records. German
native examples come from explicit authored scenario catalogs, not from
relabeling those imported English records. English/German versions of the same
scenario share a data partition to prevent training/evaluation leakage. Language
tags are declared metadata, not an automatic language detector. Structural
validation applies to both languages; lexical checks recognize supported
English and German forms and remain conservative.

Endpoint values in `.env` override YAML. Optional generation overrides in `.env`
also override recipe sizes, including pilot sizes. Keep run sizes in the recipes
unless you deliberately need an override. Configuration changes create new runs.

### Add the scoped source projections

The ordinary full and pilot recipes prepare BANKING77 intent choices with
versioned category definitions and WANLI three-way text-relation choices:
`entailment`, `contradiction` and `neutral`. A neutral relation is a valid category,
not a claim that the relation question lacks an answer. BANKING77 definitions
are editorial task guidance; they do not make the original labels human-verified
for this native task. Ambiguous categories and questionable labels can still
lead to quarantine. Acceptance means automated checks and reference agreement,
not proof that a source annotation is correct.

These recipes do not automatically project typed-decisions,
MultiDoGO, or TAT-QA into native tasks. To opt into the bounded mappings, first
prepare a plan from a run whose source snapshots and splits are already frozen:

```sh
./scripts/prepare-source-projections \
  --from-run /absolute/path/to/run \
  --pilot \
  --output /absolute/path/to/new-projection-plan
```

The equivalent package command is:

```sh
uv run --project model --no-sync foliqant-model prepare-source-projections \
  --from-run /absolute/path/to/run \
  --pilot \
  --output /absolute/path/to/new-projection-plan
```

`--output` may be omitted to use the command's reported external-workspace
location. Plan preparation reuses the exact frozen source files, families and
splits. It makes zero model requests, including model discovery, and performs no
downloads. Missing or changed inputs fail instead of being reacquired. The
source run remains unchanged. Once those inputs are frozen, you may prepare the
plan while that run is still generating. Do not execute the extension until the
parent run completes.

`--pilot` creates exactly 32 training tasks: eight MultiDoGO tasks, eight TAT-QA
tasks and 16 typed-decisions tasks, using four typed records from each of its
four workflows. Run the normal extension command below with that pilot plan
after the parent completes. To cover the full eligible selection later, prepare
a separate plan from the same parent without `--pilot`. A full plan does not
promise to reuse any model call or outcome from the pilot.

After the parent run is complete, create an immutable extension child:

```sh
./scripts/generate-data \
  --extend-projections-from /absolute/path/to/completed-parent-run \
  --projection-plan /absolute/path/to/projection-plan \
  --progress always
```

The two extension options are a required pair. They cannot be combined with
`--continue-from` or `--repair-from`. Add `--prepare-only` to validate and stage
the extension without endpoint discovery or inference. A full extension starts
only the new blind-verification jobs. Existing outcomes and published records
remain byte-for-byte unchanged in the parent, while the extension is recorded
as a new child. Rerun the exact command to resume that child.

The opt-in plan is intentionally narrow:

- MultiDoGO becomes one intent-only `multiselect` question over its fixed
  18-intent catalog. Raw redacted turns and token-aligned slot labels remain in
  the auxiliary source record; slot labels are not native targets.
- typed-decisions contributes all five questions. Source `choice`, `noul`, and
  `score` map to native `choice`, `predicate`, and `ordinal`. The explicit source
  label is the proposed answer; the converter never chooses the largest teacher
  probability. Raw distributions remain preserved as source data and do not
  become native confidence. Publication requires a blind answer and
  answerability verification.
- TAT-QA contributes at most one table comparison per context. It requires one
  unique row, adjacent unique explicit year headers from 1900 through 2099,
  strict signed-decimal cells, and matching unit markers: both unmarked, the
  same currency on both values, or percentage markers on both values. The native predicate
  compares the displayed values. This does not implement full
  financial question answering and does not use the original gold answer.

Every excluded item is counted with a stable reason. Projections remain English,
inherit the exact source family and split, and create no translated siblings.
Per-source `eligibleMultiIntentRecords` and `selectedMultiIntentRecords` report
how many tasks actually contain multiple selected intents; a multiselect
question alone does not imply that its source contains more than one intent.
Before applying source caps or pilot quotas, preparation groups new candidates
by answer-bearing task identity. A conflicting-target group excludes every new
member, as does an alias that crosses frozen splits or source families. A group
with the same target, family and split keeps the member with the smallest source
record ID as its deterministic representative. Existing baseline native tasks
remain unchanged; matching or conflicting new projections are excluded instead.
The current frozen inputs report 24 MultiDoGO rows excluded by the group-wide
conflicting-target rule. This is an offline preparation count, not evidence of
label quality.
The complete raw English records remain in `source-corpus`, including
distributions and slot annotations that are not native targets. All proposed
labels and accepted rows remain unreviewed research data.
The offline implementation is complete. The 32-task live projection pilot has
not run, and model validation and quality acceptance remain deferred.

### Migrate a completed decision run

Use an explicit migration when a completed native run must be republished under
the current deterministic projection and question-variant recipe:

```sh
./scripts/migrate-data \
  --from-run /absolute/path/to/completed-run \
  --output /absolute/path/to/new-migration
```

The equivalent package command is `foliqant-model migrate-decisions`. Omit
`--output` only when the reported default external-workspace location is
acceptable. Migration is offline: it performs no download, endpoint discovery,
or model request. It verifies and reuses the completed run's immutable sources,
rights, families, and splits, then publishes a separate migration plan and
dataset without changing the parent.

This differs from `--continue-from`. Continuation preserves a compatible run's
existing recipe and finishes missing jobs. Migration applies the named current
deterministic rules and records new ancestry instead of treating changed tasks
as a continuation of old model calls.

The migration can project eligible typed-decisions, MultiDoGO, and TAT-QA rows
from their frozen source annotations. It also creates paired tasks from an
annotation-complete, answerable multiselect: one isolated multiselect and one
single-choice question over the identical state and applicability policy. One
supported label produces that choice; several supported labels produce a null
choice with `not_answerable` and `multiple_valid_options`. The transform does
not choose a priority, infer an absent label, or create a predicate from silence.
English and German prose stay in their record language; machine enums remain
English.

Every migrated row has explicit per-record migration provenance and source
references, while source rights and the frozen family/split remain attached.
`source-annotation`, `deterministic-computation`, `derived-reference`, and
`historical-accepted` describe how a target was obtained. They do not mean a
model verified the new task. Only a successfully rerun and validated pending
task receives `model-verified` evidence status.

Migration leaves unsupported or disputed rows in its reported review list and
keeps eligible unfinished training tasks in a frozen pending queue. Run all or a
bounded selection of that queue separately:

```sh
./scripts/rerun-migrated-data \
  --from-migration /absolute/path/to/migration \
  --limit 8 \
  --job-id native-record-id \
  --progress always
```

The equivalent package command is `foliqant-model rerun-migrated-decisions`.
`--job-id` may be repeated and names a pending record ID. Without `--job-id`,
the command uses the sorted pending queue; `--limit` bounds that selection.
Unlike migration, rerunning performs local model inference. It uses the frozen
model identity without discovery or substitution, stores resumable outcomes in
a separate immutable child, and never submits held-out or review-only rows.
Rerun the exact command after interruption. The final JSON reports the migration,
dataset, artifact and report paths plus record, pending-task and review-item
counts; progress stays on stderr.

## Outputs and resume

The final JSON reports `runPath`, artifact paths, accepted/quarantined counts and
the result report. Use these paths instead of guessing the identity suffix.
Default files live under `~/.local/share/foliqant/curation/<run-identity>/`.

| Output | Purpose |
|---|---|
| `datasets/source-corpus` | Auxiliary records in their original task formats. Do not mistake these for the native training contract. |
| `datasets/native-seeds` | Every deterministic authored parent and source projection; diagnostic and available after preparation. |
| `datasets/native-decisions` | All unchanged held-out native seeds, accepted source projections, and accepted authored rewrites with only the train parents required for lineage. Unattempted and quarantined train parents are excluded. |
| `planned-coverage.json` | Planned jobs and request bound before model execution. |
| `progress.json` | Current completed, accepted and quarantined counts. |
| `coverage.json` | Coverage, source exclusions, rejection reasons and shortages. |

Coverage and cache files use an integrity envelope; their report is under the
`payload` key. All records, requests, responses and model artifacts remain outside
Git. Only configuration, source pins, implementation and documentation belong in
the repository.

`outcomes/<jobId>.json` is the private result ledger. Its `attemptTrace` records
each attempt's status, safe rejection reason and call digests. A call keeps a
32,768-character preview plus `callId`; when `previewTruncated` is true, the
complete final assistant `message.content` is still available in the immutable
`requests/calls/<callId>.json`. `responseSource: absent` is used when the endpoint
provided no final assistant content. The cache does not retain hidden reasoning
or HTTP error bodies as a candidate response.

Press Ctrl+C once to pause safely. During generation, Foliqant finishes and saves
the current candidate, starts no new candidate, releases the run lock and exits
with code `130` and `INTERRUPTED`. During preparation it stops at the next safe
phase boundary. Pressing Ctrl+C a second time stops immediately; the in-flight
request may repeat, and stopping the client does not prove the model server has
cancelled its GPU work.

Rerun the exact same recipe with the same effective `.env` settings and workspace
to resume; there is no `--resume` flag. Completed immutable calls and outcomes
are reused. The progress mode can change without creating a different run. Do
not delete locks, change cached records, or edit rejected outputs to force
acceptance. A changed recipe, prompt, request format, task contract, projection rule or model
identity creates a fresh run; it never mutates or relabels old caches.

To keep completed work after a generator update, explicitly continue from the
old native run:

```sh
./scripts/generate-data \
  --continue-from "$HOME/.local/share/foliqant/curation/<previous-run-directory>" \
  --progress always
```

This creates a child run that keeps accepted results and their original
provenance, retains quarantined results for later repair, and generates only
unfinished candidates. Repeat this same command after a pause. Add
`--prepare-only` to verify and carry completed work without model inference.
Use the same configuration, workspace and model; source data, questions and
train/held-out partitions must still match. Carried records are revalidated, not
silently accepted under changed validation rules. Keep the parent run unchanged;
advancing it produces a different snapshot and child. `--continue-from` cannot
be combined with `--repair-from`. To repair quarantined results later, finish
the continuation and use its reported child path with `--repair-from`.

Requests preserve the schema's declared field order. Some model servers include
the schema in the prompt, so changing its order can change generation. Request
hashes identify the exact bytes sent; artifact files use canonical hashes for
integrity separately.

The adapter reads standard OpenAI streaming responses internally. Your command
still prints progress on stderr and one final JSON result on stdout. Reasoning
text is discarded; only final-answer text and safe provider metadata are retained.
If the model emits 1,024 consecutive JSON whitespace characters outside a
quoted string, the adapter closes that response and records
`long-json-whitespace-run` with the exact partial final answer. This is a
degenerate-output limit, not a syntax rule. The normal bounded repair path can
then correct that phase without accepting or silently rewriting the partial JSON.
Whitespace inside quoted source text does not trigger this limit.

A `TIMEOUT` stops generation when one local request exceeds its deadline. The
300-second default is a per-request limit, not a limit for the whole run. The
progress field `elapsed` measures time on the current candidate, which may need
multiple requests; it is not total run time.

Completed outcomes and cached responses remain saved. Check the model server's
logs or activity display and let its unfinished request finish before rerunning:

```sh
./scripts/generate-data --progress always
```

Keep the same recipe, endpoint settings and workspace. The command reuses saved
work and retries the unfinished call. A timeout is an execution failure, not a
quality rejection, so it does not add a quarantined result. No automatic retry
is sent: disconnecting the client does not guarantee that server-side generation
stopped. If the same candidate repeatedly times out, inspect the server before
retrying again. Increasing the configured timeout creates a separate run.

Fact-preservation checks are deliberately conservative. A valid paraphrase can
be quarantined when lexical markers differ, so a rejection reason alone does not
prove that the model was wrong or that a fact changed. Inspect the original,
candidate and recorded reason together. Do not edit the cached record or bypass
validation; support a new equivalence only through a tested, versioned recipe
change.

Rewrites must change words or their order in at least one selected source.
Exact copies and changes only to spacing, punctuation or capitalization are
rejected with `rewrite-no-wording-change` before a solver call. This saves a
request but does not prove that the new wording is useful or factually correct.
Negation checks recognize common English negative contractions, `cannot`, and `unable`;
percentage checks recognize both `5%` and `5 percent`. These are generic lexical
checks, not an understanding of which claim each marker belongs to.
Numeric limits such as “at least”, “or higher”, “or above”, and “or more” share
an inclusive comparison marker; “at most”, “or lower”, “or below”, “or less”, and
“or fewer” share the opposite marker. With a numeric operand, “exceeds”, “above”,
and “greater than” share a strict greater-than marker; “below” and “less than”
share a strict less-than marker. Strict and inclusive limits, comparison
direction, and negation remain distinct. Incidental wording such as “exceeds
expectations” does not count as a numeric comparison.
German forms include `mindestens`/`höchstens`, strict `über`/`unter` comparisons,
common negation and unit inflections, `pro Monat`/`monatlich`, and dates such as
`28.04.2026` or `28. April 2026`. Quoted anchors inside `„…“` and `»…«` remain
exact. Decimal punctuation stays conservative: changing `65,5` to `65.5` is
rejected rather than guessing a locale. These checks do not cover every German
paraphrase or establish which entity a negation or comparison refers to.

The full recipe requires accepted candidate jobs in every eligible training
scenario/language cell. An authored job accepts a checked rewrite; a projected
job verifies its unchanged source-derived parent and creates no augmentation
duplicate. Held-out-only cells do not create a training shortage. If a required
cell is missing, the command exits with code `4` and `OUTPUT_INVALID`, pointing
to the coverage report. With zero accepted jobs, no `native-decisions` dataset
is published. Valid partial state remains diagnostic. `generatedAccepted` is
the accepted-job count, not a generated-row count. Increasing a budget or changing a recipe
creates a new run; the tool does not retry indefinitely or silently lower the
coverage gate. An all-rejected pilot also fails.

Inspect saved failures before retrying them. A valid response that disagrees
with its reference receives `solver-semantic-mismatch` and needs source/task
review; it is not automatically retried until it guesses the expected label.
For recoverable output errors after a completed attempt, including one that
failed only its coverage gate, use a separate repair run:

```sh
./scripts/generate-data --repair-from /absolute/path/to/curation/parent-run
```

Use the same pilot or custom recipe and workspace settings as the parent. The
child run verifies the parent configuration, recipe, source and native plans,
model metadata, jobs and outcomes before inference. Accepted outcomes remain
byte-for-byte unchanged. Recoverable quarantined jobs receive fresh job, seed and
request-cache identities plus bounded prior rejection feedback; oracle answers
and hidden scenario labels are never feedback. Reference disagreements retain
their quarantine and evidence without another generation call. A valid rewrite
is reused when only its solver response needs correction; a rewrite defect is
corrected in the rewrite phase. Repeating the command resumes
the child. Use the child path as the next `--repair-from` value for a later pass.
If only reference disagreements remain, repair stops before model discovery and
asks for reference review; it does not create another retry-only child.

Changed prompts, source mappings or validation rules require a new recipe run,
not repair of an incompatible parent. Start with `./scripts/generate-data --pilot`
and review a bounded source check before a full run. Existing artifacts remain
available at their original paths, and verified source downloads can be reused.
Do not copy old outcomes into changed tasks or delete the old run. The standard
16-job pilot covers authored cases; it does not qualify all imported mappings.

For truncated responses, inspect the retained final content, finish reason and
optional provider token usage before changing token limits. Missing usage means
unavailable, not zero. Repeated control-character output is not evidence that a
larger output allowance will fix the problem.

If you repair before extending source projections, prepare a new projection
plan with `--from-run` pointing to the completed repair child. A plan is bound
to its exact parent; a plan prepared from an earlier continuation cannot be
applied to a later repair child. Run repair and the 32-task extension pilot
sequentially, retaining low reasoning and temperature `0.1`.

Each authored seed declares which non-metadata state sources may be rewritten;
all other sources and the complete question contract stay fixed. Ordinary
authored cases expose all of their non-metadata sources. Whole-answer adequacy
cases expose only `original-state`, leaving `task-contract` and
`proposed-answer` byte-for-byte unchanged so generation cannot change the object
or rubric being assessed. Projected verification rewrites nothing. Any change
to this mutability policy creates a fresh run identity.

Verify a published dataset using its printed path:

```sh
uv run --project model --no-sync foliqant-model verify /absolute/path/to/native-decisions
```

After inspecting data quality and coverage, use that artifact with the existing
[training workflow](train-and-customize.md). Generation never starts training.

## Define categories with clear boundaries

Use `choice` for one category and `multiselect` for several independently valid
labels. Each option needs an `id` and a detailed `description`: state what belongs
in it, what is excluded, and how it differs from neighboring categories.
Question-level `criteria` explain the selection rule for the complete catalog.

For new catalogs, validate your configuration with `CategoryCatalog` before
building a question:

```python
from foliqant.decisions.category_catalog import CategoryCatalog
from foliqant.decisions.contracts import ChoiceQuestion

catalog = CategoryCatalog.model_validate({
    "categories": [
        {
            "id": "incident",
            "description": (
                "Reports an existing malfunction or unexpected behavior and asks "
                "for it to be resolved. Excludes instructions requested without "
                "a reported malfunction."
            ),
        },
        {
            "id": "information_request",
            "description": (
                "Asks for facts, documentation, or instructions. Excludes a "
                "reported malfunction requiring repair and a pure confirmation."
            ),
        },
        {
            "id": "confirmation",
            "description": (
                "Only acknowledges or confirms an earlier statement or action; "
                "does not add a new question, incident, or requested action."
            ),
        },
    ],
})
question = ChoiceQuestion(
    id="request_kind",
    type="choice",
    prompt="Which request kind does the current message express?",
    criteria=[
        "Use the category definitions and the message evidence.",
        "If multiple categories apply, report multiple_valid_options; do not force a winner.",
    ],
    allowedSourceIds=["message"],
    options=catalog.decision_options(),
)
```

`CategoryCatalog` normalizes category IDs to lowercase snake_case:
`Information Request` and `information-request` become `information_request`.
Canonical IDs match `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`. Collisions after normalization,
unrepresentable IDs and whitespace-only descriptions are rejected. Descriptions can be
English or German while the same English machine keys stay stable.
See the [catalog schema](../../contracts/model/category-catalog.schema.json).
The normalizer accepts printable ASCII IDs up to 128 characters, lowercases
letters, replaces each run of punctuation/spaces with `_`, and removes outer
separators. The result must start with a letter. Unicode letters and control
characters in IDs require an explicit caller mapping; they are not silently
discarded. The exported schema describes raw input: JSON Schema itself does not
normalize values or check collisions after normalization.

For independent labels, pass the same options to `MultiselectQuestion` and set
`minSelections` and `maxSelections` explicitly. Keep topic, request kind and
priority as separate questions when they represent different dimensions. Two
labels may describe one request; two requests in the same category may require
two `request_units` instead. Priority needs a supplied rubric. A deadline needs
source evidence and, for relative dates, reference time and timezone; SLA
arithmetic belongs in application code.

Answers return your exact option IDs plus answerability, a concise explanation
and citations. Resolve display descriptions from the original catalog rather
than asking the model to regenerate them. `catalog.resolve_id(raw_id)` recovers
formatting variants through the same normalization and checks exact membership;
it rejects an unknown category instead of guessing. Validate the resulting output with
`validate_decision_output` against its `DecisionInput`; syntactically valid IDs
that are absent from the question catalog are still invalid. Current V1 attaches
classification evidence to the question result, not individually to every label.

Catalog validation is an authoring boundary. Existing V1 artifacts keep their
original identifier rules, schemas and recipe identity so completed data can be
verified, repaired and extended without renaming stored answers. Importing an
external taxonomy into a new catalog requires an explicit collision-checked
mapping; this helper does not migrate datasets or configure the workflow runtime.

## Handle unreliable input in application code

Native results contain a result for each question. Code uses stable status and
issue codes; explanation text is for people. For example, the same refund email
can answer “Is a refund requested?” while failing to identify the transaction.

```json
{
  "schemaVersion": 1,
  "results": [
    {
      "questionId": "transaction",
      "type": "choice",
      "answer": null,
      "answerability": {
        "status": "not_answerable",
        "issues": ["missing_information"]
      },
      "explanation": {
        "summary": "The request does not identify which transaction is affected.",
        "evidence": [],
        "contraryEvidence": [],
        "missingFacts": ["A transaction reference"]
      }
    }
  ]
}
```

`explanation.summary` is required and can contain at most 400 characters. The
generation prompt asks for one grounded concise reason and aims for 160
characters or fewer; it permits a second sentence only for a decisive
limitation. Oversized summaries are rejected rather than truncated. This limit
does not apply to citation quotes, `missingFacts` text, or the complete
explanation object.

Use a concise paraphrase for the summary and put verbatim quotations in citation
fields. Escape quotation marks, backslashes and control characters when writing
JSON strings. After JSON decoding, each citation must match the source exactly;
the generator does not silently repair malformed JSON or change quoted evidence.

Validate JSON with [the output schema](../../contracts/model/decision-output.schema.json)
and then validate its question IDs, allowed answers and citations against
[the input contract](../../contracts/model/decision-input.schema.json). Python
callers can use `DecisionInput`, `DecisionOutput`, and
`validate_decision_output` from `foliqant.decisions.contracts`.
The validator checks structure and declared references, not semantic truth.
Partial collections must contain at least one supported item and an explanation
evidence citation. A missing part of a requested collection prevents a complete
answer, even when every visible item is clear. Report each issue code once and
describe distinct missing facts in the explanation.

For request units, `subject` identifies the target rather than repeating the
action or its type. A document type such as “warranty certificate” is not an
identifier; an explicit serial number or coverage period can be. Quoting a
generic object does not make it specific. Use `null` when there is no identifying
reference, and do not require one for an otherwise clear request. Every non-null
subject must appear verbatim in that unit's own evidence. The validator checks
that literal correspondence; it does not use a list of generic object names to
decide what is specific.

An illustrative application policy after validation is:

```python
if result.answerability.status == "answerable":
    # Apply your separate evidence, risk and authorization checks before acting.
    handle_proposed_answer(result.answer)
elif "missing_information" in result.answerability.issues:
    request_missing_information(result.explanation.missingFacts)
else:
    send_for_review(result)
```

The application functions above are your own policy, not a Foliqant workflow
runtime. Do not branch on the wording of `summary` or `missingFacts`.

| Status | Meaning |
|---|---|
| `answerable` | The predicted assessment says the question can be answered from its allowed evidence. |
| `partially_answerable` | A collection question has some supported results and an unresolved part. Do not treat the collection as complete. |
| `not_answerable` | The supplied information or question constraints do not support a substantive answer. |
| `undetermined` | The assessment could not establish answerability. |

The four generic issue codes are `missing_information`, `conflicting_information`,
`multiple_valid_options` and `no_matching_option`. Missing attachments and unclear
references use `missing_information`. `multiple_valid_options` means two or more
catalog options are positively supported but the question cardinality cannot
represent them all. Results include every independently supported issue. Several clear
requests are answerable when the question allows a collection; their mere
presence is not an issue. A permitted no-match answer can be
answerable. A returned request unit without a catalog category includes
`no_matching_option`. A conditionally stated unit remains `conditional` even
when its predicate is separately resolved; extraction records the gate and does
not execute a branch. A predicate uses `{"value":"unknown"}` when neither true nor false is
established. When an allowed source explicitly says an item is absent, a
presence predicate is answerable `false`; unknown is not false. Model/server failures are technical errors,
not valid answerability results.

## Quality boundary

All native authored, projected and generated records are unreviewed research
data. Automatic checks are useful filters but do not establish financial or
legal accuracy. The model can still miss a request or cite irrelevant text.
The response contract describes what to learn; it does not prove a checkpoint
has learned it.

Native outputs do not contain model-written confidence percentages. Trustworthy
numerical answerability or answer-adequacy estimates need independently labelled
data, held-out calibration and a separate audit. Generated diagnostic partitions
must not be relabelled as human-gold calibration or production evaluation data.

Explanations connect criteria to supplied evidence. They do not certify a
faithful transcript of the model's internal reasoning. Published explanation,
request-description and missing-fact prose comes from the corrected reference
and is mapped to exact candidate source text; unchecked solver rationale is not
used as a training target. This mapping checks exact sources, quotes and
subjects, not semantic truth. Several requests are not
automatically ambiguous, and conditional branches must not be executed as
independent actions. Authorization and business prerequisites remain application
responsibilities.

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

After a completed attempt, including one that failed only its coverage gate,
retry quarantined jobs in a separate model turn:

```sh
./scripts/generate-data --repair-from /absolute/path/to/curation/parent-run
```

Use the same pilot or custom recipe and workspace settings as the parent. The
child run verifies the parent configuration, recipe, source and native plans,
model metadata, jobs and outcomes before inference. Accepted outcomes remain
byte-for-byte unchanged. Only quarantined jobs receive fresh job, seed and
request-cache identities plus bounded prior rejection feedback; oracle answers
and hidden scenario labels are never feedback. Repeating the command resumes
the child. Use the child path as the next `--repair-from` value for a later pass.

Each authored seed declares which non-metadata state sources may be rewritten;
all other sources and the complete question contract stay fixed. Ordinary
authored cases expose all of their non-metadata sources. Whole-answer adequacy
cases expose only `original-state`, leaving `task-contract` and
`proposed-answer` byte-for-byte unchanged so generation cannot change the object
or rubric being assessed. Projected verification rewrites nothing. Any change
to this mutability policy creates a fresh run identity.

Verify a published dataset using its printed path:

```sh
uv run --no-sync foliqant-model verify /absolute/path/to/native-decisions
```

After inspecting data quality and coverage, use that artifact with the existing
[training workflow](train-and-customize.md). Generation never starts training.

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

Validate JSON with [the output schema](../../contracts/model/decision-output.schema.json)
and then validate its question IDs, allowed answers and citations against
[the input contract](../../contracts/model/decision-input.schema.json). Python
callers can use `DecisionInput`, `DecisionOutput`, and
`validate_decision_output` from `foliqant_model.curation.decision_contracts`.
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

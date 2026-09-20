# Setup and data operations

## Contents

- [Native decision data](#native-decision-data)
- [Existing setup and auxiliary curation](#existing-setup-and-auxiliary-curation)

## Native decision data

`./scripts/generate-data` selects the full native recipe;
`./scripts/generate-data --pilot` uses a separate 16-candidate bilingual recipe (eight priority scenarios per language).
`--prepare-only` performs no inference. Both reuse the configured local endpoint,
serial cached calls, source acquisition and immutable publication. Run sizes
belong in YAML; any generation overrides left in root `.env` also affect pilot
sizes. Keep the endpoint/model explicit when discovery returns multiple models.

Native full/pilot profiles select standalone Splash, model
`incoai/Qwen3.8-27B-Splash`, `endpoint.reasoningEffort: low`, temperature `0.1`,
8,192 output tokens and a 300-second timeout. Root `.env` overrides these via
`FOLIQANT_CURATION_REASONING_EFFORT`, `FOLIQANT_CURATION_TEMPERATURE` and the
existing endpoint keys. Use the exact configured endpoint rather than assuming
LM Studio's port. Low retains reasoning; do not substitute disabled reasoning or
silently escalate to xhigh. Generic endpoint configuration leaves effort omitted
unless explicitly supplied. If another server rejects the parameter, surface the
failure rather than silently dropping it. Effort and temperature changes must
change configuration/request/cache identity; resume only unchanged settings.

Preserve declared schema order through worker IPC, prompt-mode text and HTTP
requests. Do not reuse sorted artifact serialization for model prompts. Request
format `declared-schema-order-v2` hashes exact HTTP bytes, binds that hash into
call identity, and versions both generation recipes. It creates new runs;
earlier caches and outcomes remain untouched and must not be copied into a new
run as if generated with its prompts.

### Scoped offline source projections

Default native generation continues to project only the legacy compatible
sources. typed-decisions, MultiDoGO and TAT-QA native mappings require an
explicit immutable plan:

```sh
./scripts/prepare-source-projections --from-run /absolute/path/to/run --pilot \
  --output /absolute/path/to/new-projection-plan
```

The package command `foliqant-model prepare-source-projections` accepts the same
options. Omit `--output` only when the reported default external-workspace path
is acceptable. Preparation must reuse the exact frozen source snapshots,
families and splits. It makes zero model requests, including `/v1/models`
discovery, and performs no downloads. Reject missing, mutable or inconsistent
inputs instead of reacquiring them.

Preparation may run while generation is active once those source inputs are
frozen. Resolve whole answer-bearing task groups before applying source caps or
pilot quotas. Exclude every new member when targets conflict or an alias crosses
frozen splits or source families. For a same-target group within one family and
split, retain the member with the smallest source record ID and count the other
members as duplicate exclusions. Existing baseline native tasks remain
unchanged; they only cause matching or conflicting new projections to be
excluded. The current frozen inputs produce 24 MultiDoGO `conflicting-target`
exclusions. That offline count is not model validation or a quality claim.

`--pilot` selects exactly 32 training tasks: eight MultiDoGO, eight TAT-QA and
16 typed-decisions tasks, with four typed records per workflow. The plan may be
prepared before generation ends, but apply it through the normal extension
command only after parent completion. Prepare the full
selection independently from the same parent by omitting `--pilot`. Never imply
that a full extension reuses pilot model calls or outcomes. Model validation is
deferred, and the live projection pilot has not run. Offline implementation is
complete; no quality acceptance is claimed.

Apply the plan only after its parent is complete:

```sh
./scripts/generate-data \
  --extend-projections-from /absolute/path/to/completed-parent-run \
  --projection-plan /absolute/path/to/projection-plan \
  --progress always
```

Require both extension options. Reject either one alone and reject combinations
with `--continue-from` or `--repair-from`. `--prepare-only` is valid for a
completed parent and must not discover or call a model. Full execution creates
only the new blind-verification jobs in an immutable child. Preserve all parent
outcomes and published rows byte-for-byte; repeating the same command resumes
the child. Never enable this behavior implicitly for normal generation.

Keep the mappings exact:

- MultiDoGO: one intent-only native `multiselect` over the fixed 18-intent
  catalog. Preserve raw redacted text and aligned slot labels in source data;
  never project slots.
- typed-decisions: project all five questions. Map source `choice`, `noul` and
  `score` to native `choice`, `predicate` and `ordinal`. Use the explicit source
  `label`, never a distribution argmax. Preserve raw teacher distributions, but
  never convert them or teacher agreement into native confidence. Treat the
  label as a proposed answer and require blind answerability plus answer
  verification.
- TAT-QA: at most one displayed-value comparison per context. Require a unique
  row, adjacent unique explicit 1900–2099 year headers, strict signed decimals,
  and matching unit markers (the same currency on both, or percent on both). Do
  not infer broader financial QA or use the original source gold answer.

Count every exclusion with a stable reason. Preserve all raw English records and
annotations in `source-corpus`; derived native tasks remain English, inherit the
frozen family/split, and create no translations. These projections remain
unreviewed research data.

For an interrupted native run after a generator update, use explicit
`./scripts/generate-data --continue-from /absolute/path/to/old-run --progress always`.
It carries completed accepted AND quarantined outcomes unchanged into a child,
keeps original IDs/prompt provenance, and generates only missing jobs. Repeat
the same command to resume. `--prepare-only` verifies/carries without inference.
Configuration, model, sources, seeds and frozen partitions must match; carried
records undergo current semantic checks. The parent remains untouched; the
snapshot/current recipe are recorded in `continuation.json`. Do not combine
with `--repair-from`; once the child finishes, its quarantined outcomes can be
repaired separately using the child path. Generic non-native curation does not
support this option. Do not tell users to discard valid data solely because the
request format changed, and do not manually transplant outcomes to bypass checks.

Native records contain source-addressable state, typed caller questions, answers,
question-relative answerability and concise cited explanations. Raw source tasks
stay in `source-corpus`; every native parent stays in diagnostic `native-seeds`.
`native-decisions` contains all held-out native seeds, accepted source
verifications, and accepted authored rewrites with only their required train
parents. Unattempted and quarantined train parents do not enter it. Never
silently rename the raw teacher distributions or slot labels as native examples.
Unsupported projections must be counted. Authored oracle facts precede prose
generation; blind checking never receives target answers or scenario labels.
Only train families may be submitted to generation. The authored recipe supports English and German through complete, explicit
prose catalogs. Missing translations fail closed. Preserve German sources,
questions, explanations, missing facts, request descriptions and exact evidence
in German records; keep machine IDs/enums stable. English imported sources must
not be relabeled German. Language metadata is not language detection. Value variants, counterfactuals, paraphrases and
translations from one semantic template remain connected across partitioning;
`examplesPerScenario` is only a cap over the finite genuine-case catalog. The
initial catalog has four cases per scenario and language; larger caps do not justify filler,
numeric repetitions or alternate wrappers. Planning must report requested,
available and actual counts. All derived records retain source/model rights.

Every authored seed declares its mutable non-metadata source IDs. Ordinary
authored cases allow all non-metadata sources; adequacy cases allow only
`original-state` and keep `task-contract` plus `proposed-answer` fixed. Projected
verification has no mutable sources. The question contract is always fixed.

Answerability is a predicted input property, not model certainty, answer
correctness, execution permission, or a calibrated number. Preserve the concise
generic issue enum. Clear multiple requests are valid when the question permits
them; ambiguous alternatives and conditional branches are not simultaneous
actions. A null request category requires `no_matching_option`. A conditionally
stated unit remains conditional after its predicate is evaluated because
extraction records the gate rather than executing it. Same-category requests
must retain distinct request identity.

Use one grounded concise reason for `explanation.summary`, aiming for 160
characters or fewer. A second sentence is only for a decisive limitation, and
the hard contract maximum is 400 characters. Never truncate a summary; invalid
oversized output stays in the ordinary rejection and repair flow. Citation
quotes, `missingFacts`, and the complete explanation object do not inherit the
summary bound.

Coverage gates count accepted candidate jobs, not published rows or attempts.
An accepted projection verifies and publishes the exact parent once, without
generation provenance; it is verification rather than augmentation. An accepted
authored job publishes a canonical reference-derived target, not unchecked
solver rationale. Missing
required coverage exits with `OUTPUT_INVALID` and retains diagnostic progress;
never lower the gate, edit a cached outcome, or present partial output as success.
A pilot also needs at least one accepted job. Zero acceptance publishes no
`native-decisions` dataset. The exact same command resumes cached work; changed
configuration, prompts, task contracts or projection rules create a fresh run
identity and never mutate or relabel old caches.

For a separate correction pass, use `./scripts/generate-data --repair-from RUN`
(or `./scripts/curate-data --repair-from RUN` for generic curation), retaining
the parent's pilot/custom recipe, workspace and effective environment settings.
Only quarantined jobs are called again; accepted rows are carried unchanged into
an immutable child. The same command resumes that child, and using the child as
`RUN` starts another pass. The parent must have finished all jobs; a coverage
failure is allowed, a paused/incomplete run is not. Config, recipe, model and
frozen plans must match. Do not alter old cached responses or imply that missing
historical response text can be recovered. Publication still requires ordinary
validation and coverage, and generated rows remain unreviewed.

Projection plans are bound to one exact parent. After repairing a run, prepare
fresh pilot/full plans from the completed repair child; do not apply an earlier
continuation's plan to it. Run repair and projection verification sequentially.

Lexical fact-preservation guards are intentionally conservative. A valid
paraphrase can be quarantined when surface markers differ; the rejection alone
does not prove model error or factual drift. Inspect the immutable source,
candidate and recorded reason together. Never edit the cached record or bypass
validation. Add a supported equivalence only as a tested, versioned recipe
change.

Keep validation domain-independent. Do not add record IDs, scenario names,
dataset phrases or expected answers to validator rules. A request subject is an
identifying reference, not an action or document/product type, even when that
type has a purpose modifier. Quoting a generic object does not make it specific;
use null without inventing an identifier. Every non-null subject belongs in that
same unit's evidence. Partial collections need supported items and an explanation
citation; do not promote them to complete collections because visible items are
clear. Report each applicable issue once.

Generated rewrites need a changed sequence of case-insensitive Unicode word
tokens in at least one selected source. Exact copies and formatting-only changes
fail with `rewrite-no-wording-change` before solving and during derivative
revalidation; original seeds and verification-only records need no rewrite.
Common negative contractions, `cannot`, and `unable` count as negation markers; attached
percentage symbols count as units. These bounded checks neither establish useful
diversity nor prove semantic preservation. Cover each new equivalence with
positive and adversarial tests across domains, retain blind solving and reference
targets, and record fresh prompt comparisons separately from validator replays.
Never call a deterministic regression test a measured model-quality improvement.
Numeric-bound inclusive `or higher`/`or above`/`or more` markers match `at least`;
`or lower`/`or below`/`or less`/`or fewer` match `at most`. With numeric operands,
`exceed`/`exceeds`/`above` match strict greater-than, and `below` matches strict
less-than. Incidental nonnumeric wording does not match; strict/inclusive
boundaries, comparison direction, and negation stay distinct.
German supported forms include numeric-bound `über`/`unter`, `mindestens`/
`höchstens`, common negation and unit inflections, `pro Monat`/`monatlich`,
`DD.MM.YYYY` dates, and exact `„…“`/`»…«` anchors. Do not infer equivalence between
ambiguous decimal separator formats or claim complete German entailment. Replaying saved outputs after a guard change is
validator evidence, not a fresh generation or prompt-quality result.

Published descriptions, explanations, missing facts and citations come from the
corrected reference and deterministic exact source/quote/subject mapping. This
prevents solver-added requirements from becoming targets but does not prove
semantic truth. Source labels, blind agreement and all published rows remain
unreviewed research data. Run a bounded real pilot for every configured language,
inspect both source and response prose, verify publication, and verify immutable
resume. Keep execution evidence separate from validation replays. Neither a pilot
nor teacher agreement establishes population accuracy, full-recipe coverage,
training readiness or production fitness.

`--progress auto` writes phase, safe run path, completed/total, accepted,
quarantined, reused outcomes, request-cache entries present when generation
starts, current-candidate elapsed time and heartbeats to interactive stderr while
stdout stays final-JSON only; it provides no ETA. `always` forces and `never`
disables the display; the choice is outside run identity. The first Ctrl+C finishes and persists the current
candidate, or reaches a safe preparation boundary, releases the lock and exits
`130`/`INTERRUPTED`. Resume with the exact same recipe, effective `.env` and
workspace. A second Ctrl+C can leave the current request to repeat and does not
prove the endpoint stopped processing. There is no `--resume` flag or paused
success schema.

`TIMEOUT`/exit 4 stops at a per-request deadline, not a whole-run deadline.
Completed calls and outcomes remain reusable; an unfinished transport request
is not a quality rejection. Check the server has finished it before resuming
with unchanged settings. Do not blindly retry in a loop, silently skip a
candidate, or increase the configured timeout and claim the original run will
resume: changing that setting creates a new run.

Generated data is never human-gold calibration or a trustworthy score by itself.
The existing `calibrate` command selects a token-likelihood threshold; it does
not fit an input-answerability estimator. Model training, assessor fitting and
production qualification remain separate activities. See
`docs/guides/native-decision-data.md` in the checkout for the public workflow.

## Category catalogs

Use `CategoryCatalog` from `foliqant_model.curation.category_catalog` to author
new categories with `id` and nonblank `description`, then `decision_options()`
to build existing choice/multiselect/request-unit questions. Explain category
inclusion, exclusion and neighboring boundaries in descriptions; do not rely on
keys alone. Keep priority, request kind and topic as separate questions when
they express different dimensions. A date in an input is not automatically an
SLA deadline; policy-based date calculation remains deterministic application work.

Normalize recoverable key formatting deterministically (`Request Info` and
`request-info` become `request_info`) and reject collisions. Preserve German
descriptions while keeping stable English machine keys. `resolve_id` applies
normalization plus exact catalog membership to a returned key; unknown categories
remain invalid. Do not fuzzy-match, invent categories, or silently drop duplicate
selections. `validate_decision_output` still validates the whole result against
the exact task, including allowed IDs, cardinality and citations.

This is additive authoring support. The V1 artifact reader, source-projection
labels, output schemas and recipe digests retain their original semantics.
Never rewrite a completed run to impose new identifier rules. Catalog display
descriptions come from caller configuration; explanations and evidence come from
validated results. Per-label evidence binding, bounded extraction and process
branching remain planned rather than implemented service capabilities.

Source cards determine annotation provenance. A third-party model's claim of
human training data does not override a dataset publisher describing synthetic
records. Source priority/type labels are provisional references under that
source's definitions, not universal urgency or request-intent gold. Preserve
the distinction between authored, synthetic, teacher and human-reviewed data.

## Existing setup and auxiliary curation

Run from a Foliqant checkout with uv installed:

```sh
./scripts/setup-model
```

This installs the locked local environment, downloads pinned files, verifies their
hashes, and prepares an upstream checkpoint plus separate shared/customer data.
It does not train. Use the JSON result's paths rather than reconstructing the
profile directory name. Rerun with `--offline` only after both assets and Python
build dependencies are cached. Missing offline inputs fail without downloading.
`--workspace DIR` selects a different data location.

For unattended public-source curation:

```sh
./scripts/curate-data --prepare-only
# Start LM Studio with one loaded chat model, then resume:
./scripts/curate-data
```

The checked-in recipe selects BANKING77, typed-decisions, WANLI plus the SemIf
held-out selection, MultiDoGO finance and TAT-QA, capped at 1,000 records per
source. Assets total about 46.21 MiB and are cached under
`~/.local/share/foliqant/curation-downloads`. Data and run outputs stay outside
Git under `~/.local/share/foliqant/curation`.

Preparation downloads, verifies, converts and freezes source partitions without
contacting LM Studio. Full curation resumes that run, records the endpoint model
metadata, runs at most 100 local generation jobs and publishes `source-corpus`,
`augmented-corpus` and `synthetic-regression` dataset artifacts as applicable.
It does not train. All teacher and generated records remain unreviewed and
diagnostic; automatic agreement is not human adjudication.

Current augmentation uses eligible BANKING77, WANLI and TAT-QA training
families. typed-decisions soft targets and MultiDoGO token-aligned slot labels
remain in the source corpus but are excluded from automatic augmentation because
the current checker cannot reliably preserve those reference semantics.

Augmentation rewrites only BANKING77 request text, WANLI claims or TAT-QA
questions. Code retains catalogs, evidence, tables and paragraphs, and preserves
earlier conversation messages. Scenario wording keeps literal evidence, dates
and numbers. Do not admit role/message wrappers, copied system rules or changed
label catalogs to increase the acceptance count. Inspect both accepted and
quarantined samples: a passed checker is not sufficient evidence of clean input.
Prompt changes create new run identities; do not relabel earlier artifacts.

Task conditioning belongs in the ordinary system instruction used consistently
for training, evaluation and inference. A broad task name can guide generation;
never insert the target label, scenario name or expected answer into its input.
Do not add special tokenizer tokens or a state prefix without a matched-format
experiment. The generator request envelope is not the model's training content.

Do not infer generative-task improvements from an embedding model's retrieval
prefix results. For prefix experiments, freeze the exact formats, original
validation families, paired request order, decoding controls, metrics and adoption
rule before inference. Never select calibration or test records for prompt tuning.
Report regressions and failed outputs as well as gains; retain the current format
when the bounded experiment does not demonstrate a benefit. An inference-only
comparison does not establish that training with that prefix improves the model.

The example omits `endpoint.model`, which requires exactly one discovered local
model. If LM Studio exposes several, copy the YAML and set the exact model ID.
The wrapper also reads the ignored root `.env` using the allowlisted settings in
`.env.example`. Use it for the exact endpoint/model and bounded generation limits.
`FOLIQANT_CURATION_ALLOW_PRIVATE_NETWORK=true` explicitly allows a trusted
RFC1918 or IPv6 unique-local server. Loopback is the default; public endpoints,
DNS names, proxies and redirects remain rejected.
Rerun with the same configuration and workspace to resume; changing the
configuration creates a new run, and changed stored model metadata is rejected.
`--offline` forbids remote asset and dependency downloads but still permits the
configured local endpoint. It succeeds only after every selected asset and uv
dependency is cached. Do not replace missing assets with toy records or manually
edit rejected candidates.

For exact source licenses, exclusions and limitations, read
`docs/guides/automated-curation.md` in the checkout. CUAD, gated assets, model
weights, WANLI worker annotations, MultiDoGO unannotated dialogues and TAT-QA's
unlabeled test serialization are outside the current catalog.

For custom local data:

```sh
uv run --no-sync foliqant-model prepare --config /absolute/dataset.yaml --output /absolute/new-dataset
uv run --no-sync foliqant-model verify /absolute/new-dataset
```

Dataset configuration is strict UTF-8 YAML/JSON with schemaVersion 1 and name.
Each sources entry needs id, path, license, licenseEvidence, trainingAllowed true,
redistributionAllowed and privacy. sharedTrainingAllowed defaults false;
commercialUse defaults unknown; restrictions defaults empty. Private sources
require authorizationRef; public sources omit it. Evidence references must not
embed secret agreement contents. Do not infer permission from data availability.

Paths resolve relative to the configuration. JSONL records need schemaVersion 1,
globally unique id, matching sourceId, language, nonempty groupKeys, messages,
origin human/synthetic/teacher, and reviewed boolean. tags is optional. Permit one
leading system message, then alternate user and assistant; final assistant is the
supervised answer. Do not insert tools, images or executable payloads into this
text-only format. Synthetic/teacher/unreviewed data is always diagnostic.

At least four independent connected groups are required. Group related threads,
translations and document versions deliberately. Default held-out fractions are
0.1 each for validation/calibration/test, with at least one group per partition;
the rest train. Preparation normalizes Unicode/newlines, rejects duplicate IDs and
conversations, and preserves declared group relationships. It is not semantic
deduplication, PII detection or proof against unknown upstream contamination.

Success is one JSON object with ok true. Exit categories: 2 input, 3 environment,
4 execution, 5 integrity/lineage, 130 interruption. Inspect errors before retrying;
do not silently repair altered cached files or change a manifest to match them.
For future commercial use, inspect source model and dataset ancestry; additional
permission or rebuilding from an unaffected ancestor may be necessary.

For a local endpoint that emits empty final content under guided grammar, an explicit
`endpoint.structuredOutput: prompt` curation setting omits server-side response_format
while retaining strict local JSON Schema validation. Never consume reasoning_content,
automatically switch models/modes, or claim invalid output passed.

Private candidate outcomes keep a per-attempt status, safe reason and call trace.
The trace preview is capped at 32,768 characters and points by `callId` to the
immutable call-cache entry holding the complete final assistant content. An absent
final response stays absent; hidden reasoning and transport error bodies are not
substituted for it. Published training artifacts still contain accepted records only.

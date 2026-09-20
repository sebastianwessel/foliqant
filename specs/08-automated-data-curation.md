# Automated local dataset curation

Status: implemented with local source and inference evidence; scoped prompt and
input validation refinements are recorded in
`plans/reviews/curation-prompt-optimization.md`. This extends the completed
model-tooling scope. The user supplies a local LM Studio model and requires no
manual data preparation or
labeling. No paid API, cloud fallback, private-data upload, model download or
commercial/financial qualification is authorized by this work.

## Required outcome

One resumable command acquires pinned public data, converts it into the existing
DataRecord representation, freezes related-source splits, uses a configured local
model to generate and check additional cases, automatically accepts or quarantines
candidates, and publishes a verified dataset plus a coverage/provenance report.
Preparation must work while the model server is unavailable. After it is ready,
continuing the same command reuses completed work. The user must not write labels,
edit records, sort a review queue or build source converters.

Automatic acceptance means research training material, not human validation.
Generated/teacher records remain origin=synthetic/teacher and reviewed=false.
Unsupported or ambiguous candidates are quarantined automatically; the run reports
coverage shortages explicitly instead of fabricating accepted examples. Neither
agreement between model calls nor passing JSON Schema establishes correctness.
No generated case contributes to a claimed representative human-gold final audit.

## Initial sources and generation coverage

Provide pinned connectors for BANKING77, LocalLLaMA/typed-decisions, WANLI,
MultiDoGO finance and TAT-QA, subject to actual public source availability and
recorded permissions. Every unavailable source is an explicit failed/pending
source; no silent substitution by toy records. Source research records revisions,
download checksums/sizes, terms and schema evidence before adapters are implemented.
Research/noncommercial terms are allowed where the activity is permitted.

Keep original train/dev/test designations, document/thread/premise families,
source IDs and annotations. Annotation distributions are reference observations,
not operational confidence. Preserve source labels where semantically compatible;
do not manufacture evidence spans absent from the source.

Support: (1) source-grounded questions using existing reference answers/evidence,
(2) wording/irrelevant-context/category-order variants of source training cases,
and (3) explicit authored scenario families: withdrawn requests, multiple intents,
missing evidence, conflicting information and changed deadlines. Scenario facts
and expected decisions are defined in code before the model writes the prose.
Dates/arithmetic/units use deterministic checks where applicable. External rules
and documents are untrusted data, never executable instructions or current legal
truth inferred from model memory. English is default; German is an explicit
language option whose translations share source-family identity.

## CLI and operation

Add `foliqant-model curate --config <yaml> [--workspace <path>] [--prepare-only]
[--offline] [--progress auto|always|never]`. Default workspace is the existing
external Foliqant data root.
`--prepare-only` downloads/converts/splits and produces a ready-to-generate report
without inference. `--offline` forbids remote dataset downloads; configured
local inference remains allowed. No training begins automatically.

Configuration and source pins determine the run identity. Model identity is
resolved before generation and persisted. Resume refuses a changed configuration,
source hash, prompt/schema revision or resolved model identity. If the server
cannot expose an immutable model fingerprint, record that limitation explicitly;
a model name must never be presented as a verified weight digest. Reusing cached
calls does not claim fresh generation is bit-reproducible.

Each request is bounded by timeout, output bytes, tokens, retries and total run
candidate budget. Initial concurrency is one. Retain per-item successful request
and validation results with canonical hashes; interrupted items can be retried
without regenerating completed items. A scoped exclusive lock prevents concurrent
writers. A stale/uncertain owner is not silently deleted. Corrupt results fail
integrity checks instead of being overwritten. Successful publication is atomic
and immutable; new configurations create new runs.

Local artifacts and reports are private files outside Git. Console JSON contains
counts, paths and safe errors, not source content or secrets. Code/configuration,
schema, source pins, tests and docs belong in Git. Cancellation must close local
connections and retain resumable completed work. Closing a connection does not
prove the server has cancelled GPU work; document that boundary.

A generation timeout stops the run with `TIMEOUT` (exit 4), without an automatic
transport retry or a fabricated quarantine outcome. The error explains that
completed work is saved, the deadline is per request, and resumption requires
unchanged settings after checking that the server has finished the previous
request. Existing request and run identities remain unchanged by this diagnostic.

Generation request format `declared-schema-order-v2` preserves JSON Schema key
order through worker IPC, prompt-mode schema text and HTTP serialization. Model
servers may embed schemas in prompts, so alphabetic canonicalization is only
for artifact integrity, not model input. Hash the exact HTTP request bytes and
bind that hash into call identity; bind the request-format version into generic
and native generation recipes. The changed format creates new runs and never
relabels or overwrites earlier results.

Progress is an operator display on stderr; stdout remains reserved for the one
final JSON result. `--progress auto` is the default and displays progress only on
an interactive stderr, `always` forces it, and `never` disables it. Updates name
the current phase and safe run path, and report completed/total candidates,
accepted, quarantined, reused outcomes, request-cache entries present when
generation starts, current-candidate elapsed time and periodic heartbeats. It
does not estimate completion time. The progress mode does not participate in run,
configuration, request or artifact identity.

The first Ctrl+C requests a graceful pause. During generation, finish and persist
the current candidate outcome before starting no further candidate; during
preparation, stop at the next safe phase boundary. Release the run lock and exit
with the existing `INTERRUPTED` error and code 130, without a completed or paused
success result. A second Ctrl+C may stop immediately: the in-flight call can be
retried on resume, and closing the client cannot guarantee cancellation of server
work. Resume requires rerunning the exact same recipe, effective environment and
workspace; no `--resume` flag is added. Existing outcomes and request-cache
formats remain unchanged. The bounded local pause/resume verification is recorded
in [`plans/reviews/curation-progress-pause.md`](../plans/reviews/curation-progress-pause.md).

## Separate rejection-only repair pass

Native decision curation additionally accepts `--continue-from RUN`, mutually
exclusive with `--repair-from`. It may snapshot an incomplete parent under its
run lock. Preserve all completed accepted and quarantined outcomes, full retained
call records, and original generated IDs/provenance. Compare logical job plans
without their recipe-derived IDs; carried jobs retain their old IDs, missing jobs
use current IDs. Require identical effective configuration, source snapshots,
seed records, family assignments and observed model. Revalidate every carried
outcome before copying or inference. Bind the parent snapshot and current recipe
into the child identity and persist `continuation.json`; never mutate the parent.
Allow `--prepare-only` to initialize and verify without inference. Repeating the
same command resumes the child; advancing the parent creates a different snapshot
and child. Generic non-native curation rejects this option explicitly. This
operation does not retry quarantined jobs; those remain for a later repair pass.

`curate --repair-from <run>` creates a deterministic immutable child of a
completed curation run. Both wrapper scripts forward this option. A native run
that completed all jobs but failed coverage is eligible; an unfinished run is
not. `--prepare-only` and repair cannot be combined. The parent must be a direct,
non-symlink run in the selected workspace.

Require the same effective configuration, generation recipe, observed model,
source snapshots and frozen logical job plan. Verify private envelopes before
inference. Carry accepted outcome content and its available call cache unchanged;
remap only quarantined jobs into fresh job/seed/request identities. Provide only
phase-matching prior final content and safe rejection feedback, bounded for the
prompt. Repeat all ordinary checks, duplicate detection and coverage gates.
Preserve full final content in the bounded private call cache; outcome previews
may be truncated and must say so. Diagnostic outcomes are not training rows.

Persist the parent link and recipe in `repair.json`. Rerunning the same repair
command resumes its child; selecting that child starts a later pass. Parent
outcomes and published artifacts are never overwritten. Old responses that were
not retained cannot be reconstructed or described as original model responses.

## Local inference adapter

Use the OpenAI-compatible `/v1/models` and `/v1/chat/completions` interfaces via a
small typed adapter, not an inference-engine fork. Default baseUrl is
http://127.0.0.1:1234/v1. Numeric loopback addresses or localhost are allowed by
default; `allowPrivateNetwork: true` explicitly permits a trusted RFC1918 IPv4 or
IPv6 unique-local endpoint. Public addresses and DNS names remain rejected;
resolve localhost only to loopback and do not use ambient proxies or redirects.
No cloud fallback, tools, external URLs or remote model code in generation calls.

LocalEndpointConfig: baseUrl (default above), model (omitted means require exactly
one suitable discovered model), timeoutSeconds (1..600, default120), maxTokens
(1..32768, default2048), temperature (finite float0..2, default0.3),
maxResponseBytes (1024..16777216, default8388608). Strict fields and omitted/null
semantics follow existing contracts. Model ambiguity is an explicit actionable
failure, never arbitrary first-model selection. A configured model must match
discovery. Capture model/version metadata only where the server provides it.

Adapter functions: discover_models(config), and generate_json(config, model_id,
messages, schema, seed). Return typed parsed JSON plus safe response metadata.
JSON Schema constrains output, and local strict parsing validates it again.
Refusal, truncation, nonfinite/duplicate-key JSON, wrong schema, oversized body,
HTTP error or changed model are explicit failures. Runner owns bounded retries;
the adapter never silently retries or changes models. A live acceptance run must
test the exact endpoint/model before bulk generation.

## Validation, partitioning and publication

Before generation: normalize source data, remove/report exact duplicates, connect
related source families across corpora, reconcile original split constraints,
and freeze the partition plan. Conflicting split constraints never promote a
held-out family into training. All derivatives inherit the assigned partition.
Training generation sees training sources only; calibration and final-test source
contents are excluded from generation prompts and prompt/example retrieval.
Synthetic regression cases are a separately labeled population.

Validate generated schema, nonempty bounded text, allowed labels, known source
references, literal evidence existence, arithmetic where specified, duplicates
and declared scenario constraints. An independent answer/check pass must not be
fed the generator's proposed answer as the answer to endorse. Semantic checks
can reject uncertainty but cannot promote reviewed=false to human-reviewed.
Record both raw candidate and check outcome, including rejection reason. Each
candidate outcome retains a bounded trace for every completed attempt: the safe
phase, request and response digests, validator reason, and the final assistant
content when a valid assistant envelope supplied it. Never retain hidden
reasoning, credentials, HTTP error bodies or arbitrary exception text; an
attempt without final assistant content records that absence honestly.

Only a rejected candidate may receive the next bounded attempt. Its next request
may include the previous bounded final response and safe validator reason as
untrusted correction context, but never the hidden reference answer or oracle.
Every correction passes the complete schema, deterministic and independent
checker validation again. Earlier rejected attempts remain immutable and
auditable, accepted candidates are not regenerated, and published training
datasets continue to contain accepted records only.

Keep the existing canonical DataRecord, DatasetConfig and ArtifactManifest as the
published training boundary. Extend them only for explicit frozen assignments and
generation/source provenance where existing fields cannot preserve those facts.
Record generator model identity/limitations, prompt versions, parameters, source
record IDs, original splits, transformations and inherited rights. Include source
and teacher terms in declared derived-data restrictions. Generation never erases
source obligations. Exact mapping is frozen after the bounded contract review.

## Modules, dependencies and verification

New code lives under model/src/foliqant_model/curation. Reuse acquisition.download_asset,
strict config/JSON readers, private safe file operations, canonical digests and
prepare_dataset. Do not duplicate training/evaluation or add service/UI frameworks.
Use current locked dependencies; add a format dependency only when pinned assets
cannot be consumed safely with them. The source converters, local endpoint,
runner/validation and public contract integration have separate ownership.

Required evidence: actual pinned source acquisition/conversion; offline reuse;
all source splits/provenance retained; frozen assignments unchanged by augmentation;
no training access to heldouts; wrong schema/model, refusal, timeout and oversized
responses; deterministic scenario invariants; rejection/duplicate handling;
interruption/resume without reissuing completed calls; lock/corruption handling;
source-rights propagation; real LM Studio generation and downstream prepare/train
compatibility; docs/skill/schema/CLI alignment; independent code review and fixes.
Use test doubles only for controlled failure paths, never as real model evidence.

## User documentation and skills

Explain local server preparation, one-command acquisition/generation, automatic
quarantine, output counts, resume, quality limits and data/model lineage in clear
English. Provide a ready recipe requiring no manual record work. Update the
existing operating skill, schemas and configuration reference. Keep implementation
history and acceptance evidence in specs/plans, not user guides.

## Frozen-family extension (implementation contract)

DataRecord adds optional familyId and GenerationProvenance. DatasetConfig and
ResolvedDatasetConfig add optional frozenFamilies: a complete sorted mapping from
family ID to {split, sourceSplits}, where sourceSplits is sorted, unique, nonempty
original source/split identifiers. Presence selects frozen mode. Every record
declares a listed family; every listed family has records; every connected group
has one final split; all four partitions remain nonempty. Derivatives cannot add
families to a frozen plan. Fractions are planning inputs only in this mode.
Bind the complete map in datasetContentId only when present, preserving existing
fraction-mode artifacts. Retained-data verification recomputes these invariants.

Family identity participates in component union and domain-separated existing
groupKeyHashes/groupIds, so cross-artifact leakage checks require no new index
shape. GenerationProvenance is optional for imported teacher material where the
original generator may be unknown. When present it requires teacher/synthetic
origin, reviewed=false, sorted unique existing same-family parentRecordIds, no
self-parent and no dependency cycles. Fields: provider=openai-compatible, modelId,
modelIdentitySha256 (server metadata, NOT weight digest), promptSha256,
parametersSha256, requestSha256, parentRecordIds, optional modelWeightsSha256 only
when known from actual weight verification. Missing fields are not invented.

Automatic publication does not mark upstream labels human-reviewed without
evidence. The initial source corpus and augmented corpus may therefore remain
diagnostic. A separate synthetic scenario regression artifact is explicitly
diagnostic and never reported as representative real-world validation. No manual
review step blocks the requested automated research pipeline.

## Task-aware augmentation eligibility

Import all five declared sources, but exclude soft teacher probability targets
(typed-decisions) and token-aligned slot targets (MultiDoGO) from generic
paraphrase generation. Their original records remain in the corpus. Source
adapters emit augmentation-ineligible tags; runner coverage reports count these
training-parent exclusions. Generating new annotations for these tasks requires
a separate task-aware validator, not silent reuse of old token labels or exact
teacher probabilities. Source prompts declare the JSON contract used by their
reference labels. Checker task instructions preserve that contract and distinguish
an intended abstention/contradiction/manual-review answer from malformed input.

Cross-family exact duplicate source payloads are omitted entirely, with counts,
rather than using generic repeated turns to bridge unrelated conversation splits.
Same-family duplicates may collapse. A neutral redaction marker must not expose
the supervised slot label. Conservative source-family rights survive any later
cross-source duplicate collapse.

## Scoped task input and prompt conditioning

Generation edits only the final user content, preserving earlier conversation
messages. For BANKING77 only `request` is editable; for WANLI only `claim`; for
TAT-QA only `question`. Code reconstructs the final user JSON with all other
fields unchanged. The model never rewrites label catalogs, evidence, tables,
paragraphs, system instructions or conversation envelopes. Plain authored
scenarios retain their user text shape and exact quotes. Reject added role and
generation envelopes and copied rule-map instructions before the checker call.
Numeric/date checks compare the editable source text to the candidate, excluding
immutable task metadata and source evidence. Checks must not be relaxed to
inflate acceptance. The checker receives the reconstructed complete task and
context without the reference answer or the scenario operation label.

Use a generic `task` identifier in the generation request envelope, derived from
the input source contract, never from the answer or a target-bearing scenario
tag. This is conditioning for the generator, not a new tokenizer token or an
injected training-user prefix. Existing training system instructions define the
task for consumers. A shared inference/training prefix is a future format change
requiring matched evaluation, not an assumed quality improvement. Prompt revisions
create fresh run identities. Earlier artifacts stay immutable; discovered quality
defects are documented in execution evidence and exclude those pilot artifacts
from training recommendations.

The requested prefix investigation is a separate, inference-only validation
experiment, not a change to generated training records. The research script
`scripts/evaluate_task_prefixes.py` freezes three prompt conditions, 24 original
validation families and 72 serial requests before execution. It uses the existing
trusted local endpoint adapter, never downloads or trains, preserves source
artifacts, and stores requests/responses outside Git. Calibration and test records
are not selected. Full exact correctness remains primary; TAT-QA answer-only
diagnostics must not silently replace its annotation contract. A prefix requires
paired evidence and a disjoint validation confirmation before changing defaults.
Protocol and measured limitations belong in `plans/reviews/`.

## Explicit structured-output transport mode

The local endpoint also accepts optional `reasoningEffort` with exactly `low`,
`medium`, or `xhigh`. Omission leaves the provider setting unspecified; explicit
null and unsupported values fail configuration validation. When present it is
sent as top-level `reasoning_effort` in both structured-output modes and is part
of effective configuration, request, provenance and cache identities. The
wrapper accepts `FOLIQANT_CURATION_REASONING_EFFORT`; it does not silently retry
without the setting, disable reasoning, or switch models on rejection.
The native full/pilot recipes select `low` and temperature `0.1`, with the existing
8,192-token limit and serial requests, following the standalone Splash comparison.
Provider-neutral endpoint defaults remain unspecified for reasoning so other
model servers are not assumed to support this capability.

LocalEndpointConfig.structuredOutput is json-schema (default) or prompt. The
json-schema mode sends the standard response_format schema request. Prompt mode
omits that wire parameter and adds the schema to the model's instructions for
runtimes whose guided grammar interferes with reasoning/final separation. Both
modes strictly parse final message.content and validate the same schema locally;
neither accepts reasoning_content as the final answer. There is no automatic
mode or model fallback. The mode and effective request body are bound into
configuration, request cache and provenance identities. Malformed, truncated or
schema-invalid final responses remain failures in both modes.

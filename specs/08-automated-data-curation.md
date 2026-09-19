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
[--offline]`. Default workspace is the existing external Foliqant data root.
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
Record both raw candidate and check outcome, including rejection reason.

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

## Explicit structured-output transport mode

LocalEndpointConfig.structuredOutput is json-schema (default) or prompt. The
json-schema mode sends the standard response_format schema request. Prompt mode
omits that wire parameter and adds the schema to the model's instructions for
runtimes whose guided grammar interferes with reasoning/final separation. Both
modes strictly parse final message.content and validate the same schema locally;
neither accepts reasoning_content as the final answer. There is no automatic
mode or model fallback. The mode and effective request body are bound into
configuration, request cache and provenance identities. Malformed, truncated or
schema-invalid final responses remain failures in both modes.

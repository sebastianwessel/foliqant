# Model contract definitions

Contract version: `1`. The canonical Python classes live in
`model/src/foliqant_model/contracts/__init__.py`; `schema --output` generates their JSON
Schemas outside the repository or, for reviewed copies, under `model/schemas/`.
The generated schemas, not parallel handwritten DTOs, are the machine-readable
model-tooling boundary. Shared native-decision schemas are owned by the runtime
package and generated under `schemas/foliqant/decisions/`.

All objects are closed. Every field is required unless this document says
**optional** or gives a default. Optional fields are omitted; JSON `null` is used
only where this document defines a nullable value. Parsers do not coerce scalar
types. Integers exclude booleans. Numbers must be finite. Strings are UTF-8.

## Scalar rules and canonical form

- `Id`: `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
- `Digest`: exactly 64 lowercase hexadecimal SHA-256 characters.
- `Commit`: exactly 40 lowercase hexadecimal characters.
- `Timestamp`: RFC 3339 UTC with a terminal `Z`.
- `LocalPath`: nonempty filesystem path supplied by the operator. It is never
  passed through a shell or interpolated from environment variables.
- `SafePath`: nonempty POSIX relative path whose segments are neither empty,
  `.` nor `..`; it has no leading slash, backslash, NUL, drive prefix, or URI
  scheme.
- `JsonPointer`: RFC 6901 pointer; the empty string is allowed and identifies the
  whole document.
- `JsonValue`: JSON null, boolean, finite number, string, array of `JsonValue`, or
  object from string keys to `JsonValue`. This intentionally open recursive value
  is allowed only in `Prediction.expectedJson` and `Prediction.generatedJson`.
- Canonical JSON uses sorted object keys, UTF-8 without ASCII escaping, compact
  separators, no insignificant whitespace, and no nonfinite number. A digest of
  a contract object means SHA-256 of these bytes.
- A typed map is a JSON object whose property names and values have the declared
  types. Typed-map keys are serialized in lexical order. No typed map permits an
  undeclared value shape.

Arrays described as `sorted unique` reject duplicate elements and noncanonical
ordering. Unless a different key is stated, records are sorted by the first ID or
path field shown.

## Input contracts

Defaults are applied only after the entire input has passed syntax, duplicate-key,
unknown-field, size, type, and version validation. The normalized resolved object,
with every default materialized, is what is hashed and persisted.

### Data input

- `ChatMessage`: `role` (`system|user|assistant`) and `content` (nonempty string,
  at most 1 MiB in UTF-8). This text is data, never executable configuration.
- `DataRecord`: `schemaVersion` (literal `1`), `id` (`Id`), `sourceId` (`Id`),
  `language` (nonempty BCP47-like tag matching
  `[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*`), `groupKeys` (1..256 unique nonempty
  strings), `messages` (2..256 `ChatMessage` values), `tags` (unique strings,
  default `[]`), `origin` (`human|synthetic|teacher`), and `reviewed` (boolean).
  Conversation ordering and role constraints are those in `01-model-lifecycle.md`.
- `SourceDeclaration`: `id` (`Id`), `path` (`LocalPath`), `license` (nonempty
  string), `licenseEvidence` (nonempty string), `trainingAllowed` (literal
  `true`), `sharedTrainingAllowed` (boolean, default `false`),
  `redistributionAllowed` (boolean), `privacy` (`public|private`),
  `authorizationRef` (nonempty string, required exactly when `privacy=private`),
  `attribution` (string, default empty), `commercialUse`
  (`allowed|restricted|unknown`, default `unknown`), and `restrictions`
  (unique nonempty strings, default `[]`).
- `DatasetConfig`: `schemaVersion` (literal `1`), `name` (`Id`), `sources`
  (1..1024 declarations with unique `id`), `seed` (uint32, default `42`),
  `validationFraction`, `calibrationFraction`, `testFraction` (each finite,
  exclusive `0..1`, default `0.1`; sum strictly below `1`), `maxRecords`
  (integer `4..1000000`, default `100000`), and `maxRecordBytes` (integer
  `1..16777216`, default `1048576`).

`train` requires `sharedTrainingAllowed=true` on every source represented in its
dataset. `customize` does not; both commands still require `trainingAllowed=true`.
Private sources always require `authorizationRef`.

`trainingAllowed` attests permission for the actual current training use; it
does not attest permission for a future commercial use. Research/non-commercial
sources are permitted when their terms allow the intended activity.
`commercialUse` records the operator assessment: `allowed` means the cited
evidence permits the intended commercial use, subject to recorded obligations;
`restricted` means additional permission or constraints apply; `unknown` means
not established. Neither `unknown` nor `restricted` blocks lawful private
research. Never infer commercial permission from public availability, a private
repository, or `redistributionAllowed`. Preserve these fields unchanged in
resolved sources, dataset identities and all descendant source-rights unions.
`restrictions` records attribution, non-commercial/research-only limits,
redistribution conditions and other relevant obligations. The tool records
attestations, not legal certification. There is no commercial publishing command.

### Training input

`TrainConfig` contains `schemaVersion` (literal `1`), `name` (`Id`), and:

| Field | Type and default |
|---|---|
| `seed` | uint32, `42` |
| `steps` | integer `1..1000000`, `100` |
| `batchSize` | integer `1..64`, `1` |
| `gradientAccumulation` | integer `1..1024`, `1` |
| `maxSequenceLength` | integer `64..131072`, `2048` |
| `learningRate` | finite number greater than `0` and at most `1`, `0.0001` |
| `numLayers` | literal `-1` or integer `1..1024`, `-1` |
| `rank` | integer `1..256`, `8` |
| `scale` | finite number greater than `0` and at most `256`, `20` |
| `dropout` | finite number, inclusive `0` and exclusive `1`, `0` |
| `gradientCheckpointing` | boolean, `true` |
| `validationEvery` | integer `1..1000000`, `25` |
| `validationBatches` | integer `1..1000000`, `10` |
| `saveEvery` | integer `1..1000000`, `25` |
| `timeoutSeconds` | integer `1..604800`, `3600` |

Prompt masking is invariant and is not a user switch in version 1.

### Evaluation input

`EvaluationConfig` contains `schemaVersion` (literal `1`), `seed` (uint32,
default `42`), `maxTokens` (integer `1..32768`, default `256`), `maxExamples`
(optional integer at least `1`), `outputSchema` (optional `LocalPath` to a local
UTF-8 JSON Draft 2020-12 schema document), `evidencePointer` (optional `JsonPointer`),
`fieldPointers` (unique `JsonPointer` list, default `[]`), and `timeoutSeconds`
(integer `1..604800`, default `3600`). Before model load, the schema must pass
`Draft202012Validator.check_schema`. Every `$ref` and `$dynamicRef` must be a
same-document fragment beginning with `#`; local-file, network, and every other
retrieval target are prohibited. JSON Schema `format` remains annotation-only.

## Reusable durable records

- `FileEntry`: `path` (`SafePath`), `size` (nonnegative integer), `sha256`
  (`Digest`). Lists are sorted by `path` and paths are unique.
- `InventoryFileRef`: `path` (`SafePath`), `sha256` (`Digest`), `recordCount`
  (nonnegative integer), and `format` (`json|jsonl`). It must identify the same
  path and digest in the enclosing manifest's `files`.
- `ConfigIdentity`: `kind` (`dataset|train|evaluation`), `sha256` (`Digest`).
- `CountRate`: `eligibleCount` and `passedCount` (nonnegative integers,
  `passedCount <= eligibleCount`), and `rate` (finite `0..1` or null). `rate` is
  null exactly when `eligibleCount=0`; otherwise it equals
  `passedCount / eligibleCount` computed as IEEE 754 binary64, round-to-nearest
  ties-to-even, and serialized with the shortest round-trip decimal.
- `VersionedComponent`: `name` (nonempty string), `version` (nonempty string),
  and `role` (`backend|converter|inference-runtime|library`). Lists are sorted
  by `(role,name)` and that pair is unique.
- `ProducerIdentity`: `name` (literal `foliqant-model`), `version` (nonempty
  installed package version), `command` (one of the thirteen commands),
  `pythonVersion`, `platform`, and `machine` (nonempty strings), `codeRevision`
  (`Commit` or null), `codeDirty` (boolean or null),
  `codeIdentityUnavailableReason` (optional nonempty string), and `components`
  (sorted unique `VersionedComponent` list). `codeRevision` and `codeDirty` are
  both null exactly when code identity cannot be obtained, and the reason is
  then required; otherwise the reason is omitted.
- `SourceRight`: `sourceId` (`Id`), `license`, `licenseEvidence` (nonempty
  strings), `trainingAllowed` (literal `true`), `sharedTrainingAllowed`,
  `redistributionAllowed` (booleans), `privacy` (`public|private`),
  `authorizationRef` (nonempty string required exactly for private), and
  `attribution` (string), `commercialUse` (`allowed|restricted|unknown`),
  and `restrictions` (unique nonempty strings).
  It deliberately omits the original source path. Lists are sorted by
  `sourceId`; repeated IDs must have byte-identical canonical records or artifact
  finalization fails.
- `PrecisionChange`: `operation` (`quantize|dequantize-for-fusion|fuse|convert`),
  `fromPrecision`, `toPrecision` (nonempty strings), and `lossy` (boolean).
- `CompatibilityEvidence`: `runtime` and `runtimeVersion` (nonempty strings),
  `checkedAt` (`Timestamp`), `inputArtifactId` (`Digest`), `commandSha256`
  (`Digest`), and `resultSha256` (`Digest`).
- `CompatibilityRecord`: `target` (nonempty string), `status`
  (`unverified|verified|failed|unsupported`), and `evidence`
  (`CompatibilityEvidence` or null). Evidence is null exactly for `unverified`;
  `verified`, `failed`, and `unsupported` require it. Lists are sorted by target.

### Privacy-safe leakage records

`LeakageEntry` is one JSONL record with `recordId` (`Id`), `componentId`
(`Digest`), `groupKeyHashes` (sorted unique `Digest` list), `conversationHash`
(`Digest`), and `promptHash` (`Digest`). Hashes are:

- each `groupKeyHashes` value: SHA-256 of the original group-key UTF-8 bytes;
- `conversationHash`: SHA-256 of canonical JSON for all messages after NFC text
  normalization and LF newline normalization;
- `promptHash`: the same operation after removing the final assistant message;
- `componentId`: SHA-256 of canonical JSON for the sorted record IDs in the
  transitive connected component.

`LeakageIndexRef` contains `file` (`InventoryFileRef` whose format is `jsonl`),
`splits` (sorted unique list from `train|validation|calibration|test`), and
`entryCount` (equal to `file.recordCount`). The JSONL entries are sorted by
`recordId`, contain no message text or unhashed group key, and have unique record
IDs. Dataset indexes contain all records. Adapter indexes conservatively contain
every train and validation row submitted to the backend, whether or not a partial
batch was consumed, unioned with model-parent exposure and the complete exposure
index of any warm-start adapter ancestor. Exact leakage means any
matching record ID, group-key hash, full-conversation hash, or prompt-only hash;
no semantic-near-duplicate or PII-detection claim is made.

`InheritedLeakageRef` contains `ancestorArtifactId` (`Digest`) and `file`
(`InventoryFileRef` with path exactly
`inherited-leakage/<ancestorArtifactId>.jsonl`). Artifacts with exposure data copy
the exact index bytes for every ancestor carrying a dataset, used-data, or
exposure index; the copied file digest must equal that ancestor's index digest.
The list is sorted by ancestor artifact ID and unique. These retained bytes make
standalone union verification possible without resolving an external parent.

## Artifact envelope and lineage

`ArtifactManifest` has `schemaVersion` (literal `1`), `artifactId` (`Digest`),
`kind`, `name` (`Id`), `createdAt` (`Timestamp`), `stage`, `customer`, `files`,
`parents`, `producer`, `sourceRights`, and `details`.

- `kind` is `dataset|checkpoint|quantized|adapter|merged|export|evaluation|policy|audit`.
- `stage` is `upstream|shared|customer|none`. `customer` (`Id`) is required
  exactly for `customer` stage and omitted otherwise.
- `files` is a sorted unique `FileEntry` list and excludes only
  `manifest.json`. Any other unlisted artifact entry is invalid.
- `parents` is an ordered list of `ParentRef`: semantic primary model/dataset or
  evaluation input first, then secondary inputs in CLI argument order.
- `sourceRights` is the transitive union of the artifact's data sources and all
  parent source rights under the collision rule above.
- `details` is exactly one kind-specific record below and its type must match
  `kind`; it is never a dictionary escape hatch.
- `artifactId` is the digest of canonical manifest JSON with only the top-level
  `artifactId` field omitted. It therefore binds metadata, parent snapshots,
  inventories, producer identity, and creation time.

`ParentRef` is a complete immutable snapshot with the same fields as
`ArtifactManifest` except `schemaVersion`; its own `artifactId` remains present.
Its nested `parents` are recursively complete. The snapshot must equal the
verified parent manifest after canonical parsing and removal of only its
`schemaVersion`. To validate the snapshot's `artifactId`, verification restores
`schemaVersion:1`, removes only `artifactId`, and hashes canonical JSON. A lineage tree
may have at most 32 parent edges on any root-to-leaf path and at most 256
`ParentRef` occurrences total; repeated ancestors count each time. Cycles,
truncation, summaries, mutable aliases, or a tree over either bound are rejected
before work starts. Parent files are inventory metadata only; parent file bytes
are not copied merely to satisfy lineage.

Kind/stage rules are fixed:

| Kind | Stage and direct parents |
|---|---|
| `dataset` | `none`; no parents |
| `checkpoint` | `upstream`; no parents |
| `quantized` | stage/customer exactly copied from one `checkpoint` or `merged` parent |
| `adapter` | `shared` or `customer`; model parent, dataset parent, then optional warm-start adapter |
| `merged` | stage/customer copied from adapter; model parent then adapter parent |
| `export` | stage/customer copied from one `merged` parent |
| `evaluation` | stage/customer copied from the effective model/adapter; model, optional adapter, then dataset |
| `policy` | stage/customer copied from one calibration evaluation parent |
| `audit` | stage/customer copied from test evaluation, then policy parent |

A shared adapter may use an upstream `checkpoint`/`quantized` model, a
`merged/shared` model, or a `quantized/shared` model whose sole direct model
parent is `merged/shared`; it may never have customer ancestry. A customer
adapter may use a `merged/shared` model or a `quantized/shared` model whose sole
direct model parent is `merged/shared`.
Its customer is the required CLI customer. A warm start must have the same
stage, customer, and exact model parent artifact ID.

## Kind-specific `details`

### Dataset

- `SourceFileSummary`: `sourceId` (`Id`), `sha256` (`Digest`), `size`
  (nonnegative integer), and `recordCount` (positive integer).
- `RecordAssignment`: `split` (`train|validation|calibration|test`),
  `componentId` (`Digest`), and `groupIds` (sorted unique nonempty `Digest` list).
  A group ID is the SHA-256 of `group\0`, `conversation\0`, or `prompt\0`
  followed by the corresponding original group key, normalized canonical full
  conversation bytes, or normalized canonical prompt-only bytes. It is used
  only for disjointness checks.
- `OriginCounts`: required nonnegative `human`, `synthetic`, and `teacher`.
- `PartitionCounts`: `records` and `components` (nonnegative integers),
  `languages` and `sources` (typed maps from nonempty string/`Id` to nonnegative
  integer), and `origins` (`OriginCounts`). Empty zero-valued map entries are
  omitted.
- `ResolvedSplitSettings`: `seed` (uint32) and the three finite fractions from
  normalized `DatasetConfig`.
- `DatasetDetails`: `datasetConfig` (fully resolved `DatasetConfig` with source
  paths replaced by their `sourceId`; represented by `ResolvedDatasetConfig` in
  the schema), `datasetConfigSha256` (`Digest`), `datasetContentId` (`Digest`),
  `normalizationVersion` (literal `1`), `splitSettings`
  (`ResolvedSplitSettings`), `sourceFiles` (sorted unique `SourceFileSummary`
  list), `assignments` (typed map from `Id` to `RecordAssignment`), `partitions`
  (typed map with exactly the four split keys and `PartitionCounts` values),
  `chatFiles` and `recordFiles` (typed maps with exactly the four split keys and
  `InventoryFileRef` values), `leakageIndex` (`LeakageIndexRef` for all four
  splits), and `diagnostic`
  (boolean, true exactly when any record has `origin=synthetic`,
  `origin=teacher`, or `reviewed=false`).

`ResolvedDatasetConfig` has every `DatasetConfig` field and default, but each
source is `ResolvedSourceDeclaration`, which has every `SourceDeclaration` field
except `path`. Dataset paths are exactly `chats/<split>.jsonl`,
`records/<split>.jsonl`, and `leakage.jsonl`. Every JSONL line is one canonical
JSON object followed by LF, ordered by record ID. A chat row has exactly the key
`messages`; a full-record row is the normalized `DataRecord`; leakage rows are
`LeakageEntry`.

`datasetContentId` is the digest of one canonical object with exactly these keys:
`normalizationVersion`, `splitSettings`, `sourceRights`, `assignments`,
`chatFileDigests`, and `recordFileDigests`. The two digest maps have exactly the
four split keys and digest the emitted canonical files; `sourceRights` is the
sorted path-free list. Raw source-file hashes, timestamps, and output paths are
excluded, so source row order does not change content identity. Raw source hashes
remain in `sourceFiles` and the artifact identity.

### Checkpoint and quantized model

- `ChatTemplateSource`: `kind`
  (`file|tokenizer-config|registered-default`) and `value` (nonempty string).
  `value` is respectively the `SafePath`, literal
  `tokenizer_config.json#/chat_template`, or registered default-template `Id`.
- `ModelIdentity`: `architecture` (nonempty string), `weightFormat`
  (`safetensors|gguf`), `configSha256`, `tokenizerSha256`, and
  `chatTemplateSha256` (`Digest` values), `tokenizerFiles` (sorted unique
  nonempty `SafePath` list), and `chatTemplateSource` (`ChatTemplateSource`).

`configSha256` hashes the raw `config.json` bytes. `tokenizerFiles` contains every
inventoried tokenizer, tokenizer-config, config-template, vocabulary, and merges
file, and excludes model `config.json`, weights/indexes, README, and license
files. `tokenizerSha256` hashes canonical JSON for the sorted `FileEntry` records
at exactly those paths. Template selection uses raw UTF-8 template text in this
order: `chat_template.jinja` when present; otherwise a nonempty string at
`tokenizer_config.json` key `chat_template`; otherwise the single named default
template registered for the architecture. Version one registers no fallback
templates: a missing or ambiguous explicit template fails rather than guessing.
`chatTemplateSha256` hashes those raw
UTF-8 bytes. Training preflight repeats selection and rejects a missing,
ambiguous, or hash-mismatched template.
- `CheckpointDetails`: `model` (`ModelIdentity` with `weightFormat=safetensors`),
  `upstreamRepo` (matching `OWNER/NAME`), `upstreamRevision` (`Commit`),
  `licenseRef` (nonempty string), `weightPrecision` (nonempty string), and
  `compatibility` (sorted unique `CompatibilityRecord` list).
- `QuantizedDetails`: `model` (`ModelIdentity` with
  `weightFormat=safetensors`), `bits` (`4|8`), `groupSize` (literal `64`),
  `parentArtifactId` (`Digest` equal to the sole parent), `precisionHistory`
  (nonempty `PrecisionChange` list ending in `operation=quantize`), and
  `exposureLeakageIndex` (`LeakageIndexRef`), and `compatibility` (sorted unique
  `CompatibilityRecord` list), plus `inheritedLeakageIndexes` (sorted unique
  `InheritedLeakageRef` list). The exposure index is a copied union of parent
  exposure and is an empty inventoried `leakage.jsonl` with `splits=[]` for an
  upstream checkpoint parent.

### Adapter and merge

- `BackendTrainConfig`: `method` (`lora|qlora`), `maskPrompt` (literal `true`),
  `seed`, `steps`, `batchSize`, `gradientAccumulation`, `maxSequenceLength`,
  `learningRate`, `numLayers`, `rank`, `scale`, `dropout`,
  `gradientCheckpointing`, `validationEvery`, `validationBatches`, and
  `saveEvery` with the normalized `TrainConfig` types, plus `modelArtifactId`,
  `datasetArtifactId`, `trainFileSha256`, and `validationFileSha256` (`Digest`).
  It excludes name, timeout, and every temporary absolute path.
- `MeasuredBytes`: `value` (nonnegative integer or null) and `unavailableReason`
  (optional nonempty string). The reason is required exactly when value is null.
- `AdapterValidationObservation`: `iteration` (nonnegative integer), `loss`
  (finite nonnegative number), and `elapsedSeconds` (finite nonnegative number).
- `AdapterDetails`: `modelArtifactId`, `datasetArtifactId` (`Digest`),
  `trainConfig` (fully resolved `TrainConfig`), `trainConfigSha256` (`Digest`),
  `backendConfig` (`BackendTrainConfig`), `warmStartArtifactId` (optional
  `Digest`), `usedDataLeakageIndex` (`LeakageIndexRef` whose splits are exactly
  `train,validation` and whose path is `leakage.jsonl`),
  `inheritedLeakageIndexes` (sorted unique `InheritedLeakageRef` list),
  `finalLoss` (finite nonnegative number),
  `validation` (`AdapterValidationObservation` list ordered by unique ascending
  `iteration`; the list may be empty when the backend emitted no validation
  callback),
  `peakMemoryBytes` (`MeasuredBytes`), `elapsedSeconds` (finite nonnegative
  number), `trainableTensorNames` (nonempty sorted unique list of nonempty
  strings), and `adapterFormat` (literal `mlx-lora-v1`). `method=qlora` exactly
  when the model parent is quantized; otherwise it is `lora`. The parent copies
  `finalLoss`, `validation`, `elapsedSeconds`, and `trainableTensorNames` from
  the accepted `TrainWorkerResult`, and sets `peakMemoryBytes.value` to its
  `peakMemoryBytes` integer with `unavailableReason` omitted. These fields are
  persisted observations, not rederived values. Warm-start compatibility
  compares `trainableTensorNames` exactly.

`usedDataLeakageIndex` is the sorted unique union of the model parent's persisted
exposure index (empty for an upstream checkpoint), the warm-start adapter's
complete used-data index when present, and every current train/validation row
submitted to the backend.
For a dataset ancestor, union construction selects train/validation rows using
that ancestor snapshot's `assignments`; for an adapter or model ancestor it uses
the whole retained index. Repeated record IDs must have byte-identical canonical
`LeakageEntry` values or finalization fails with `LINEAGE_MISMATCH`; unioning never
uses first-wins or last-wins behavior.

The adapter manifest's `producer.components` must contain
`name=foliqant-completion-loss`, `version=1`, `role=library`. This identifies the
custom loss whose supervised mask is
`promptOffset <= targetPosition < sequenceLength`; padded positions are excluded.
- `MergedDetails`: `model` (`ModelIdentity` with `weightFormat=safetensors`),
  `modelParentArtifactId`, `adapterArtifactId` (`Digest` matching its first two
  parents), `precisionHistory` (`PrecisionChange` list),
  `exposureLeakageIndex` (`LeakageIndexRef` containing the sorted unique union of
  model and adapter exposure, stored at `leakage.jsonl`),
  `inheritedLeakageIndexes` (sorted unique `InheritedLeakageRef` list), and
  `compatibility` (sorted unique
  `CompatibilityRecord` list). A quantized model parent requires
  a `dequantize-for-fusion` entry with `lossy=true` before `fuse`.

### Evaluation and predictions

- `GenerationSettings`: `temperature` (literal `0`), `seed` (uint32), and
  `maxTokens` (integer `1..32768`).
- `ValidatorSettings`: `outputSchemaSha256` (`Digest` or null),
  `evidencePointer` (`JsonPointer` or null), `fieldPointers` (sorted unique
  `JsonPointer` list), and `jsonRequired` (boolean, true exactly when a schema,
  evidence pointer, or field pointer is configured).
- `ScoringIdentity`: `jsonParserVersion` (literal `rfc8259-v1`),
  `exactVersion` (literal `canonical-json-or-stripped-text-v1`),
  `evidenceVersion` (literal `exact-input-substring-v1`), and
  `fieldVersion` (literal `json-pointer-equality-v1`).
- `DeploymentProfile`: `modelArtifactId` (`Digest`), `adapterArtifactId`
  (`Digest` or null), the three hashes from `ModelIdentity`, `generation`
  (`GenerationSettings`), `validators` (`ValidatorSettings`), `scoring`
  (`ScoringIdentity`), `pythonVersion`, `platform`, `machine` (nonempty strings),
  and `runtime` (sorted unique `VersionedComponent` list).
  It excludes dataset, split, selected IDs, `maxExamples`, timeout, and output
  paths. `deploymentProfileId` is its canonical digest.
- `ComponentRepresentative`: `componentId` (`Digest`), `recordId` (`Id`), and
  `groupIds` (sorted unique nonempty `Digest` list). It is sorted by component ID.
- `EvaluationAggregate`: `total` (nonnegative integer), `exact`, `json`,
  `schema`, `evidence`, and `applicableValidation` (`CountRate`), plus
  `fields` (typed map from configured `JsonPointer` to `CountRate`). `exact` and
  `json` have `eligibleCount=total`; schema/evidence/field counts have
  `eligibleCount=total` exactly when configured and zero otherwise.
- `Prediction`: `id`, `sourceId` (`Id`), `language` (language tag), `tags`
  (sorted unique strings), `componentId` (`Digest`), `groupIds` (sorted unique
  `Digest` list), `representative` (boolean), `expected`, `generated` (strings),
  `expectedJson`, `generatedJson` (`JsonValue` or null), `elapsedSeconds`
  (finite nonnegative number), `generatedTokens` (nonnegative integer),
  `meanTokenLogprob` (finite number at most zero or null), `jsonValid` (boolean),
  `meanTokenLogprobUnavailableReason` (optional literal `no-generated-tokens`),
  `finishReason` (`stop|length`),
  `schemaValid`, `evidenceValid` (boolean or null), `fieldPresent` and
  `fieldValid` (typed maps from every configured `JsonPointer` to boolean),
  `applicableValid` (boolean), and `exactCorrect` (boolean).
- `EvaluationDetails`: `modelArtifactId`, `adapterArtifactId` (`Digest` or null),
  `datasetArtifactId` (`Digest`), `split` (`validation|calibration|test`),
  `evaluationConfigSha256` (`Digest`), `deploymentProfile`
  (`DeploymentProfile`), `deploymentProfileId` (`Digest`), `selectedRecordIds`
  (sorted unique `Id` list), `selectedGroupIds` (sorted unique `Digest` list),
  `representatives` (sorted unique `ComponentRepresentative` list),
  `selectionUnit` (literal `component-representative-v1`), `predictions`
  (`InventoryFileRef` for JSONL), `outputSchemaFile` (`InventoryFileRef` with
  format `json` or null), `aggregate` (`EvaluationAggregate`),
  `languages` and `sources` (typed maps to `EvaluationAggregate`), and
  `diagnostic` (copied from dataset).

Before generation, the evaluator computes the lexicographically smallest record
ID for every component in the full requested split. It then selects all record
IDs when `maxExamples` is omitted, or the lexicographically smallest record-ID
prefix of length `min(maxExamples, available records)`. `representatives`
contains the precomputed minimum
for each component represented by at least one selected row. That minimum is
necessarily in the selected prefix. Every selected row has exactly one prediction
and every represented component has exactly one representative prediction.
When an adapter is supplied, its recorded model parent must be the exact
`modelArtifactId` supplied to evaluation.
Before loading weights for `calibration` or `test`, evaluation compares every
selected row's `LeakageEntry` against the persisted exposure index on the
effective model and, when present, the adapter. Any exact ID, group-key,
full-conversation, or prompt-only match is `LEAKAGE_DETECTED`. Validation-split
evaluation is permitted to reuse training validation rows and does not apply
this rejection. The check never depends on reading bytes through a parent
snapshot; quantized, merged, export, and adapter artifacts carry the required
inventoried index themselves.

`expectedJson`/`generatedJson` contain the parsed value or null when parsing
fails. A valid JSON `null` also has a null parsed value; `jsonValid=true`
distinguishes it from a parse failure. `meanTokenLogprob` is null exactly when `generatedTokens=0`; the unavailable
reason is then required and otherwise omitted. `finishReason=stop` excludes the
stopping EOS from token count/scoring; `length` includes the final generated token.
`jsonValid` describes `generatedJson` even when no JSON-dependent validator is
configured. `schemaValid` and `evidenceValid` are null exactly when their validator
is not configured. A configured evidence pointer must resolve in the JSON to
either a nonempty string or a nonempty list of nonempty strings, and every string
must be an exact substring of the supplied input messages; otherwise evidence is
invalid. Configured field pointers must resolve in the reference before
generation; failure is an input contract error. For a prediction, `fieldPresent`
records pointer resolution and `fieldValid` records strict equality with the
reference value; a missing prediction pointer makes both false. Before generation,
the reference must parse and pass every configured JSON, schema, evidence, and
field-pointer requirement. `applicableValid` is true exactly when required JSON
parses, configured schema and evidence validation pass, and every configured
prediction field is present. `fieldValid` equality never affects eligibility.
No configured validators means true. Base equality is recursive structural equality: object key
sets must match exactly, arrays are ordered, strings compare exactly, numbers use
numeric equality (`1` equals `1.0`), and booleans are distinct from numbers
(`true` does not equal `1`). If neither value parses as JSON, base equality is
stripped exact text equality; if only one parses, equality is false.
`exactCorrect` is base equality AND `applicableValid`; invalid JSON alone does not
make a plaintext prediction incorrect when no JSON-dependent validator is
configured. Length-truncated output is never risk-eligible even if it parses and
matches.

When configured, `outputSchemaFile` is the exact validated input schema copied
byte-for-byte into the evaluation artifact and
`ValidatorSettings.outputSchemaSha256` is the SHA-256 of those bytes. When no
schema is configured, the file reference and digest are null. Validation never
retrieves another document and never treats `format` as an assertion.

### Policy and audit

- `RiskSampleSummary`: `representativeCount`, `eligibleCount`, `acceptedCount`,
  and `errors` (nonnegative integers with
  `errors <= acceptedCount <= eligibleCount <= representativeCount`). A
  representative from a selected component is eligible exactly when
  `applicableValid=true`,
  `finishReason=stop`, and its `meanTokenLogprob` is finite.
- `PolicyDetails`: `evaluationArtifactId`, `deploymentProfileId`,
  `datasetArtifactId` (`Digest`), `selectionUnit` (literal
  `component-representative-v1`), `representativeRecordIds` (sorted unique
  nonempty `Id` list), `representativeGroupIds` (sorted unique `Digest` list),
  `threshold` (finite number at most zero or null), `maxError` (finite number,
  exclusive `0..1`), `minAccepted` (positive integer), `selection`
  (`RiskSampleSummary`), `reason` (optional nonempty string), and `scoreMethod`
  (literal `mean-generated-token-logprob-v1`). Threshold is null exactly for an
  abstain-all policy; then accepted/errors are zero and reason is required.
  Otherwise reason is omitted.
- `AuditDetails`: `evaluationArtifactId`, `policyArtifactId`,
  `deploymentProfileId`, `datasetArtifactId` (`Digest`), `selectionUnit`
  (literal `component-representative-v1`), `representativeRecordIds` (sorted
  unique nonempty `Id` list), `representativeGroupIds` (sorted unique `Digest`
  list), `counts` (`RiskSampleSummary`), `total` (evaluation row count),
  `coverage` (finite `0..1`; acceptedCount/representativeCount),
  `upperErrorBound` (finite `0..1` or null), `status`
  (`insufficient|meets-bound|fails-bound`), `diagnostic` (boolean), and
  `assumptions` (sorted unique nonempty strings).

Calibration and audit use representatives only. A missing representative,
non-disjoint calibration/test record or group ID, dataset mismatch, or profile
mismatch is a contract failure. Every representative must also have a finite
score; a null score fails with `RISK_SCORE_MISSING` rather than silently reducing
the denominator. `upperErrorBound` is null exactly when no sample is accepted.
Status and the exact-binomial calculation follow
`01-model-lifecycle.md`.

### Export

- `CheckpointExportMetadata`: `format` (literal `checkpoint`),
  `safetensorsFileCount` and `tensorCount` (positive integers).
- `GgufExportMetadata`: `format` (literal `gguf`), `ggufVersion`
  (positive integer), `tensorCount` and `metadataKeyCount` (positive integers),
  and `quantizationType` (nonempty string).
- `ExportDetails`: `model` (`ModelIdentity`), `mergedArtifactId` (`Digest` equal
  to the sole parent), `exportMetadata` (the discriminated union above),
  `precisionHistory` (`PrecisionChange` list), `exposureLeakageIndex`
  (`LeakageIndexRef` copied from the merged parent at `leakage.jsonl`),
  `inheritedLeakageIndexes` (sorted unique `InheritedLeakageRef` list), and
  `compatibility` (sorted
  unique `CompatibilityRecord` list whose statuses are all `unverified` and
  evidence values are null).

Export manifests always record compatibility as `unverified`. A real independent
runtime smoke result is external immutable release evidence bound to the export
`artifactId`; it does not mutate or promote the artifact. `verify` never changes
compatibility state. Checkpoint exports contain one unverified compatibility
record with `target="transformers"`; GGUF exports contain one with
`target="llama.cpp"`.

The repository smoke scripts persist a closed `RuntimeSmokeReport` outside the
artifact. It has `schemaVersion` (literal `1`), `evidenceId` (`Digest`), `target`
(`transformers|llama.cpp`), `evidence` (`CompatibilityEvidence` whose runtime
and input artifact match the target and verified export), `executableSha256`
(`Digest`), and `result` selected by `target`. `evidenceId` is SHA-256 of the
canonical JSON report with `evidenceId` omitted. `commandSha256` and
`resultSha256` are SHA-256 of canonical JSON for the script's fixed bounded
invocation record and persisted result respectively.

- `transformers` result: `generatedTokens` (integer `1..16`), `generatedText`
  (nonempty string), `elapsedSeconds` (finite number greater than or equal to
  zero), and nonempty `pythonVersion`, `torchVersion`, and
  `transformersVersion` strings.
- `llama.cpp` result: `returnCode` (literal `0`), `stdout` (nonempty string),
  `stderr` (string), `outputCharacters` (positive integer equal to the Unicode
  character count of `stdout`), and `elapsedSeconds` (finite number greater than
  or equal to zero).

This report is release-verification evidence, not an artifact manifest or a
compatibility promotion. It may contain generated text and remains private at a
caller-chosen path. The scripts refuse to overwrite that path.

## Offline worker contracts

The parent writes one private request JSON file and the child writes one private
result JSON file. Both are closed discriminated unions. The child is launched as
an argument array with `shell=false`; none of these paths is a shell fragment.

`MlxLoraParameters` has `rank` (integer `1..256`), `scale` (finite number greater
than `0` and at most `256`), and `dropout` (finite number, inclusive `0` and exclusive
`1`). `MlxOptimizerConfig` has exactly one key, `adamw`, whose value has exactly
`weight_decay` (literal `0`).

`MlxTrainNamespace` is the complete namespace passed to MLX LM and serialized as
the restricted `adapter_config.json`: `model`, `data`, `adapter_path`
(`LocalPath`), `resume_adapter_file` (`LocalPath` or null), `train` (literal
`true`), `test` (literal `false`), `fine_tune_type` (literal `lora`), `optimizer`
(literal `adamw`), `optimizer_config` (`MlxOptimizerConfig`), `seed` (uint32),
`num_layers` (literal `-1` or integer `1..1024`), `batch_size` (integer `1..64`),
`iters` (integer `1..1000000`), `val_batches` (integer `1..1000000`),
`learning_rate` (finite number greater than `0` and at most `1`), `steps_per_report` (literal
`1`), `steps_per_eval` and `save_every` (integer `1..1000000`), `test_batches`
(literal `500`), `max_seq_length` (integer `64..131072`), `config` (null),
`grad_checkpoint` (boolean), `grad_accumulation_steps` (integer `1..1024`),
`clear_cache_threshold` (literal `0`), `lr_schedule`, `report_to`, `project_name`
(all null), `lora_parameters` (`MlxLoraParameters`), and `mask_prompt` (literal
`true`). The file may retain private workspace paths, but no manifest or runtime
load relies on them. `BackendTrainConfig` is its path-free durable projection.

Every worker request has `schemaVersion` (literal `1`), `requestId` (`Id`), and
an `operation` discriminant:

| Request branch | Additional fields |
|---|---|
| `DoctorWorkerRequest` / `doctor` | no additional fields |
| `TrainWorkerRequest` / `train` | `namespace:MlxTrainNamespace`; `trainPath`, `validationPath`, `outputPath` (`LocalPath`) |
| `GenerateWorkerRequest` / `generate` | `modelPath:LocalPath`; `adapterPath:LocalPath|null`; `messages` (valid prompt-only `ChatMessage` list); `seed:uint32`; `maxTokens` integer `1..32768`; `temperature` literal `0` |
| `QuantizeWorkerRequest` / `quantize` | `modelPath`, `outputPath` (`LocalPath`); `bits:4|8`; `groupSize:64`; `mode` literal `affine` |
| `MergeWorkerRequest` / `merge` | `modelPath`, `adapterPath`, `outputPath` (`LocalPath`); `dequantize` (boolean, true exactly for a quantized model) |
| `GgufWorkerRequest` / `gguf` | `modelPath`, `outputPath` (`LocalPath`); `outputPrecision` literal `F16` |

- `ValidationObservation`: `iteration` (nonnegative integer), `loss` (finite
  nonnegative number), and `elapsedSeconds` (finite nonnegative number).
- `DoctorWorkerResult`: `mlxImportAvailable` and `metalAvailable` (booleans),
  `mlxVersion` and `mlxLmVersion` (nonempty strings or null), and
  `unavailableReason` (nonempty string or null). When import is unavailable,
  Metal is false, both versions are null, and a redacted reason is required.
  When import succeeds, both versions are present and the reason is null.
- `TrainWorkerResult`: `finalLoss` (finite nonnegative number),
  `validation` (`ValidationObservation` list ordered by iteration),
  `peakMemoryBytes` (nonnegative integer), `elapsedSeconds` (finite nonnegative),
  `adapterWeightsPath`, `adapterConfigPath` (`LocalPath`), and
  `trainableTensorNames` (sorted unique nonempty string list).
- `GenerateWorkerResult`: `generated` (string), `generatedTokens` (nonnegative
  integer), `meanTokenLogprob` (finite number at most zero or null),
  `meanTokenLogprobUnavailableReason` (optional literal
  `no-generated-tokens`), `finishReason` (`stop|length`), and `elapsedSeconds`
  (finite nonnegative). Null/reason and EOS counting rules are identical to
  `Prediction`.
- `QuantizeWorkerResult`: `outputPath` (`LocalPath`), `tensorCount` (positive
  integer), `fromPrecision`, `toPrecision` (nonempty strings).
- `MergeWorkerResult`: `outputPath` (`LocalPath`), `tensorCount` (positive
  integer), `dequantized` (boolean).
- `GgufWorkerResult`: `outputPath` (`LocalPath`) and `metadata`
  (`GgufExportMetadata`).

`WorkerSuccess` has `schemaVersion` (literal `1`), `requestId`, `operation`, `ok`
(literal `true`), and the matching result branch. `WorkerFailure` has those first
three fields, `ok` (literal `false`), and `error`. `WorkerError` has `code`
(`INVALID_REQUEST|SAFE_LOAD_FAILED|UNSUPPORTED_ARCHITECTURE|TOKENIZATION_FAILED|SEQUENCE_TOO_LONG|TRAINING_FAILED|GENERATION_FAILED|NONFINITE_METRIC|OUTPUT_INVALID|INTERRUPTED|INTERNAL`)
and a redacted nonempty `message`; it has no open details. The parent rejects a
mismatched request ID/operation, malformed result, nonzero child exit, or result
whose referenced output fails independent inspection. Worker success is never
sufficient by itself to finalize an artifact.

`doctor` performs its MLX import and Metal probe only through
`DoctorWorkerRequest`; an import exception is represented by a successful typed
`DoctorWorkerResult` with unavailable capability, not untyped subprocess output.
The parent maps `mlxImportAvailable` to CLI `mlxAvailable`, `metalAvailable` to
`mlxDeviceAvailable`, and the two versions to `components`.

## Run state

`RunState` is a closed mutable workspace record, not an artifact:
`schemaVersion` (literal `1`), `runId` (`Id`), `command`, `startedAt`, `updatedAt`
(`Timestamp`), `status` (`running|completed|failed|interrupted`), `pid` (positive
integer), `configuration` (`ConfigIdentity` or null), `parentArtifactIds`
(ordered unique `Digest` list), `datasetArtifactId` (`Digest` or null),
`failure` (`RunFailure` or null), `logPath` (`SafePath`), and
`finalizedArtifactId` (`Digest` or null).

`RunFailure` has `code` (`CliErrorCode`) and `message` (nonempty string). Running
state has null failure and final artifact. Completed state has a final artifact
and null failure. Failed/interrupted state has a failure and null final artifact.
`updatedAt >= startedAt`; transitions are only running to one terminal state.
Atomic artifact publication happens only after completed state is ready.

## CLI success and failure contracts

Except parser-generated `--help`, success writes exactly one compact
`CliSuccess` JSON object plus LF to stdout. Failure writes no stdout; its final
stderr line is exactly one compact `CliFailure` object plus LF. Earlier stderr
lines may contain human diagnostics but must not contain source messages,
predictions, secrets, or authorization references.

`CliSuccess` has `schemaVersion` (literal `1`), `ok` (literal `true`), `command`,
and `result`, whose discriminant must equal `command`. Shared
`CreatedArtifactResult` contains `outputPath` (`LocalPath`), `artifactId`
(`Digest`), `kind`, `stage`, and `customer` (`Id` or null). Command results are:

| Command | Exact `result` fields |
|---|---|
| `doctor` | `command:"doctor"`; `pythonVersion`, `platform`, `machine` nonempty; `mlxAvailable`, `mlxDeviceAvailable` booleans; `freeDiskBytes` nonnegative integer; `availableCommands`, `unavailableCommands` sorted unique command lists; `components` sorted `VersionedComponent` list |
| `schema` | `command:"schema"`; `mode:output|check`; `directory:LocalPath`; `schemaCount` nonnegative integer; `driftCount` nonnegative integer (`0` on success) |
| `fetch` | `command:"fetch"`; `artifact:CreatedArtifactResult` (`checkpoint/upstream`); `fileCount` positive integer |
| `prepare` | `command:"prepare"`; `artifact` (`dataset/none`); `datasetContentId:Digest`; `partitionRecords` typed map with exactly four nonnegative split counts; `diagnostic:boolean` |
| `quantize` | `command:"quantize"`; `artifact` (`quantized`); `bits:4|8`; `groupSize:64` |
| `train` | `command:"train"`; `artifact` (`adapter/shared`); `finalLoss` finite nonnegative; `elapsedSeconds` finite nonnegative |
| `customize` | `command:"customize"`; `artifact` (`adapter/customer`); `finalLoss` finite nonnegative; `elapsedSeconds` finite nonnegative |
| `evaluate` | `command:"evaluate"`; `artifact` (`evaluation`); `split`; `exampleCount`, `representativeCount` nonnegative; `exact:CountRate`; `predictionsPath:LocalPath` |
| `calibrate` | `command:"calibrate"`; `artifact` (`policy`); `threshold` finite <=0 or null; `selection:RiskSampleSummary`; `abstainAll:boolean` |
| `audit` | `command:"audit"`; `artifact` (`audit`); `status`; `counts:RiskSampleSummary`; `coverage` finite `0..1`; `upperErrorBound` finite `0..1` or null |
| `merge` | `command:"merge"`; `artifact` (`merged`); `modelArtifactId`, `adapterArtifactId` (`Digest`) |
| `export` | `command:"export"`; `artifact` (`export`); `format:checkpoint|gguf`; `compatibilityStatus` literal `unverified` |
| `verify` | `command:"verify"`; `path:LocalPath`; `artifactId:Digest`; `kind`; `stage`; `customer:Id|null`; `fileCount` nonnegative; `lineageNodeCount` unique ancestor count, nonnegative; `valid` literal `true` |

`CliFailure` has `schemaVersion` (literal `1`), `ok` (literal `false`), `command`
(command or null when command selection failed), `exitCode` (`2|3|4|5|130`), and
`error` (`CliError`). `CliError` has `code` (`CliErrorCode`), `category`, `message`
(nonempty string), `location` (`ErrorLocation` or null), and `artifactId`
(`Digest` or null). `ErrorLocation` has `kind`
(`argument|config-pointer|input-path|artifact-path|runtime`) and `value`
(nonempty string). Messages and locations are redacted.

`CliErrorCode`, category, and exit code are an exact mapping:

| Exit | Category | Codes |
|---|---|---|
| 2 | `input` | `ARGUMENT_INVALID`, `CONFIG_INVALID`, `CONFIG_TOO_LARGE`, `CONFIG_DUPLICATE_KEY`, `CONFIG_UNSUPPORTED_VERSION`, `SCHEMA_DRIFT`, `INPUT_NOT_FOUND`, `OUTPUT_EXISTS`, `DATA_RECORD_INVALID`, `DATA_RIGHTS_DENIED`, `DATA_DUPLICATE_ID`, `DATA_DUPLICATE_CONVERSATION`, `DATA_PARTITION_INVALID`, `RISK_REPRESENTATIVE_MISSING`, `RISK_SCORE_MISSING` |
| 3 | `environment` | `DEPENDENCY_MISSING`, `ENVIRONMENT_UNSUPPORTED`, `ARCHITECTURE_UNSUPPORTED` |
| 4 | `execution` | `BACKEND_FAILED`, `IO_FAILED`, `NETWORK_FAILED`, `TIMEOUT`, `OUTPUT_INVALID`, `INTERNAL_ERROR` |
| 5 | `integrity` | `INTEGRITY_FAILED`, `UNSAFE_ARTIFACT_PATH`, `LINEAGE_MISMATCH`, `LEAKAGE_DETECTED`, `PROFILE_MISMATCH`, `SAMPLE_OVERLAP` |
| 130 | `interrupted` | `INTERRUPTED` |

Unknown exceptions are caught at the CLI boundary as redacted `INTERNAL_ERROR`;
they never add ad hoc fields or a new exit code. `--help` is plain text, exits 0,
does not import MLX, and is exempt from `CliSuccess`.

## Verification and change control

`verify` validates the canonical manifest, artifact ID, every file size/hash,
safe paths, absence of symlinks and extras, recursive snapshots and bounds,
kind/stage/parent rules, source-right union, and all kind-specific invariants.
It proves recorded integrity and lineage only.

A contract change updates the canonical classes, generated schemas, examples,
this spec, and `representation-catalog.md` together. Readiness must reject an
untyped map, open `details`, missing command variant, undocumented null/default,
or any public output shape not listed here.

`VerifyResult.lineageNodeCount` counts unique ancestor artifact IDs, excluding the verified artifact itself; repeated snapshots in a lineage DAG count once. Doctor command availability means at least one supported invocation is available: checkpoint export remains available without Metal, while GGUF export requires MLX.

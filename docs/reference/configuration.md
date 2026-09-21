# Configuration reference

Foliqant accepts strict UTF-8 YAML or JSON. Configuration objects are closed:
unknown fields, duplicate keys, YAML tags, anchors and aliases, nonfinite numbers,
and type coercion are rejected. A configuration file is limited to 1 MiB.
Values such as `${HOME}` are ordinary text; Foliqant does not interpolate
environment variables.

Fields with a default may be omitted. Do not use `null` in place of an omitted
value. In particular, omit `authorizationRef`, `familyId`, `generation`,
`frozenFamilies`, `model`, `maxExamples`, `outputSchema` and `evidencePointer`
when they do not apply.

Identifiers used by `name`, `id` and `sourceId` are 1–128 characters. They start
with an ASCII letter or digit and then contain only letters, digits, `.`, `_` or
`-`. A language is a simple BCP 47-style tag such as `en`, `de` or `de-DE`.

New category catalogs normalize IDs to unique lowercase snake_case keys matching
`[a-z][a-z0-9]*(?:_[a-z0-9]+)*` and require nonblank descriptions. Normalization
collisions are rejected. Use `CategoryCatalog`
from `foliqant.decisions.category_catalog`; see
[category definitions](../guides/native-decision-data.md#define-categories-with-clear-boundaries).
This does not change historical V1 identifiers or rewrite stored artifacts.

## Native decision-data configuration

`CurationConfig.decisionData` selects the native state-and-questions generation
path. Omit it for the auxiliary source-format recipe. See
[native generation](../guides/native-decision-data.md) for full/pilot commands.

| Field inside `decisionData` | Type | Default | Range |
|---|---|---:|---|
| `examplesPerScenario` | integer | `4` | 4–10,000; cap over available genuine cases, not a promised row or family count |
| `sourceExamplesPerSource` | integer | `100` | 0–100,000; zero skips projection |
| `minimumAcceptedPerCell` | integer | `1` | 0–1,000; accepted jobs per eligible training scenario/language cell |

`familiesPerScenario` is not an alias and is rejected. Existing `endpoint` and
`generation` fields apply, including candidate, attempt, input and response
limits. The authored recipe supports English and German, and execution is serial.
Native full/pilot profiles select `[en, de]`; a custom recipe may select either
language. German response prose remains German; IDs and enum values stay stable. A zero cell minimum is intended for pilots; at least one accepted job is
still required for success. Projected jobs verify an unchanged source-derived
parent; authored jobs produce checked rewrites only for source IDs declared by
the seed. Ordinary authored cases declare all non-metadata sources. Whole-answer
adequacy cases declare only `original-state`, while `task-contract` and
`proposed-answer` remain fixed. All output is unreviewed research data. No
setting authorizes cloud fallback, human-gold calibration claims or production
use.

The initial authored catalog contains four genuine cases per scenario. A larger
`examplesPerScenario` value still selects only those four; it does not create
numeric repeats, alternate wrappers, or filler metadata. Planning reports the
requested cap, available catalog count and actual selected count.

## Dataset configuration

Pass a `DatasetConfig` file to `foliqant-model prepare --config FILE`. A relative
source `path` is resolved from the directory containing this configuration file.
Absolute local paths are also accepted. Paths are never resolved from an
environment variable.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `schemaVersion` | integer | required | Must be `1`. |
| `name` | identifier | required | Dataset recipe name. |
| `sources` | source list | required | 1–1,024 declarations with unique `id` values. |
| `seed` | integer | `42` | 0–4,294,967,295. |
| `validationFraction` | float | `0.1` | Greater than 0 and less than 1. |
| `calibrationFraction` | float | `0.1` | Greater than 0 and less than 1. |
| `testFraction` | float | `0.1` | Greater than 0 and less than 1. |
| `maxRecords` | integer | `100000` | 4–1,000,000 across all sources. |
| `maxRecordBytes` | integer | `1048576` | 1–16,777,216 bytes per JSONL record. |
| `frozenFamilies` | assignment map | omitted | Complete family-to-split map produced by curation; ordinary authored datasets omit it. |

The three held-out fractions must sum to less than 1; training receives the
remainder. Preparation also needs enough independent connected groups to place
at least one group in every partition.

### Source declaration

Each item in `sources` is a `SourceDeclaration`.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `id` | identifier | required | Must match every record's `sourceId`. |
| `path` | local path | required | UTF-8 JSONL file; relative paths use the config directory. |
| `license` | nonempty string | required | License or rights label recorded in lineage. |
| `licenseEvidence` | nonempty string | required | Evidence reference; do not embed secrets. |
| `trainingAllowed` | boolean | required | Must be exactly `true`. |
| `sharedTrainingAllowed` | boolean | `false` | Must be `true` before this source can train a shared adapter. |
| `redistributionAllowed` | boolean | required | Records the source permission; it does not grant permission. |
| `privacy` | string | required | `public` or `private`. |
| `authorizationRef` | nonempty string | omitted | Required for private sources and forbidden for public sources. |
| `attribution` | string | `""` | Attribution text retained with descendants. |
| `commercialUse` | string | `unknown` | `allowed`, `restricted` or `unknown`. |
| `restrictions` | string list | `[]` | Unique, nonempty restriction statements. |

These fields preserve the declaration you provide. They do not determine that a
source, trained weights or a descendant model is commercially cleared. Review
the actual license, authorization and inherited restrictions for the intended
use.

## Data records

Each source is JSONL: one `DataRecord` JSON object per line. Record IDs must be
globally unique across the prepared dataset.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `schemaVersion` | integer | required | Must be `1`. |
| `id` | identifier | required | Unique record identifier. |
| `sourceId` | identifier | required | Must match its source declaration. |
| `language` | language tag | required | For example `en`, `de` or `de-DE`. |
| `groupKeys` | string list | required | 1–256 unique, nonempty relationship keys. |
| `messages` | message list | required | 2–256 messages; conversation rules below. |
| `tags` | string list | `[]` | Unique strings. |
| `origin` | string | required | `human`, `synthetic` or `teacher`. |
| `reviewed` | boolean | required | Whether a person reviewed the example. |
| `familyId` | identifier | omitted | Stable whole-family identity used by a frozen curation plan. |
| `generation` | provenance object | omitted | Local generation lineage; valid only for unreviewed synthetic or teacher records with `familyId`. |

A message has a `role` of `system`, `user` or `assistant` and nonempty `content`
of at most 1 MiB in UTF-8. There may be one leading system message. The remaining
messages alternate user and assistant and end with user then assistant. The final
assistant message is the supervised reference answer.

`generation` records the provider, model ID, model-metadata digest, prompt,
parameter, request and parent-record identities. A model-weight digest is
included only when independently available. Curation writes this object;
source records and ordinary hand-authored records omit it.

## Curation configuration

Pass a `CurationConfig` file to `foliqant-model curate --config FILE`. The
built-in source catalog pins each asset revision, URL, size and SHA-256 digest.
Changing the configuration, source catalog or prompt implementation creates a
new run identity.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `schemaVersion` | integer | `1` | Must be `1`. |
| `name` | identifier | `financial-decisions-en-v1` | Curation run name. |
| `sources` | selection list | five built-in sources | 1–32 entries with unique source IDs. |
| `endpoint` | endpoint object | local defaults | Loopback OpenAI-compatible endpoint settings. |
| `generation` | generation object | bounded defaults | Candidate and scenario limits. |
| `seed` | integer | `42` | 0–4,294,967,295. |

Each source selection has an `id` and a `maxRecords` default of `1000`, bounded
from 4 to 100,000. Supported IDs are `banking77`, `typed-decisions`, `wanli`,
`multidogo-finance` and `tatqa`. Selection keeps related families together, so
the result may be below the requested cap.

### Local endpoint

| Field | Type | Default | Rule |
|---|---|---:|---|
| `structuredOutput` | string | `json-schema` | `json-schema` sends server-side constraints; `prompt` omits them. Both strictly validate final JSON locally; no automatic fallback. |
| `allowPrivateNetwork` | boolean | `false` | Allows a numeric RFC1918 IPv4 or IPv6 unique-local endpoint. Set it only for a trusted model server; public addresses, DNS names, credentials, redirects and ambient proxies remain rejected. |
| `baseUrl` | URL | `http://127.0.0.1:1234/v1` | HTTP on an IP loopback address by default, or an explicitly allowed private-network address; no credentials, redirects or ambient proxy. |
| `model` | string | omitted | Exact discovered model ID. Omission requires exactly one endpoint model. |
| `reasoningEffort` | string | omitted | `low`, `medium`, or `xhigh`; sent as `reasoning_effort`. Omission leaves the provider setting unspecified. Explicit null is invalid. There is no silent fallback if the server rejects it. |
| `timeoutSeconds` | integer | `120` | 1–600 seconds per request. |
| `maxTokens` | integer | `2048` | 1–32,768 generated tokens. |
| `temperature` | float | `0.3` | Finite, 0–2. |
| `maxResponseBytes` | integer | `8388608` | 1,024–16,777,216 response bytes. |

The endpoint is not contacted with `--prepare-only`. On a full run, discovered
model metadata is stored before generation. A resume must present the same model
identity.

Generation uses standard OpenAI SSE with `stream_options.include_usage: true`;
there is no separate transport setting or nonstreaming fallback.
`maxResponseBytes` covers the entire stream, including framing and discarded
reasoning. The adapter retains final-answer content only. It stops and retains
degenerate output after 1,024 consecutive JSON whitespace characters outside
quoted strings; this does not change the timeout or token budget. A transport
failure remains a fatal execution error, not a quality rejection.

The native full and pilot YAML recipes override the generic endpoint defaults:
standalone Splash at `http://127.0.0.1:8000/v1`, model
`incoai/Qwen3.8-27B-Splash`, `reasoningEffort: low`, `temperature: 0.1`,
`maxTokens: 8192`, and `timeoutSeconds: 300`. Root `.env` overrides these values.
Reasoning effort is supported in both structured-output modes and participates
in request, cache and run identity. It is not a separate hard token budget;
`maxTokens` bounds the total generated response, including reasoning where the
server counts it that way.

### Root `.env` overrides

`./scripts/curate-data` reads a root `.env` when present. It accepts only the
documented `FOLIQANT_CURATION_*` endpoint and generation variables in
[`.env.example`](../../.env.example), validates their types and bounds, and then
applies them over the selected curation recipe. Empty variables do not override
the recipe. It does not source arbitrary shell code, interpolate YAML, accept
credentials, or configure remote endpoints. Use a copied `.env` for local model
selection; Git ignores it by design. A shared root `OPENROUTER_API_KEY` entry is
ignored and is not exported to the local curation runtime or its workers.

`FOLIQANT_CURATION_REASONING_EFFORT` overrides `endpoint.reasoningEffort`;
`FOLIQANT_CURATION_TEMPERATURE` overrides `endpoint.temperature`. The checked-in
native profiles and `.env.example` select `low` and `0.1` respectively. Removing
an environment override uses the YAML value; to leave reasoning unspecified for
another model, use a custom recipe without that field and remove its environment
override too.

### Generation settings

| Field | Type | Default | Rule |
|---|---|---:|---|
| `maxCandidates` | integer | `100` | 1–100,000 total generation jobs. |
| `maxAttempts` | integer | `2` | 1–5 attempts per candidate. |
| `languages` | list | `[en]` | One or both of `en` and `de`, without duplicates. |
| `scenarios` | list | all five | Unique values from the five scenario names below. |
| `scenarioFamilies` | integer | `20` | 4–10,000 code-authored scenario families. |
| `maxInputCharacters` | integer | `16000` | 256–131,072 characters for both parent input and generated candidate; oversized parents are quarantined before inference. |

Scenario names are `withdrawn-request`, `multiple-intents`,
`missing-evidence`, `conflicting-information` and `changed-deadline`. Accepted
model outputs remain synthetic and unreviewed. See
[automated curation](../guides/automated-curation.md) for the runnable recipe,
source omissions and published datasets.

## Training configuration

Pass a `TrainConfig` file to `train` or `customize`. All values bound the backend
run; they are not hardware capacity promises.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `schemaVersion` | integer | required | Must be `1`. |
| `name` | identifier | required | Training run name. |
| `seed` | integer | `42` | 0–4,294,967,295. |
| `steps` | integer | `100` | 1–1,000,000 microbatches; divisible by `gradientAccumulation`. |
| `batchSize` | integer | `1` | 1–64; train and validation must each contain at least this many records. |
| `gradientAccumulation` | integer | `1` | 1–1,024. |
| `maxSequenceLength` | integer | `2048` | 64–131,072 tokens; overlong records fail rather than truncate. |
| `learningRate` | float | `0.0001` | Finite, greater than 0 and at most 1. |
| `numLayers` | integer | `-1` | `-1` or 1–1,024; `0` is invalid. |
| `rank` | integer | `8` | 1–256. |
| `scale` | float | `20.0` | Finite, greater than 0 and at most 256. |
| `dropout` | float | `0.0` | Finite, at least 0 and less than 1. |
| `gradientCheckpointing` | boolean | `true` | Enables backend gradient checkpointing. |
| `validationEvery` | integer | `25` | 1–1,000,000 steps. |
| `validationBatches` | integer | `10` | 1–1,000,000 batches per validation. |
| `saveEvery` | integer | `25` | 1–1,000,000 steps. |
| `timeoutSeconds` | integer | `3600` | 1–604,800 seconds for the training worker. |

Memory also depends on the exact model, quantization, trainable layers, sequence
length, activations and backend. A value inside these bounds can still exceed the
machine's available memory.

## Evaluation configuration

Pass an `EvaluationConfig` file to `evaluate`. `timeoutSeconds` is the total
generation deadline across the selected examples, not a fresh allowance for
every example.

| Field | Type | Default | Rule |
|---|---|---:|---|
| `schemaVersion` | integer | required | Must be `1`. |
| `seed` | integer | `42` | 0–4,294,967,295. |
| `maxTokens` | integer | `256` | 1–32,768 generated tokens per example. |
| `maxExamples` | integer | omitted | At least 1; omission evaluates the full selected split. |
| `outputSchema` | local path | omitted | Strict JSON Schema file, at most 16 MiB. |
| `evidencePointer` | JSON Pointer | omitted | RFC 6901 pointer to a quote or nonempty quote list. |
| `fieldPointers` | JSON Pointer list | `[]` | Unique pointers scored against the reference. |
| `timeoutSeconds` | integer | `3600` | 1–604,800 seconds for all generation subprocesses. |

A relative `outputSchema` path is resolved from the evaluation configuration.
The schema uses JSON Schema Draft 2020-12 and may contain only same-document
fragment references; external retrieval is disabled. Configuring a schema,
evidence pointer or field pointer requires both references and generated answers
to be strict JSON. The empty JSON Pointer `""` selects the document root. An
evidence value must quote input text exactly.

See [prepare your own dataset](../guides/prepare-data.md),
[train and customize](../guides/train-and-customize.md), and
[evaluate and audit](../guides/evaluate-and-audit.md) for command examples.

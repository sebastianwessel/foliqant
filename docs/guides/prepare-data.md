# Prepare your own dataset

A dataset contains examples of the input a model receives and the answer it
should produce. Preparation validates those examples, records their permissions,
and separates related records together so they cannot cross partitions.

For an unattended starting corpus, use
[automated public-source curation](automated-curation.md). It downloads pinned
assets, converts them without manual labeling and can use a loopback LM Studio
model for bounded augmentation. Use this guide when you already have authorized
JSONL records of your own.

## Describe each example

Store one JSON object per line in a UTF-8 `.jsonl` file outside the repository.
Each record needs:

| Field | Meaning |
|---|---|
| `schemaVersion` | `1` |
| `id` | Unique identifier across your datasets |
| `sourceId` | Source declared in your dataset configuration |
| `language` | Language tag, such as `en` or `de` |
| `groupKeys` | Shared identifiers for related threads, documents or translations |
| `messages` | Optional system message, then alternating user and assistant messages |
| `tags` | Optional labels for filtering or interpreting results |
| `origin` | `human`, `synthetic` or `teacher` |
| `reviewed` | Whether a person reviewed this record |

For example, this illustrates one record; a usable dataset needs at least four
independent groups:

```json
{"schemaVersion":1,"id":"example-001","sourceId":"practice","language":"en","groupKeys":["thread-001"],"messages":[{"role":"user","content":"Where can I find the account fees?"},{"role":"assistant","content":"fees"}],"tags":["intent"],"origin":"synthetic","reviewed":false}
```

Only the final assistant message is the supervised answer. Earlier assistant
messages are context. Messages must contain nonempty text. Image, audio and tool
messages are not accepted in this data format.

Choose grouping keys carefully:

- Keep a complete email thread in one group.
- Give a document and its translations the same family key.
- Group amendments, repeated reports and related customer cases where splitting
  them would reveal an answer across partitions.

Preparation catches exact duplicates and declared relationships. It does not
discover every paraphrase, privacy issue or unknown upstream training overlap.

## Declare the source

Save a YAML configuration beside your local data. Paths resolve relative to this
configuration file, not the current terminal directory.

```yaml
schemaVersion: 1
name: practice-intents
sources:
  - id: practice
    path: records.jsonl
    license: self-authored-example
    licenseEvidence: "Examples authored for this local exercise"
    trainingAllowed: true
    sharedTrainingAllowed: true
    redistributionAllowed: false
    privacy: public
    attribution: "Local practice examples"
    commercialUse: unknown
    restrictions: []
seed: 42
validationFraction: 0.1
calibrationFraction: 0.1
testFraction: 0.1
```

Declare real permissions for your source. Do not copy the example's permissions
onto customer or third-party data. Private sources also require an
`authorizationRef` pointing to your permission evidence; keep that evidence private.
See [licenses and permissions](data-licenses.md).

## Prepare and inspect

```sh
uv run --no-sync foliqant-model prepare \
  --config /absolute/path/to/dataset.yaml \
  --output /absolute/path/to/new-prepared-dataset
```

The output directory must not exist. The success result contains partition
record counts, the dataset content identity and a `diagnostic` flag. Synthetic,
teacher-produced or unreviewed records make the dataset diagnostic.

| Output | Purpose |
|---|---|
| `chats/<split>.jsonl` | Model-facing messages only |
| `records/<split>.jsonl` | Full normalized records and metadata |
| `leakage.jsonl` | Recorded relationships and content hashes |
| `manifest.json` | Identity, permissions, assignments, counts and file checksums |

The four split names are `train`, `validation`, `calibration` and `test`.
Fractions count connected groups, so record percentages can differ. Each held-out
partition receives at least one group. Training receives the remainder; settings
that leave it empty are rejected. Row order does not change the prepared content
identity. Unicode and newlines are normalized; case and meaningful whitespace are
preserved. Empty lines, duplicate record IDs and duplicate conversations fail.

Verify the completed artifact before sharing it with another local process:

```sh
uv run --no-sync foliqant-model verify /absolute/path/to/new-prepared-dataset
```

Next: [verify and recover artifacts](../operations/artifacts.md).

# Setup and data operations

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

The example omits `endpoint.model`, which requires exactly one discovered local
model. If LM Studio exposes several, copy the YAML and set the exact model ID.
Rerun with the same configuration and workspace to resume; changing the
configuration creates a new run, and changed stored model metadata is rejected.
`--offline` forbids remote asset and dependency downloads but still permits the
configured loopback endpoint. It succeeds only after every selected asset and uv
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

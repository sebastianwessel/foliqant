# Build a local research corpus automatically

The curation command downloads five pinned public sources, converts them into Foliqant records, freezes related records into one partition, and publishes a verified source corpus. With LM Studio running, the same command also asks one local model to generate bounded variants and scenario cases, checks them automatically, quarantines failures, and publishes two additional datasets.

No source record editing, hand labeling, cloud API, or model download is part of this workflow. Curation does not start training.

## Before you start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). The default source assets total 46.21 MiB; allow more space for converted records, generated responses and immutable dataset artifacts under `~/.local/share/foliqant`.

LM Studio is optional for source preparation. To run generation, load exactly one chat model in LM Studio and start its local server at `http://127.0.0.1:1234/v1`. The default recipe omits `endpoint.model`, so Foliqant requires exactly one suitable discovered model instead of choosing one arbitrarily. If the endpoint exposes several models, copy the recipe and set `endpoint.model` to the exact model ID returned by LM Studio.

If a local runtime returns empty final content with server-side structured output,
set `endpoint.structuredOutput: prompt` in your copied recipe. This explicitly asks
the model for schema-shaped JSON without the server's guided grammar. Foliqant
still parses and validates the final JSON strictly; malformed output is rejected.
There is no automatic fallback, and internal reasoning is never used as the answer.
The default `json-schema` mode retains the server-side schema request.

The endpoint must stay on loopback. Foliqant does not use ambient proxies,
follow redirects, fall back to a cloud service, download models, or manage the
server. LM Studio may activate an installed selected model when a request arrives,
according to its own loading settings.

The wrapper reads the ignored root `.env` and applies the checked allowlist in
[`.env.example`](../../.env.example). This is the usual place to choose your
loaded model, endpoint limits and output transport mode. The YAML recipe remains
the versioned baseline; `.env` remains local to your machine.

## Prepare the public sources first

Run preparation while LM Studio is stopped or still being configured:

```sh
./scripts/curate-data --prepare-only
```

The wrapper ensures the locked base environment while preserving already
installed optional and development packages, then runs the checked-in
[curation recipe](../../model/examples/curation.yaml). Preparation downloads
exact checksummed assets, reuses valid cached bytes, converts the sources,
removes reported duplicate or leaking families, freezes source partitions, and
publishes `source-corpus`. It does not contact the model endpoint.

The command writes one JSON result. Save its `runPath`, `reportPath`, and dataset paths rather than reconstructing the digest suffix. The default workspace layout is:

```text
~/.local/share/foliqant/
  curation-downloads/<catalog-digest>/  verified public-source cache
  curation/<name>-<identity>/
    configuration.json
    source-plan.json
    sources/                           converted source batches
    datasets/source-corpus/            prepared diagnostic dataset
    prepared-report.json
```

To verify the resulting dataset, pass the `source-corpus` path printed by the command:

```sh
uv run --no-sync foliqant-model verify /absolute/path/to/source-corpus
```

Preparation and verification establish byte integrity, declared lineage and partition rules. They do not establish label correctness, financial fitness, or permission for an intended product.

## Continue with the local model

After LM Studio is serving one model, run the same recipe without `--prepare-only`:

```sh
./scripts/curate-data
```

The command resumes the prepared run. It reuses verified downloads and the frozen source plan, records the discovered model metadata, and processes at most 100 generation jobs with one request at a time. Successful automatic checks add records to `augmented-corpus`. Rejected, malformed, unsupported or duplicate candidates are recorded as quarantined outcomes and excluded without asking you to edit them. Code-authored scenario families are published separately as `synthetic-regression`.

```text
pinned sources ──> source-corpus
                       │
                       ├── local variants ──> augmented-corpus
                       │
code-authored scenarios ──> local wording ──> synthetic-regression
```

The completed result reports `sourceRecords`, `generatedAccepted`, `generatedQuarantined`, dataset artifact IDs and `reportPath`. All generated or teacher-derived records remain `reviewed: false`. Model agreement and schema validation are diagnostic signals; neither is human adjudication.

Generation requires a final response with `finish_reason: stop` and content that
matches the requested JSON Schema. Some runtime and model combinations respond
without usable final structured content; those attempts are rejected or
quarantined instead of being treated as records.

## Resume and offline operation

Rerun the same command after an interruption. A configuration digest, full source-catalog digest and prompt implementation version select the run directory. Completed source conversions and candidate outcomes are reused. Once model metadata is stored, a changed model identity is refused rather than silently mixed into the same run.

Use offline mode only after uv dependencies and every selected source asset are cached:

```sh
./scripts/curate-data --offline --prepare-only
```

For a generation resume, `--offline` still permits the configured loopback LM Studio endpoint; it only forbids remote dataset and dependency downloads. A missing or changed cache file fails instead of being repaired or replaced silently.

Do not delete a run lock because a recorded PID looks stale. Inspect the run and process first. Closing a timed-out HTTP connection also does not prove that LM Studio stopped GPU work.

## Default sources and limits

The recipe selects up to 1,000 records from each source while retaining whole document, conversation or premise families. A cap can produce fewer than 1,000 records when the next family does not fit.

| Source | What it contributes | Important limit or omission | Terms recorded in lineage |
|---|---|---|---|
| BANKING77 | 77-way banking intent classification | Single queries, no thread state | CC BY 4.0 |
| typed-decisions | Five typed decisions over four workflow families | Synthetic states and three-sample teacher distributions; not human truth. Imported into the source corpus but excluded from augmentation because the current checker cannot independently judge soft target distributions. | Apache-2.0 |
| WANLI plus SemIf | Entailment, contradiction and insufficient-evidence judgments | GPT-3-origin pairs; 256 SemIf test cases stay held out; overlapping seed families are removed from training | CC BY 4.0 plus MIT selection metadata |
| MultiDoGO finance | Finance turn intents and slot labels | Published supervised files contain customer turns rather than complete alternating dialogues; sensitive slot values are redacted. Imported into the source corpus but excluded from augmentation because paraphrasing would invalidate token-aligned slot labels. | CDLA-Permissive-1.0 |
| TAT-QA | Financial-report table-and-text reasoning | Uses the labeled test-gold file; the unrelated unlabeled test serialization is omitted | CC BY 4.0 data; MIT repository code |

The download excludes WANLI raw worker annotations, MultiDoGO unannotated dialogues, TAT-QA's unlabeled test file, CUAD, model weights and gated assets. See [source research](../../specs/research/curation-sources.md) for exact revisions, sizes, checksums and converter counts.

The current training-augmentation jobs use eligible BANKING77, WANLI and TAT-QA
training families. typed-decisions and MultiDoGO remain available in the source
corpus; the exclusions above prevent automatic generation from changing
reference semantics that the current checker cannot preserve.

Research and noncommercial sources may be used when their actual terms permit the activity. Foliqant records the available evidence and restrictions; it does not impose a blanket commercial-only filter or claim that descendant weights are commercially cleared.

## Change the bounds

Copy [the example](../../model/examples/curation.yaml) and pass your copy with `--config`. The main controls are:

| Field | Default | Meaning |
|---|---:|---|
| `sources[].maxRecords` | `1000` | Per-source cap, 4–100,000, applied without splitting families. |
| `endpoint.baseUrl` | `http://127.0.0.1:1234/v1` | Local OpenAI-compatible endpoint; only loopback is allowed. |
| `endpoint.structuredOutput` | `json-schema` | Use `prompt` explicitly when server-side guided grammar is incompatible; local schema validation remains mandatory. |
| `endpoint.model` | omitted | Requires exactly one discovered model. Set an exact ID only when several are served. |
| `endpoint.timeoutSeconds` | `120` | Total deadline for one endpoint request. |
| `endpoint.maxTokens` | `2048` | Maximum generated tokens per request. |
| `generation.maxCandidates` | `100` | Total bounded generation-job budget. |
| `generation.maxAttempts` | `2` | Attempts before a candidate is quarantined. |
| `generation.languages` | `[en]` | Generated languages; `de` is also accepted. |
| `generation.scenarioFamilies` | `20` | Code-authored scenario families before local wording. |
| `generation.maxInputCharacters` | `16000` | Maximum characters allowed in the parent input and generated candidate. Oversized parents are quarantined before inference. |

Changing the configuration creates a distinct run identity. It never mutates an earlier completed dataset.

## Train only after inspection

Curation outputs ordinary verified dataset artifacts, so later training uses the existing `train` command and an explicitly chosen dataset path. The source and augmented corpora are diagnostic because they include public, synthetic, teacher-derived or unreviewed records. `synthetic-regression` is a separate stress-test population and must not be reported as representative production validation.

Next: [train and customize](train-and-customize.md), then [evaluate and audit](evaluate-and-audit.md).

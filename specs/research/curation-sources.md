# Public curation sources

Date: 2026-09-19. Status: exact public assets inspected and pinned; source adapters implemented. This note records source facts and conversion choices. It does not claim that public labels qualify Foliqant for financial production use.

The separate [broader business-process dataset review](business-process-datasets.md)
records banking/funds, insurance and public-sector candidates inspected on
2026-09-20. They are not additional pinned sources, and do not change the current
catalog, frozen runs or projections.

The packaged catalog is `model/src/foliqant_model/curation/source-catalog-v1.json`, with SHA-256 `10432e0564195bbdda5453dc2648105897af7cd7a76dee1b44db93fa4e03ad29`. Its 25 files total 48,449,751 bytes (46.21 MiB). Every URL contains an immutable Git commit or Hugging Face revision; acquisition verifies the exact byte size and SHA-256 before parsing. No gated source, access-token flow, model weight, or paid service is required.

## Selected sources

| Source | Immutable revision | Pinned input and observed structure | Reference semantics | Stated terms |
|---|---|---|---|---|
| BANKING77 | `57ec275d8078af65b7731c2a98be812d844a6d6b` | `banking_data/train.csv` (839,073 bytes, 10,003 rows), `test.csv` (239,961 bytes, 3,080 rows), and `categories.json` (77 labels). CSV columns are `text,category`. | Human-origin online-banking queries with intent annotations. Single utterances, not threads or evidence judgments. | Repository `LICENSE` is CC BY 4.0. Commercial computational use is allowed subject to attribution; retain the license notice when redistributing source data. |
| LocalLLaMA/typed-decisions | `ea9306458d6e9563628369a3d1e72e362fb381d2` | Combined `all/train-00000-of-00001.parquet` (598,824 bytes, 1,200 rows) and `all/test-00000-of-00001.parquet` (222,140 bytes, 400 rows). Each split has 300/100 cases for each of `agent_trace_observability`, `customer_service`, `invoice_processing`, and `security_incidents`; each case has five questions. Stable columns are `id`, `workflow`, `split`, `state`, `questions`, `gold`, `factors`, `label_agreement`, and `n_questions`. | Synthetic latent skeletons, model-rendered text, and the mean of three teacher-endpoint samples. The card explicitly says the score measures teacher agreement, not correctness. This is the public corpus used by Laya's typed-decisions experiment; it is not Laya's claimed private/general training corpus. | Dataset card metadata says Apache-2.0. Preserve its notice. Do not label its records human-reviewed or adjudicated. |
| WANLI plus the SemIf selection | WANLI `61c95318fd71c55b6ba355d76253254615f387ec`; SemIf `ca3ba65f142967030ecb453346e94d6f476a69df` | WANLI `train.jsonl` (25,438,383 bytes, 102,885 rows) and `test.jsonl` (1,233,111 bytes, 5,000 rows). Fields are `id`, `premise`, `hypothesis`, `gold`, `genre`, and `pairID`; labels are `entailment`, `neutral`, and `contradiction`. SemIf's `benchmarks/manifests/source-selection.jsonl` identifies exactly 256 official-test rows and their option order: 86 entailment, 85 neutral, and 85 contradiction. | GPT-3 generated the initial pairs; crowdworkers labeled and sometimes revised them. `genre` distinguishes `generated` and `generated_revised`. This is synthetic-origin data with human annotations, not an internal review. | WANLI is CC BY 4.0. SemIf's selection/build metadata is MIT. Attribute both components and keep their notices. |
| MultiDoGO finance | `baa30639c4b271f394b81443c842193407cdf26d` | Turn-level finance `train.tsv` (2,070,448 bytes; 15,213 customer turns, 1,684 conversations), `dev.tsv` (297,593 bytes; 2,167 turns, 240 conversations), and `test.tsv` (592,511 bytes; 4,361 turns, 482 conversations). Columns are `conversationId`, `turnNumber`, `utteranceId`, `utterance`, `slot-labels`, and `intent`. There are 18 intents; multiple intents use `<div>`. | Human-to-human elicited finance dialogue, but the published supervised splits contain only annotated customer turns. They cannot reconstruct alternating full dialogues. `conversationId` remains the leakage family. | `LICENSE.txt` is CDLA-Permissive-1.0. Computational use and publication are permitted under its notice requirements. Credential-like slot values are still handled as sensitive-like text. |
| TAT-QA | `870accc41953dcde885aabeb963d94aabdc0fbc3` | `dataset_raw/tatqa_dataset_train.json` (12,845,647 bytes; 2,201 contexts, 13,215 questions), `dev.json` (1,637,431 bytes; 278 contexts, 1,668 questions), and `test_gold.json` (2,167,546 bytes; 277 contexts, 1,663 questions). Each context contains a table, ordered paragraphs, and questions. Labeled questions contain `answer`, `derivation`, `answer_type`, `answer_from`, and `scale`; answer types are `span`, `multi-span`, `arithmetic`, and `count`. | Financial-report table-plus-text question answering with human reference annotations. The context/table UID is the leakage family. | The README says the dataset is CC BY 4.0. The repository `LICENSE` is MIT for code and does not replace the dataset terms. |

License fields are source metadata and a conservative implementation assessment, not a promise that a descendant model is cleared for every use. The catalog records attribution, redistribution, shared-training, commercial-use assessment, and source-specific restrictions in every lineage.

## Conversion rules

All converters produce the existing `DataRecord` chat shape with a structured JSON reference answer. They never copy hidden generator factors into model input. Every imported record has `reviewed: false`; `origin` is `human` for BANKING77, MultiDoGO, and TAT-QA, `teacher` for typed-decisions, and `synthetic` for WANLI. These values describe the source's creation process, not Foliqant review status.

Official train, validation, and test assignments remain `ImportedRecord.originalSplit`. `maxRecords` uses deterministic hashing and includes whole `familyId` groups. If an identical or related family crosses official partitions, the held-out partition wins and the overlapping training rows are excluded. The returned `excludedCounts` reports malformed rows, duplicates, family overlap, and cap omissions; the adapter never fills gaps with toy data.

- **BANKING77:** one record per query. The prompt supplies all 77 category IDs and the reference emits one intent. A normalized exact-query hash is the family, preventing duplicate text from crossing train/test. Six rows in the pinned corpus are removed because an exact-query family occurs in both splits.
- **typed-decisions:** parse only `state`, `questions`, and `gold`. `factors` is latent generation data and never model input. Preserve all four workflows and the full teacher distributions. At the default 1,000-record cap, retain all 400 test cases and deterministically select 600 training cases.
- **WANLI/SemIf:** map entailment/neutral/contradiction to supported/insufficient/contradicted and preserve SemIf's per-row option order for its 256 selected test cases. `pairID` is the seed family. Exclude 329 WANLI training rows belonging to the 204 seed families used by the SemIf test selection. At the default cap, retain all 256 SemIf cases and select 744 training rows without those families.
- **MultiDoGO finance:** use turn-level data, split `<div>` multi-intent labels, preserve slot-label arrays, and group all turns by `conversationId`. Deterministically replace sensitive token values with neutral `<redacted>` markers, never slot-type markers. Exclude ten pinned rows whose whitespace-token count does not match the slot-label count. Collapse 27 same-family duplicate payloads. Omit every copy of a payload repeated across different conversation families (15,554 rows), rather than retaining one record and losing its family aliases. This avoids connecting nearly the entire corpus through generic turns while preventing a discarded held-out alias from reappearing in training. The pinned corpus retains 6,150 unique convertible records.
- **TAT-QA:** create one QA record per labeled question and group by context/table UID. Include the table and ordered paragraphs in the user input and preserve answer, type, source, scale, and derivation in the reference. The current unlabeled `tatqa_dataset_test.json` has 1,669 questions and does not share stable IDs with `test_gold`; do not join the two heuristically. Use `test_gold` directly as the official labeled test asset. At a cap that bisects a context, return fewer records rather than split its family; the default cap produces 998 records.

Training augmentation currently selects eligible BANKING77, WANLI and TAT-QA
training families. typed-decisions stays in the source corpus but is tagged
augmentation-ineligible because its references are soft teacher distributions
that the current checker cannot independently judge. MultiDoGO also stays in the
source corpus but is augmentation-ineligible because paraphrasing would
invalidate its token-aligned slot annotations.

## Inspected but not selected in the first adapter set

CUAD is publicly downloadable and technically straightforward, but it teaches English contract-clause extraction rather than the initial correspondence and financial-report mix. The official GitHub repository is pinned at `67faa0e6023b04fcaae6cc09497ab00e5d63a2a2`; `data.zip` is 18,309,308 bytes with SHA-256 `f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a` and expands to `CUADv1.json`, `train_separate_questions.json`, and `test.json` (82,572,714 bytes total). The GitHub repository has no license file. The Atticus Project's official Hugging Face mirror at `a3c393f5d103fd0c516374e4fdff676c8176dcb1` labels the data CC BY 4.0 and contains the original datasheet. A later CUAD adapter should pin both the GitHub bytes and official license evidence, retain contract-level train/test grouping, and only be enabled when contract extraction is an evaluated target.

The following were deliberately omitted: WANLI's 120,676,261-byte raw worker-annotation file, MultiDoGO's large unannotated dialogues, TAT-QA's unlabeled test serialization, CUAD PDFs, Laya model weights, and all gated or terms-acceptance downloads. They add cost or ambiguity without being needed by the selected converters.

## Verification evidence

Actual pinned schemas were parsed locally without printing source text. The focused unit suite constructs minimal temporary records and covers all converters, synthetic/teacher provenance, held-out preservation, family grouping, sensitive-slot redaction, and WANLI seed leakage. A full offline conversion against the downloaded real bytes observed:

| Source | Returned at cap 1,000 | Unique convertible total | Reported exclusions before cap |
|---|---:|---:|---|
| BANKING77 | 1,000 | 13,077 | 6 cross-split family rows |
| typed-decisions | 1,000 | 1,600 | none |
| WANLI/SemIf | 1,000 | 102,812 | 329 training rows sharing SemIf test seed families |
| MultiDoGO finance | 1,000 | 6,150 | 15,554 cross-family duplicate payloads; 27 same-family duplicates; 10 invalid slot alignments |
| TAT-QA | 998 | 16,546 | none |

These counts describe deterministic conversion behavior, not training quality or benchmark results.

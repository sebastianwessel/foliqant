# Splash generation defaults and bilingual readiness

Date: 2026-09-20. Status: implemented; final live bilingual pilot, publication,
and immutable resume verified for the original request format. The subsequent
full-run timeout and corrected schema-order format are covered in the
[timeout follow-up](splash-schema-order-timeout.md). The corrected full run is
prepared but not started; the original 178 outcomes remain preserved separately.
These are research-pipeline checks, not production financial qualification.

## Effective settings

Native full/pilot recipes, `.env.example`, and the ignored root `.env` agree on:

- Standalone Splash, model `incoai/Qwen3.8-27B-Splash`.
- `reasoning_effort: low`; reasoning remains enabled.
- `temperature: 0.1`, `max_tokens: 8192`, request timeout 300 seconds.
- One model request at a time; no model fallback, disabled reasoning, or automatic
  effort escalation. Full generation permits two attempts per candidate; the
  bounded pilot permits one.
- Root environment overrides select `http://192.168.2.101:8000/v1` on this host;
  committed examples use loopback port 8000.

The earlier [reasoning comparison](splash-reasoning-effort-comparison.md) supports
low as the practical next generator setting. Its main comparison used
zero temperature, with a separate 0.1 counterexample check. This is not a
comprehensive temperature optimization study or evidence that low is optimal
for every German task.

The endpoint exposes optional `reasoningEffort` (`low`, `medium`, `xhigh`) and
forwards it in both structured-output modes. An unspecified generic adapter
leaves the parameter absent. Effective temperature/effort are bound into run,
request, cache and provenance identities. Unknown/null effort settings fail
validation. Changing recipes creates a new run and leaves older results intact.

## Language and validation changes

Both native presets select English and German. The pilot now contains 16 jobs,
eight priority scenarios per language. There are 132 authored parents in each
language across 33 scenarios, with explicit German source/task/reference prose.

JSON keys, IDs and plain-English enums remain unchanged. Only human-facing
prose is localized. Subjects must match source anchors; citations quote exact
source text. Published German summaries, missing facts and request descriptions
come from German canonical references, not arbitrary solver prose. A regression
test supplies an English solver rationale and confirms German publication with
unchanged English enums.

Localization validates the original evidence before remapping it and rejects
missing prose or invalid explicit quote translations. Full-source citation
fallback applies only when no explicit substring translation exists. English
and German siblings share semantic families and cannot cross data partitions.
German compatible imported records retain their source/claim/label text; the
five built-in downloaded corpora currently supply English records and are never
relabeled as German.

Generic preservation fixes cover numeric-bound comparison aliases, including
`or above`, `or below`, and strict `exceeds`, plus German comparisons, negation
and unit inflections, numeric dates and quotation anchors. Strict/inclusive
boundaries and comparison direction remain distinct. Fee/category ambiguity and
missing applicability reference facts were corrected. Recipe identity is
`native-decisions-v11`; lexical guard identity is v10.

German catalogs received independent semantic review and a separate grammar
pass. Language metadata does not detect language: the actual pilot source and
response prose were inspected. These checks do not establish arbitrary
multilingual correctness, complete linguistic entailment, or expert financial
review. Decimal separator conversion remains conservative.

## Final real-model pilot

Command: `./scripts/generate-data --pilot --offline --progress always`.

Run: `~/.local/share/foliqant/curation/native-financial-decisions-pilot-v1-b0e31786271a`.
The 32 serial model calls all recorded low reasoning, temperature 0.1 and
8,192 maximum tokens.

| Language | Candidate jobs | Accepted | Quarantined |
| --- | ---: | ---: | ---: |
| English | 8 | 7 | 1 |
| German | 8 | 5 | 3 |
| Total | 16 | 12 | 4 |

The four saved rejections were inspected:

- German: missing settlement evidence was incorrectly answered false rather
  than unknown.
- German: a generic current-balance phrase was incorrectly used as a request's
  identifying subject.
- German: a conditional branch citation omitted its EUR 100 subject anchor.
- English: mutually exclusive branches were incorrectly connected by a
  prerequisite relation.

No acceptance rule was loosened to admit these responses. Complete rejected
responses and reasons remain in the immutable call/outcome records for repair.
The small, deliberately varied sample is not an accuracy estimate; full-run
scenario coverage is not guaranteed by passing this pilot.

`foliqant-model verify` passed for the published native dataset and both
prepared full-run datasets. Every published pilot native input/output pair also
passed independent contract/evidence validation. The final pilot dataset has
10 German training rows (five checked rewrites plus their five required parents)
and 27 German held-out rows, all retaining their recorded language. English
training rows total 14; accepted generated rows are counted separately from
parent and held-out records.

Rerunning the identical public pilot command reused all 16 outcomes. All 84
tracked cache/outcome/dataset files remained byte-identical, with the same 32
cached calls and no new generation. Cross-split family checks passed.

A preliminary bilingual pilot was stopped gracefully after 13 jobs for the
catalog grammar review, with exit code 130 and preserved results. It was not
promoted into the final run. Earlier English-only and validator replay evidence
remain separate; saved-output replay is not fresh model generation.

## Prepared full run

Run: `~/.local/share/foliqant/curation/native-financial-decisions-en-de-v1-38d8294835d2`.

- 9,600 auxiliary source records; 1,264 native parents, including 132 German.
- 865 eligible generation jobs: 760 English and 105 German.
- At most 2,150 model calls under the configured two-attempt limit.
- At least one accepted job per eligible training scenario/language cell.
  A coverage failure remains an error; diagnostic outcomes stay available for
  a separate repair pass.

Start from the repository root with:

```sh
./scripts/generate-data --progress always
```

The source/environment caches are ready. The command creates/resumes this new
bilingual run, not an older English-only generation. It does not start training.

## Verification and private evidence

- Offline tests: 707 passed; seven native training/backend integration tests
  were deselected and are not claimed as executed here.
- Mypy, Ruff lint/format, 23 generated schemas, documentation/CLI checks,
  tracked-data audit, skill validation and diff whitespace checks passed.
- Docs, native profiles, configuration reference, repo skill and canonical spec
  manifest were updated together. The root `.env` remains ignored; no secrets,
  datasets, model weights or generated outputs were added to Git.

Private evidence: `~/.local/share/foliqant/checks/splash-bilingual-readiness-2026-09-20/`.
It contains final command logs, runtime source hashes/snapshots, pilot rejection
assessment and immutable-resume verification. Recorded model identity is server
metadata, not an independently verified weight digest.

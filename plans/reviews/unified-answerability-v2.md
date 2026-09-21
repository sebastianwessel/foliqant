# Unified answerability contract review

Date: 2026-09-22. Scope: native decision contract V2, offline data conversion,
current runtime/model tooling, examples, evaluation gold, docs and skills.
Baseline: `fe15f58`. This record supersedes the old missing-information/no-match
routing distinction in earlier reviews; it does not rewrite their measurements.

## Decision and implementation

Missing facts and requests outside the available answers share
`no_supported_answer`. Keep the concrete obstacle in the explanation and missing
facts, not a replacement enum, flag, or prose-parsing router. Independent conflict
and excess positively supported options retain `conflicting_information` and
`multiple_valid_options`. A permitted null request category still carries the
unified issue; an otherwise complete collection may remain answerable.

Native input/output versions advance to 2. Runtime, configuration, ordinary
native data preparation, training and evaluation reject old codes/versions.
Unrelated outer record, artifact and catalog versions remain unchanged. Current
seed, source projection, question-variant and migration recipe identities advance;
old caches and artifacts retain their identities.

The explicit `upgrade-decision-data` boundary checks recognized native V1
records, translates contract fields, and publishes through the existing dataset
transaction. It accepts the two audited historical system instruction prefixes
and known English/German issue-bearing criteria; unknown instructions fail for
review. Original source evidence and answers are not synthesized or corrected.
A frozen transformation preserves the upgraded artifact's verification behavior
when future generation prompts change.

Independent review found and resolved malformed-criteria coercion, duplicate JSON
key acceptance, and provenance verification that initially failed to prove exact
translation. Regression tests reject these and forged provenance. Ordinary
artifact verification checks the parent files/manifest, rights, families, records
and exact deterministic translation. No duplicated legacy contract hierarchy,
legacy runtime aliases, model judge, queue or persistence service was added.

## Actual offline dataset conversion

Parent artifact:
`3b424a9d609e7d5562a6614c0abc342b733c538776184e722cf52a97287d9fc0`.
Child artifact:
`5ee3ea7ded3f9151e4ae9af3d472d58fb06e3dbeaa2c9844b7cdf2bfa7e7f063`.
Private child path: `.foliqant/migrations/native-contract-v2/datasets/native-decisions`.

| Partition | Records |
| --- | ---: |
| Train | 1,868 |
| Validation | 282 |
| Calibration | 264 |
| Test | 499 |
| Total | 2,913 |

Languages remain 2,684 English and 229 German. Exactly 186 result issue lists
change; this particular corpus has no result carrying both merged codes.
The merge/dedup case is covered separately by synthetic regression tests.

An independent comparison checked every row: source state, business answers,
explanations/citations, status, metadata, languages and frozen splits are unchanged.
All 10 original dataset-file digests still match. The normal CLI verifier passed,
and repeating the exact upgrade reused the same child artifact. Full records and
conversational fine-tuning JSONL are both published and verified. Zero inference
calls, downloads or training were used for conversion.

The enclosing source run's 118 review items remain outside this dataset and
untouched. Conversion is neither acceptance of those items nor fresh blind
verification of any row. Trained weights, historical evaluation reports and remote
Hugging Face publication have not been changed.

## Verification

- Runtime offline suite: 876 passed.
- Model offline suite: 952 passed; 7 native integration tests deselected.
- Focused upgrade regression suite: 13 passed, including no-network,
  original immutability, four splits, exact labels/evidence, rights, restart,
  strict legacy rejection and parent/provenance tampering.
- Strict typing, lint and formatting passed for runtime/examples and model code.
- Runtime/model generated schemas, docs/CLI audit, tracked-data audit and strict
  MkDocs build passed. Both skill packages passed structural validation; the
  extended skill checker reports advisory size/cross-reference warnings only.

Support/HTTP example gold advances to revision 7 and private V2 paths. Separate
English/German vague-request and out-of-catalog scenarios remain, with the same
issue and review route. Obsolete clarification/manual-triage finish files were
removed. Old reports and private gold exports are retained as historical evidence.

## Fresh local execution

Sequential measurements use the configured Qwen Splash endpoint, low reasoning,
temperature 0.1 and unchanged configured token bounds. Private full results,
explanations, expected/actual values and confusion matrices live under
`.foliqant/evaluations/native-v2-20260921T215541Z/`. No parallel model requests,
endpoint discovery, retries to chase labels or gold inferred from predictions
are used. The completed run has 52 evaluated attempts plus one pilot:

| Example | Passed checks | Attempts |
| --- | ---: | ---: |
| Support pipeline and isolated model steps | 184 / 184 | 30 |
| Extraction and read-only MCP context | 38 / 38 | 6 |
| Read-only MCP wiring | 28 / 28 | 4 |
| HTTP support boundary | 110 / 110 | 12 |
| Total | 360 / 360 | 52 |

All authored vague-request, out-of-catalog, conflict and multiple-intent checks
passed. This is a fresh run, not rescaled historical metrics or prediction-based
gold. The MCP-only checks do not call the model. There were no execution failures.

These small authored suites establish integration behavior only. They are not
calibrated confidence, population accuracy or financial production qualification.
No training, remote publishing or Git push is part of this change.

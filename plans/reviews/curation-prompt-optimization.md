# Curation prompt optimization evidence

Date: 2026-09-19. Model: `qwen3.8-27b-splash`, user-supplied private
OpenAI-compatible endpoint. Runtime model metadata is not a weight fingerprint.

## Baseline audit

The temperature 0.1, three-attempt, 20-job pilot
`financial-decisions-en-v1-56a8b7a475a1` reported 13 accepted and 7 quarantined.
All 20 outcomes and the 61 successful cached calls were audited. Every accepted
sample had a defect under the scoped-input contract: seven scenario samples
copied task rules into user content, four BANKING77 samples contained role or
generation envelopes (one changed a label spelling), and two WANLI samples
rewrote the full task instead of its claim. These pilot generated artifacts must
not be used as recommended training inputs. Their integrity verification proves
serialization and lineage, not semantic or formatting quality. They remain
immutable audit evidence outside Git.

Seven final quarantines comprised two changed-number cases, three truncated
checker responses and two checker-answer mismatches. The changed-number failures
included rewritten TAT-QA table/document metadata and newly invented unrelated
facts; they cannot all be described as changed monetary amounts. BANKING77's
embedded `77` is not matched by the numeric regex. One mismatch was correct
decision/reason with scalar evidence instead of an array; the other differed in
TAT-QA answer serialization and annotation fields. The old cache retains only
successful endpoint responses, so intermediate failed calls cannot all be
classified from it. Do not infer their reasons from the final per-job reason.

## Implementation decisions

- The generator receives only the editable request, claim, question or final
  scenario text, plus a broad answer-neutral task identifier.
- Code reconstructs immutable labels, evidence, tables and paragraphs and keeps
  preceding conversation messages. Generated wrappers and rule copying are
  rejected before model checking.
- Numeric/date checks apply to editable content and count repeated occurrences.
  Literal quoted evidence remains required.
- The checker solves the reconstructed task using its original instructions and
  prior context. Answer formats state public task types, including evidence as
  an array. It never receives the reference answer or expected scenario label.
- Input policy descriptors, transformations, checker format hints and prompt
  versions participate in recipe identities. Existing outcomes are not reused
  across those recipe changes.
- Existing system instructions condition training and evaluation. A universal
  state prefix or custom tokenizer token is not introduced without a matched
  inference/training experiment.

## Live verification

### Initial scoped-input pilot

Run `financial-decisions-en-v1-788c8fc84e28` used generator v3 and checker v5,
with the same 20-job, temperature 0.1, three-attempt configuration. It accepted
12 records and quarantined eight: six checker-answer mismatches and two truncated
checker responses. Accepted records comprised six scenarios, five BANKING77
requests and one WANLI claim; no TAT-QA record was accepted.

A separate agent inspected all 12 accepted records. Their reconstructed inputs
preserved immutable source fields, passed the final version-2 structural guards,
and retained quoted evidence and relevant dates, identifiers, amounts and
currencies. None duplicated its parent or an existing source conversation under
the audit's exact and normalized comparisons. No residual defect was detected by
that inspection. These are conservative paraphrases, not evidence of broader
task coverage or independent human adjudication.

### Final guarded pilot

Run `financial-decisions-en-v1-ad6ddfd1e467` completed with generator v4,
checker v6 and task guards v2: **16 accepted and four quarantined** (80% observed
acceptance). All four quarantines were checker-answer mismatches: three TAT-QA
jobs and one changed-deadline scenario. Accepted records comprised nine
scenarios, five BANKING77 requests and two WANLI claims.

A separate agent inspected all 16 accepted records and detected no structural
or semantic defects under this bounded audit. Every structured immutable sibling
was preserved, every reconstructed input matched `assemble_input`, and the
accepted candidates passed current guards. No exact or normalized source-field
duplicates or exact source-conversation duplicates were detected. This does not
exclude semantic near-duplicates. Same-model generation and checking are not
independent human adjudication, and this small sample lacks accepted TAT-QA or
changed-deadline coverage.

Execution used one request at a time and took approximately 11 minutes 21
seconds. Fifteen outcomes used one attempt, one used two and four used three.
There were 57 successfully cached calls; their mean duration was 10.65 seconds.
No deadline failure was reported. The final `.env` remained at 20 candidates,
temperature 0.1 and three attempts; no larger batch or training was launched.

All three published datasets passed `foliqant-model verify` with `valid: true`:

| Dataset | Records | Train | Validation | Calibration | Test |
| --- | ---: | ---: | ---: | ---: | ---: |
| source-corpus | 4,998 | 2,943 | 497 | 427 | 1,131 |
| augmented-corpus | 5,005 | 2,950 | 497 | 427 | 1,131 |
| synthetic-regression | 29 | 22 | 2 | 3 | 2 |

The report is at
`~/.local/share/foliqant/curation/financial-decisions-en-v1-ad6ddfd1e467/completed-report.json`;
dataset directories are beneath that run's `datasets/` directory. No generated
data or model weights are committed. Comparison with the defective baseline
cannot establish model accuracy. No holdout-based production quality or human
review is claimed.

### Remaining annotation bottleneck

Inspection of completed final-run quarantines found TAT-QA checker responses that
matched the semantic answer but differed in exact source annotations. Examples
included an empty derivation instead of a comparison expression for a span answer,
and a table-only origin instead of a combined table/text origin for a count.
Another span answer differed only in reference punctuation.
The current format hint does not describe all source-specific derivation
conventions. The exact-answer contract conservatively quarantines these cases;
this work does not normalize away those fields or claim their loss is harmless.
A future change must explicitly decide whether those annotation fields belong
in the learned target and validate the revised contract before accepting more
TAT-QA augmentations. Raising the acceptance rate alone is not sufficient.

## Offline verification

The final implementation passed 301 tests, with seven native integration tests
deselected. Strict typing, Ruff lint and formatting checks, 21 generated schemas,
documentation checks (22 guides and 31 CLI examples), tracked-data checks and
`git diff --check` also passed. This prompt-only change did not run model training
or the native training lifecycle.

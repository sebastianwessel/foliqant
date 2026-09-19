# Task-prefix experiment design review

Date: 2026-09-19. Status: preregistered recommendation before inference. This
review matches `scripts/evaluate_task_prefixes.py`. It defines a bounded paired
experiment for the existing local Qwen generative model and does not authorize a
default prompt change.

## Question and eligible data

Compare the unchanged source prompt with two answer-neutral wrappers around the
final user content. The experiment asks whether either wrapper improves exact
task output on frozen validation families without increasing invalid output,
slowing median generation materially, or harming an included source. It does not
compare models, train a model, tune on calibration, or inspect test data.

Read only the `validation` record file from a verified original `source-corpus`
artifact. Include English BANKING77, WANLI, and TAT-QA records with a frozen
family and no generation provenance. Exclude train, calibration, test, generated
records, typed-decisions soft targets, and MultiDoGO token-aligned targets.

Authored scenarios are also excluded. Their eligible validation sample is too
small for a balanced stratum, and we already examined their behavior in earlier
prompt work. Earlier stateless inference did not train or update the model; the
concern is the tiny, pre-examined sample and resulting weak independence, not
training contamination. Do not substitute scenario rows from another split.

## Frozen treatments

The original system message and all earlier conversation messages remain
byte-identical in every arm. Remove only the final reference assistant message.

- `baseline`: final user content unchanged.
- `task-key-user-v1`: `Task: {task}\nInput: {input}`.
- `instruct-query-user-v1`: `Instruct: {directive}\nQuery: {input}`.

`{input}` is the original final user content inserted byte-for-byte inside the
wrapper. The fixed source mappings are:

| Source | `task` | `directive` |
| --- | --- | --- |
| BANKING77 | `intent-classification` | `Classify the online-banking request.` |
| WANLI | `evidence-assessment` | `Assess the claim against the supplied evidence.` |
| TAT-QA | `financial-question-answering` | `Answer the financial-report question from the supplied source material.` |

These mappings are fixed before selection and contain no answer, target label,
scenario subtype, expected decision, reason, or evidence. The reference answer
must not affect a prompt, schema, seed, selection, or call order. The response
schema may expose the public field contract and complete allowed label set, but
never the correct choice.

## Deterministic paired sample

Set `SEED = 20260919`. Select 24 records: eight distinct validation families per
included source.

1. Sort eligible rows by record ID and keep the first row per family.
2. Rank those family representatives by the canonical digest of
   `[20260919, family_id]`.
3. Take the first eight for each source.
4. Interleave records by round in the fixed source order BANKING77, WANLI,
   TAT-QA.

Refuse fewer than eight eligible families for any source. Persist the verified
dataset artifact ID, selected record/family IDs, hashes, and full call plan before
inference. The family is the sampling unit.

This yields 72 serial calls: 24 records by three arms. For each record, use the
same record-derived seed in every arm:
`int(canonical_digest([20260919, record_id])[:8], 16)`.

## Counterbalancing and generation

Issue the three arms for a record as one block. Enumerate all six permutations of
the fixed arm tuple `(baseline, task-key-user-v1, instruct-query-user-v1)`. For
round index `r` and source index `s`, assign permutation `(r + 2*s) mod 6`. Across
24 blocks, each permutation occurs exactly four times and every arm occupies each
within-block position eight times.

Run one request at a time. Use temperature zero and keep model identity, endpoint,
structured-output mode, schema, token limit, timeout, and every other parameter
identical across arms. Bind the exact endpoint configuration, model metadata,
schemas, templates, messages, seeds, schedule, and script hash in the private
plan. Server model metadata is not a verified weight digest.

## Metrics

The primary metric is complete exact correctness: the endpoint must return strict
schema-valid JSON, and its entire object must be structurally equal to the
reference under the existing scorer's rules. A refusal, truncation, invalid or
schema-invalid response, or missing field is incorrect.

Report successful valid output, primary exact correctness, task-answer
correctness, and annotation correctness per arm and source. Preserve safe output
failure reason counts so failures can be audited rather than collapsed into an
unexplained total.

- BANKING77 task-answer and annotation correctness equal exact full output.
- WANLI task-answer and annotation correctness equal exact full output.
- TAT-QA task-answer correctness requires scalar/list shape and list order to
  match, exact `answerType` and `scale`, and elementwise `answer` equality after
  collapsing whitespace, stripping surrounding whitespace, and ignoring at most
  one final ASCII period. Do not normalize case, numbers, signs, commas, units,
  currencies, or other punctuation.
- TAT-QA annotation correctness jointly requires exact `answerFrom` and exact
  `derivation`. Raw outputs may describe those two fields separately after the
  run, but the preregistered boolean remains their conjunction.

TAT-QA task-answer correctness is diagnostic and cannot replace primary exact
correctness. It isolates the known annotation bottleneck without silently
discarding provenance or derivation fields.

Report wall time and input characters descriptively. The endpoint adapter does
not expose usage-token counts, so report tokens as unavailable rather than
inventing a proxy. Median wall time also enters the operational nomination gate;
it is not a statistical claim that one format is faster.

## Paired analysis and uncertainty

For each treatment versus baseline, report the four primary paired cells: both
correct, both incorrect, treatment-only correct, and baseline-only correct.
Report paired net gain, paired risk difference `net_gain / 24`, and the one-sided
exact McNemar/binomial p-value over discordant pairs.

There are two primary comparisons. Apply Bonferroni-adjusted one-sided alpha
`0.025` to each. Report the exact one-sided 97.5% lower confidence bound for the
treatment-win probability among discordant pairs. With no discordant pairs,
report a tie and no informative bound. Source slices, task-answer metrics, failure
categories, and latency are descriptive; do not add tests after seeing outputs.

The experiment is small. Wide uncertainty or many ties means inconclusive, not
equivalent.

## Failure and resume policy

Make one completed model outcome per record and arm. Do not retry refusal,
truncation, invalid JSON, schema failure, or another completed model output;
record it as invalid and incorrect. Retrying only failed arms would bias the
paired comparison.

On a transport or integrity failure, stop before issuing the next request. Resume
only when an existing completed call has the identical immutable call digest.
Do not change a prompt, seed, model, token limit, schema, or output mode within a
run. A prior uncertain fatal call requires inspection and a new preregistered run,
not silent reissue. Never run calls in parallel.

## Conservative nomination rule

A treatment may only be nominated for a separate confirmation run when every
condition holds:

1. primary paired net gain is at least two records;
2. one-sided paired p-value is at most `0.025` and the corresponding 97.5% lower
   discordant-win bound is greater than `0.5`;
3. exact-correct count is no lower than baseline for each included source;
4. valid-output count is no lower both overall and for every source;
5. TAT-QA task-answer count is no lower than baseline; and
6. treatment median wall time is at most 120% of baseline median wall time.

If both qualify, nominate the treatment with larger primary net gain, then fewer
invalid outputs, then the fixed identifier order `instruct-query-user-v1`,
`task-key-user-v1`. Report all gates and the selected identifier explicitly.

The pilot never changes a default. A nomination requires confirmation on a
disjoint, preselected set of unused validation families with the same source
balance. Calibration and test remain untouched until a prompt is fixed under a
separate authorization. If no treatment clears every gate, retain baseline and
report no demonstrated benefit; do not weaken the gates or inspect other splits.

## Required private evidence and limits

Keep plans, calls, and model responses outside Git. Bind the report to the plan
digest, verified dataset artifact ID, selected record and family IDs, exact
message/template/schema bytes, schedule, seeds, model metadata, endpoint
parameters, script hash, response hashes, scorer rules, and cache status.

The final report must state that public-source overlap with upstream pretraining
is unknown, model metadata is not a weight fingerprint, local generation may not
be bit-reproducible, the same small validation sample nominated the candidate,
and no outcome is human review, financial qualification, or a test-set claim.

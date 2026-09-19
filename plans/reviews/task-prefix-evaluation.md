# Task-prefix evaluation evidence

Date: 2026-09-19. Status: all 72 calls completed; no prefix nominated. This report
records the frozen validation-only experiment defined in the
[design review](task-prefix-experiment-design-review.md). The existing input
format is retained; no generation or training default changes.

## Research basis and limits

The [Qwen3 Embedding paper](https://arxiv.org/abs/2506.05176) describes a
dedicated embedding and reranking family built with a multi-stage training
pipeline that includes supervised fine-tuning. The
[official Qwen3-Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)
formats retrieval queries with a one-sentence instruction and reports retrieval
benefits from query-side instructions. Retrieval documents do not receive the
same prefix. Those findings motivate an ablation of concise task cues; they do
not predict generative correctness.

This experiment uses a user-supplied local Qwen generative checkpoint, not a
Qwen3-Embedding model. Qwen's
[official chat-template documentation](https://qwen.readthedocs.io/en/latest/getting_started/concepts.html#chat-template)
defines role-structured generation and system instructions, while the
[generation quickstart](https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html)
applies the model's chat template to messages before completion. The experiment
therefore treats the embedding-style wording as ordinary text inside the existing
generative chat contract. It adds no custom token and makes no claim that an
embedding result transfers to generation.

The endpoint identifies the loaded `qwen3.8-27b-splash` checkpoint through
runtime metadata. That metadata is not a verified weights digest. This is not a
fine-tuning experiment, an embedding evaluation, a production qualification, or
a multilingual evaluation. English is the only evaluated language.
It measures direct task solving under the frozen decoding settings, not curation
augmentation acceptance or the effect of learning prefixes during later training.

## Frozen treatments

Every arm retains the original system message. Only the final user message may
differ, and the original user content is appended byte-for-byte.

- `baseline`: `{input}`
- `task-key-user-v1`: `Task: {task}\nInput: {input}`
- `instruct-query-user-v1`: `Instruct: {directive}\nQuery: {input}`

The source-level task names and directives were frozen before selection:

| Source | `{task}` | `{directive}` |
|---|---|---|
| BANKING77 | `intent-classification` | `Classify the online-banking request.` |
| WANLI | `evidence-assessment` | `Assess the claim against the supplied evidence.` |
| TAT-QA | `financial-question-answering` | `Answer the financial-report question from the supplied source material.` |

The wrappers contain no correct label, reference answer, scenario subtype, or
record-specific decision. Public label and output constraints come from each
source's existing task contract.

## Frozen protocol

The sample contains 24 original English validation records: eight distinct
families from each included source. Family representatives and their order were
selected before inference without reading labels or model outputs. Authored
scenarios, generated records, train, calibration, and test records are excluded.

Each record receives all three arms as one block, for 72 serial calls. All six
arm-order permutations occur four times. The three calls for a record share one
deterministic record seed derived from experiment seed `20260919`. Generation
uses temperature `0` and keeps the endpoint, model identity, structured-output
mode, schema, maximum output, timeout, and remaining generation settings fixed
across arms. Requests are not issued in parallel and completed model outcomes
are not selectively retried.

Primary scoring uses complete structural equality with the reference output.
The preregistered report also records strict output validity, per-source exact
counts, paired treatment-versus-baseline outcomes, and TAT-QA task-answer and
annotation diagnostics. TAT-QA normalization changes only whitespace and at most
one final ASCII period; it preserves scalar/list shape, order, case, numbers,
signs, commas, units, currencies, and scale.

The endpoint adapter does not expose generated-token counts for this experiment.
The report will therefore describe wall time and ordinary input-character counts,
without presenting characters as tokens or estimating token usage.

## Results

| Format | Exact correct | Task-answer diagnostic | Valid output | Mean seconds | Median seconds | Mean input characters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 14/24 | 14/24 | 24/24 | 5.35 | 4.58 | 1,838.46 |
| Task/Input | 12/24 | 12/24 | 24/24 | 5.46 | 4.47 | 1,875.13 |
| Instruct/Query | 12/24 | 12/24 | 24/24 | 5.20 | 4.38 | 1,907.79 |

All calls completed without a retry, refusal, truncation or schema failure.
Approximately 6 minutes 24 seconds were spent inside serial call measurements;
plan publication to report publication was approximately 6 minutes 54 seconds,
including preparation overhead and the interval before execution.

| Task | Baseline exact | Task/Input exact | Instruct/Query exact |
| --- | ---: | ---: | ---: |
| BANKING77 | 7/8 | 7/8 | 7/8 |
| WANLI | 7/8 | 5/8 | 5/8 |
| TAT-QA | 0/8 | 0/8 | 0/8 |

For each treatment versus baseline, the paired table is identical: 12 both
correct, 10 both incorrect, zero treatment-only correct, and two baseline-only
correct. Net gain is -2/24 (-8.33 percentage points); the preregistered one-sided
improvement p-value is 1.0 and the one-sided 97.5% lower bound on discordant win
probability is zero. These data provide no evidence of improvement. Two losses
in a small sample do not prove that either format is generally harmful.

Both treatments fail the primary gain, paired-evidence and no-task-regression
gates. Neither is nominated for confirmation. The small median latency differences
are descriptive, not repeat-tested evidence of a speed advantage, and do not
justify lower observed correctness. Keep the baseline; do not enlarge the search
or weaken metrics to obtain a positive result.

The task-answer and joint annotation metrics also equal 14, 12 and 12 overall.
TAT-QA scored zero on all preregistered metrics in all arms; interpretation of
those failures requires the shape/annotation audit below, not a claim that every
underlying financial answer was wrong.

### Independent audit

A separate agent reconstructed the selected validation families, arm order,
messages, schemas, seeds, model identities and result counts from the private
records without using the runner's summary function. All 72 envelope hashes and
request links verified; the plan/report digest, source artifact ID and frozen
runner hash matched. All six arm permutations occurred four times, and file
timing was consistent with serial execution. Original system and source content
remained byte-identical. Reference answers did not enter the model requests or
schemas. Independent paired calculations reproduced every reported count.

Both prefixes lost on the same two WANLI records: the baseline matched the
reference's insufficient-evidence judgment, while both prefixed arms selected
the same stronger, incorrect judgments. No treatment won on another record.

TAT-QA needs particular care. In every arm, `answerType` matched 8/8 references,
`scale` 7/8, `answerFrom` 7/8, and `derivation` 0/8. Agent inspection found six
apparent answer-value matches whose representation differed (scalar versus list,
number versus string, or currency/numeric notation), one near-equivalent textual
span, and one substantively narrower answer. These are descriptive observations,
not newly awarded correctness points or human adjudication. One apparent value
match also had the scale mismatch.

The original financial-QA instruction lists the five output fields but does not
fully specify scalar/list conventions, value formatting or source derivation
annotations. The shared schema permits several answer shapes. This limits the
financial-QA subtest's ability to separate representation errors from reasoning
errors. It is not evidence that all eight underlying answers were wrong. A future
output-contract investigation must define those conventions and use fresh
validation families; this run's prompts and scores remain frozen.

## Reproduction and verification

The runner is `scripts/evaluate_task_prefixes.py`. Pass an original verified
source-corpus with `--dataset` and a new external directory with `--output`.
Without `--execute`, it verifies data, discovers the configured local model and
publishes the immutable plan without generating. Rerun with the same arguments
plus `--execute` to execute that plan. The root `.env` supplies the trusted local
endpoint; temperature is explicitly fixed to zero for this experiment. Do not
change the script, model, configuration or source artifact between these steps.

Private evidence is under
`~/.local/share/foliqant/experiments/task-prefix-ablation-v1-20260919/`:

- `plan.json`: `1a42458b14efa94221113012059b0faa19e92926f8641a12c31a1f4969f7f2f5`
- frozen runner SHA-256: `ddde040d5106e3e3665ff6f07db4a771dd2bb01724ba90b3a564fc05d1b209dc`
- source artifact: `216d2b4a932d029c51a2e2154a2df5cb9fb424358dae9ff6cb3bdb63c87f0965`
- `calls/000.json` through `calls/071.json`: immutable request-linked outcomes
- `report.json`: aggregate metrics bound to the plan digest

The full offline suite passed 315 tests; seven native training integration tests
were deselected. Fourteen focused experiment tests cover leakage prevention,
selection, scheduling, scoring, paired statistics and nomination gates. Ruff,
formatting, strict typing (including the runner), documentation and schema checks
also passed. Native training was not part of this inference-only investigation.

## Evidence handling

Private datasets, selected record and family IDs, raw prompts, raw responses,
endpoint details, and run artifacts remain outside Git. This report records only
aggregate evidence and limitations. Public-source pretraining overlap remains
unknown, model responses may not be bit-reproducible, and automatic correctness
does not establish human review or financial fitness.

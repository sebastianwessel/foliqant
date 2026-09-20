# Quality-repair pilot evidence

Updated: 2026-09-20. Status: **bounded pipeline verification accepted; not a training approval**.

The corrected balanced pilot below completed generation, manual review, exact
artifact-membership verification and unchanged resume. Its 22 accepted jobs are
bounded execution evidence, not an accuracy estimate or full-recipe coverage.
Earlier runs remain diagnostic and are not relabeled as corrected artifacts.

## First repair pilot

Run `native-quality-repair-pilot-v1-024ed613dbab` used the configured local
`qwen3.8-27b-splash` endpoint, one request at a time, with one attempt per job.
It was paused normally at a candidate boundary after 17 of 35 planned jobs:
11 accepted and six quarantined. Exit 130 and the absent process confirmed the
pause. No cached artifacts were overwritten. The full reconstructed private
bundle is outside Git at
`~/.local/share/foliqant/checks/quality-repair/native-quality-repair-pilot-review-bundle.json`.

An independent pass inspected all 11 accepted inputs and canonical outputs.
The classifications, priority conflict, changed deadline, repeated request
targets and adequacy judgments were supported by their supplied evidence. This
is a small diagnostic sample, not an accuracy estimate. Publication was not
reached because the pilot was paused for the following findings.

The six quarantines have overlapping causes:

- `requests-partial`: the solver ignored a stated missing page containing
  another request, marked the collection complete, and used a generic object
  as a discriminating subject despite the supplied instructions. Keep rejected.
- `predicate-unknown`: the solver inferred that a transfer did not settle from
  the absence of a settlement report. This confuses missing evidence with
  explicit negative evidence. Keep rejected; reinforce the general distinction
  in the grounding instruction.
- `adequacy-omitted-request`: an equivalent rewrite of “unstated” to “not
  stated” changed the lexical negation count. This is a conservative guard's
  false rejection. More fundamentally, the task contract and proposed answer
  are evaluation objects and should not be rewritten. Freeze these explicitly
  and allow only the original state to vary; do not loosen factual guards.
- All three conditional cells exposed underspecified subject boundaries and
  status semantics. Quote the discriminating anchor and state that conditional
  status records the source's gate even when its separate predicate is known.
  Missing subject support in the solver's chosen evidence remains invalid.
- Conditional references also omitted `no_matching_option` for a concrete
  unmatched explanation request. Fix every affected reference and enforce the
  null-category/issue invariant generally, rather than accepting a model's
  whole response merely because it found this reference defect.
- `conditional-unresolved` additionally invented prerequisite/order edges
  between mutually exclusive branches. The existing relation guard correctly
  rejected these.

## Attribution and limits

These observations establish implementation, reference and prompt-contract
defects alongside actual model errors. They do not isolate the base weights,
fine-tuning, quantization or serving runtime as the cause of those model errors.
The configured model name and server metadata are not independently verified
weight provenance. No other model or hosted API was compared.

Earlier acceptance rates and this pilot's rate are not a controlled model
comparison: references, prompts, grouping, validators and sampled cases changed.
The accepted explanations now come from reviewed references after blind answer
agreement; they are not evidence that free-form solver explanations are reliable.

## Second repair pilot

Run `native-quality-repair-final-pilot-v1-06738ece7cb9` attempted the balanced
35-job plan with the repaired references and explicit source mutability. It
completed 13 jobs: five accepted and eight quarantined. The next generation
request hit the 300-second endpoint deadline, and a separate five-second,
read-only model-discovery request also timed out while connecting. The local
server was unreachable; no parallel inference was started. The CLI exited with
`TIMEOUT`, and no final training dataset was published. Completed caches remain
intact. The private bundle is `final-pilot-review-bundle.json` in the same
external audit directory.

All five accepted records were inspected. The complete, omitted-output and
correct-unknown adequacy judgments are supported, with task contracts and proposed
answers unchanged; only the original state varies. The omitted-output example
also verifies equivalent ISO/written dates. The ambiguous-choice and superseding
deadline answers are supported. The prepared native-seed artifact independently
passed the public `verify` command. These observations do not substitute for a
completed generation/publication/resume check.

The eight quarantines were:

| Reason | Count | Interpretation |
| --- | ---: | --- |
| Duplicate conversation | 3 | Correct answers but unchanged input; not new derivatives. |
| Truncated solver output | 1 | No complete structured response within the configured limit. |
| Semantic mismatch | 1 | Invented execution order from ordinary order of mention. |
| Citation not found | 1 | A quoted phrase omitted a word from the actual source. |
| Changed unit guard | 1 | False rejection of equivalent “per month” and “monthly”. |
| Subject missing from its own unit evidence | 1 | Correct subject, but that unit's quote omitted its anchor. |

The month/monthly guard needs a narrowly tested equivalence. The prompts need
explicit distinctions between execution order and mention order, instructions to
make a real wording change, and the already-required unit-specific citation and
graph constraints. These are final refinements; references and result fields do
not need another redesign. A fresh live run remains required after these changes.

### Runtime attribution limit

The truncated ordinal solver used an 8,192-token completion cap. Its task and
schema were small relative to the advertised context window, and successful
responses in the run were short. However, failed raw responses and their token
usage are not retained, so hidden reasoning versus repetitive output cannot be
distinguished from the available evidence. Do not label this a proven reasoning
exhaustion problem or confuse it with the later network outage.

LM Studio documents reasoning controls on its
[native chat API](https://beta.lmstudio.ai/docs/developer/rest/chat), but not in
the listed [Chat Completions parameters](https://beta.lmstudio.ai/docs/developer/openai-compat/chat-completions).
Its [model.yaml documentation](https://beta.lmstudio.ai/docs/app/modelyaml)
ties `enableThinking` to support in the actual prompt template. No undocumented
request flag or model-runtime change was introduced. Metadata-only failure
diagnostics and a controlled model-preset comparison remain possible follow-up
work; neither is evidence already obtained by this repair.

## Offline verification before endpoint recovery

After the final prompt and monthly-rate refinements on 2026-09-20:

- Full offline suite: **408 passed, 7 native integration tests deselected**.
  No training change is being qualified by this curation review.
- Strict typing passed for 52 source files; Ruff passed; all 23 generated schemas
  matched; documentation checks passed for 24 references and 32 CLI examples.
- Tracked-data and Git whitespace checks passed. No raw data or model artifacts
  were added to Git.
- The monthly-rate regression preserves amounts and rejects changed cadence or
  duration-to-rate changes; it also covers multiple spaces, newlines and a word
  suffix that must not be mistaken for the rate keyword.
- The new recipe completed the real public CLI's offline preparation. Run:
  `native-quality-repair-verification-v1-ce603a058735`. It prepared 9,600 auxiliary
  source records and the native seed dataset without inference. Both datasets
  independently passed `foliqant-model verify`.
- The endpoint remained unreachable on a second five-second model-discovery
  check. No fresh generation was attempted while it was unavailable.

The historical private recipe is
`~/.local/share/foliqant/checks/quality-repair/verification-pilot.yaml`.
The matching 52-file runtime fingerprint was saved beside that recipe as
`verification-pilot-runtime-sha256.json`. The read-only
`check_final_artifact.py` in that directory takes the completed run path and
fingerprint path; it checks exact accepted-parent membership, unchanged held-out
rows, canonical answers/citations, split isolation and runtime identity. Its
subsequent executions are recorded below.

## Third verification pilot

The recovered endpoint completed all 35 jobs in
`native-quality-repair-verification-v1-ce603a058735`: 26 were accepted, comprising
25 derivatives and one blind-verified projection, and nine were quarantined. The
published artifact contains 425 records: 374 held-out records and 51 training
records, split as calibration 91, test 179, train 51 and validation 104. It passed
the public verification command with artifact SHA-256
`fbbbf626fee7ad24b4779cfeeddb2f1d44a8329cd7f28e2c7dda61d26caf7ef0`.

The artifact check found the same 52 runtime files, no cross-family split leaks
and no unexpected parents. A resume reused all 35 outcomes and 67 request caches;
all 138 request, outcome and dataset files remained byte-identical.

Manual review classified the nine quarantines as six substantive solver errors,
one schema-invalid duplicate issue, one truncated solver output and one ambiguous
source-label disagreement. No validator false rejection was found. The same review
found a reference wording defect in the accepted answerable-ratio family: four
summaries name the numerator where they should name the computed ratio. Therefore
this artifact remains diagnostic and is not final training acceptance. The
previously unreachable endpoint had recovered. This finding required the fresh
corrected balanced run and publication/resume checks recorded below.

## Corrected balanced pilot

Run `native-quality-repair-ratio-final-v1-5cf4d705241f` completed all 35
planned jobs with the corrected ratio references. It accepted 22 jobs: 21
derivatives and one blind-verified projection. Thirteen jobs were quarantined.
Manual review found no incorrect accepted target or rejection that departed from
the implemented contract. The quarantines comprise five substantive solver
errors, two schema-invalid duplicate-issue responses, five conservative lexical
guard false rejections and one ambiguous source-label disagreement. There were
no unchanged-input or duplicate-conversation rejections.

The five conservative rejections are recall limitations, not semantic drift:
four rewrites expressed an existing negation with different tokens, and one
changed “year-end 2025” to “end of 2025”. The guards intentionally do not prove
general paraphrase entailment. Both source projections were inspected: the WANLI
claim is directly supported and was accepted; the ambiguous BANKING77 label was
correctly withheld after blind disagreement.

All four corrected authored-catalog ratio summaries were checked against their inputs and
criteria. The current ratio is 150/100 = 1.5, the debt-to-total-assets ratio is
45/100 = 45 percent, the expense-to-revenue ratio is 20/120, approximately 16.67
percent, and the management-fee-to-average-AUM ratio is 1.5/1000 = 0.15 percent.
Their stated threshold comparisons are correct. The selected answerable-ratio
rewrite was conservatively quarantined before solving because of the year-unit
token change; no incorrect ratio derivative was accepted.

The published artifact contains 416 records: 373 held-out records and 43 training
records, split as calibration 94, test 179, train 43 and validation 100. It passed
the public verification command with artifact SHA-256
`2961b70744fe1c0bd0be4b611e9c6f4190ff5ce501a353e17efbd69c16930018`.
Independent checks found no unexpected parents or cross-family split leaks and
confirmed that all 52 runtime files were unchanged. Resume reused all 35 outcomes
and 63 request caches; all 134 request, outcome and dataset files remained
byte-identical.

This is acceptance evidence for the bounded pipeline artifact. It is not a
training-readiness, full-coverage or model-quality approval: at least one planned
cell has zero accepted examples, and the observed conservative guard losses remain
part of the operating tradeoff.

The final code passed 408 offline tests, strict typing for 52 source files, Ruff
and all 23 schema drift checks. The private `ratio-summary-delta-proof.json`
confirms only four reference-summary strings and their derived identities
changed; all 132 authored inputs, semantic signatures, families and rewrite
permissions stayed unchanged. `ratio-final-artifact-check.json` and
`ratio-final-resume-check.json` retain the mechanical verification evidence.

The separate [Splash concurrency check](splash-concurrency-check.md) preserved
correctness but found only a 1.04 percent wall-time improvement for two concurrent
requests. Generation remains serial; no training or bulk generation was started.

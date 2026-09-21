# Curation recovery and source quality

Date: 2026-09-20–21. Implements the generic fixes identified in
[the rejection investigation](completed-run-rejection-investigation.md).
This is curation pipeline verification, not financial accuracy, training
readiness or production model qualification.

## Delivered behavior

- Recover invalid solver output against the same validated rewritten state.
  Rewriting failures are corrected in the rewrite phase. External repair loads
  phase-matching full responses from verified caches, including earlier attempts.
- Bind recovered response request hashes, schema and model identity to their
  original call identity. Preserve call evidence through repair and interruption.
- Provide bounded structural feedback without exposing reference answers.
  A valid semantic disagreement is terminal for automatic retry; native repair
  with only semantic disagreements stops before discovery or child creation.
- Permit tested English/German calendar-year-end equivalences without weakening
  actual durations, rates, year values or calendar boundaries.
- Preserve optional provider token counts, normalized finish reasons and a
  repeated-character diagnostic in endpoint caches/IPC. Missing/malformed optional
  metadata remains unavailable. Retain final content without silently editing
  output or storing internal reasoning. Token limits and reasoning stay unchanged.
- Give BANKING77 all 77 exact source labels with bilingual editorial descriptions,
  contrastive boundaries and acknowledged overlap; normalize only new native IDs.
  Unknown source labels fail closed. Descriptions are not adjudicated source gold.
- Preserve WANLI as three-way text-relation choices, including neutral as an
  answerable category. Both texts and source annotations remain intact; there is
  no invented predicate missingness or forced conversion of questions into facts.
- Bind revised prompts, catalog definitions and source task semantics to new
  recipe/projection identities. No old generated outcome is transplanted.
- Limit solver request schemas to the result types used by the task and their
  reachable definitions. Keep all surviving constraints, schema ordering and
  complete V1 post-validation. Version the algorithm in the recipe and bind the
  actual projected schema into each request identity.
- Consume standard SSE internally without retaining reasoning text. Bound total
  received bytes, validate stream identity/framing and retain exact partial final
  content when 1,024 consecutive unquoted JSON whitespace characters accumulate.
  Only this deliberate content rejection enters bounded repair; malformed streams,
  identity changes, network errors and timeouts stop generation. Request format
  `declared-schema-order-sse-v3` prevents reuse of older transport calls.

## Data preservation

Original run: `native-financial-decisions-en-de-v1-continue-3beaee64b8c2`.
All 2,338 relative file paths and SHA-256 values matched the pre-edit inventory.
The published artifact
`844c86503e824db6c90442f54ec7a9759c9ae358bbc0e393f95b9837a9dc587d`
still passes public CLI verification. Its 654 accepted and 211 quarantined jobs
have not been edited, retried or deleted.

New offline preparation reused cached downloads without inference:
`native-financial-decisions-en-de-v1-04e5a5f333df`.
Its complete source plan, all 9,600 original source records, frozen source
families and all 1,008 native family assignments equal the previous run.
All 1,264 new seed input/reference pairs pass the native contracts; they comprise
264 authored seeds and 500 projections each for BANKING77 and WANLI. This is
structural validation, not evidence that every upstream label is correct.
This preparation predates the schema projection change; it remains immutable
and is not an instruction to resume generation with a changed recipe.

## Offline verification

- Full suite: **888 passed, seven native MLX integration tests deselected**.
  Final SSE root rerun completed in 98.65 seconds. Training/export code was not changed;
  no native MLX model qualification is asserted by these curation checks.
- Strict mypy: 63 source files passed.
- Ruff lint and formatting: 123 files passed.
- Generated schema drift: all 26 schemas match; V1 wire schemas unchanged.
- Documentation: 24 references and 33 CLI examples passed.
- Model-maintenance skill validation and canonical spec checks passed.
- Tracked-data audit and Git whitespace checks passed.
- Independent review verified all four recovered-call integrity regressions,
  catalog coverage, source-label isolation and recovery/documentation alignment.
- Independent schema review passed all 35 projection tests and validated all
  264 English/German authored seeds against the projected schema, strict output
  contract and existing semantic checks; no validation weakening was found.

Initial suite failures were test fixtures still asserting old projections and
two unfinished recovery test fixtures; they were corrected before the complete
passing rerun. No checks were disabled or acceptance gates reduced.
A later post-projection run hit the host's unaccepted Xcode license through
system Git in one workspace-policy test; the final full rerun used the existing
bundled Git on PATH and passed without changing that test or host settings.

## Live diagnostic pilot

Recipe: `5ccec7045e2474dad8f46d6ef82386e246da55804453b4dce97c9e493d0871a9`.
The 32-task purposeful sample selects only training jobs: 16 authored tasks
(known failures plus English/German controls), eight BANKING77 and eight WANLI.
This is a failure-oriented diagnostic, not a random accuracy estimate and not
the separate MultiDoGO/TAT-QA/typed-decisions projection-extension pilot.

Evidence lives outside Git in `curation-audits/recovery-46604b0daaf3efa9`, with
the frozen selection, request caches, retained outcomes and interruption report.
The first two formerly rejected authored cases passed. The third solver request
timed out at 300 seconds; its valid rewrite and both completed outcomes survived.
No timeout was misclassified as unanswerable or accepted data. Further requests
stopped until the operator observed cancellation in Splash, then the same pilot
resumed from its cached boundary. The same solver request timed out again at
300 seconds. The pilot therefore completed only two of 32 tasks; imported source
mapping quality and the remaining bilingual cases have not been qualified.

A separate controlled comparison reused the third task's validated rewrite,
messages, seed, model identity and generation settings, changing only the request
schema. It removed unused result variants and definitions: 9,444 to 3,480 compact
JSON characters, a 63.2% reduction. It also timed out at 300 seconds. Evidence is
in `curation-audits/schema-comparison-a6d4b180dafc549f`; no response was returned
to assess, and no result was accepted or published. The optimization reduces
request size but is **not a demonstrated timeout fix or throughput improvement**.
All 31 nonempty combinations of native result types are covered offline.

Model requests initially stopped after that comparison. Reasoning remained
low, temperature 0.1 and the output limit 8,192 tokens throughout, with one
request at a time. Backend logs are needed to distinguish slow generation,
decoding failure or other server behavior; the client timeout alone does not
establish the cause. Do not start a full generation run on the strength of the
offline tests or the two passing diagnostic cases alone.

An offline reproduction bundle is retained outside Git at
`curation-audits/timeout-repro-1243478a92088f43`. It contains both exact HTTP
request bodies reconstructed from verified frozen inputs, with checksums and
the original seed/settings. The bodies are 17,308 and 11,344 bytes respectively;
only the response schema differs. It contains no reference answers or endpoint
credentials and made zero network requests. Inspect backend logs and establish
that the server is idle before replaying; do not upload the private inputs
automatically.

Follow-up diagnostics first confirmed an idle backend through `/status` (no
queued/decoding requests, no stale status, healthy Metal). A preliminary
reconstruction at `timeout-repro-641736182c6b3deb` loaded schema metadata from
canonical storage, which reordered its keys. Its streaming and nonstreaming
requests passed full validation and reference agreement in 17.51 and 14.81
seconds, with identical final content. This **is not an exact reproduction**
of the failing request and does not establish a transport fix. A retained
`correction.json` records that confound; the corrected reproduction above
restores runtime schema order without changing schema meaning. Neither
diagnostic response was inserted into the training corpus or pilot outcomes.

The corrected declared-order streaming diagnostic at
`timeout-repro-1243478a92088f43/stream-20260920T222152Z` located the failure:
reasoning ended before final content began at 7.15 seconds. Partial JSON then
accumulated 1,026 repeated whitespace characters; the diagnostic disconnected
at 31.10 seconds. Internal reasoning text was discarded, not saved. The final
JSON stopped at a quotation in a German explanation summary. Thus the observed
failure is in final-output generation, not simply long reasoning.

Testing the existing `structuredOutput=prompt` mode in
`timeout-prompt-987c6fe6f7bf9bef` completed in 15.99 seconds but produced invalid
JSON: an unescaped string quotation at the same summary. The isolated
nonstreaming production worker also rejected it as invalid JSON. Its one-off
diagnostic helper did not retain the rejection response; the streamed final
response is preserved. Neither is accepted data, and defaults remain unchanged.
This motivates generic serialization guidance and paraphrased summaries, not
automatic quote repair, changed evidence or unconditional output-mode fallback.

Solver prompt v9 now asks for paraphrased summaries, verbatim evidence in
citation fields and correct JSON escaping while retaining exact decoded quotes.
The original failing task, with runtime schema order, seed and all generation
settings unchanged, completed through the normal nonstreaming worker in 22.07
seconds. Strict schema, citation/task validation and reference agreement passed;
the response used 1,174 completion tokens including 883 reasoning tokens.
Evidence: `curation-audits/serialization-control-fa59ca4da8ff33cd`.

The new v9 preparation `native-financial-decisions-en-de-v1-1ce06709613e`
retains the original effective configuration and all source snapshots, 1,264
native seeds, 1,008 frozen families and 865 job count. Its fresh 32-case diagnostic
at `curation-audits/recovery-817f1531671bf4f5` stopped on the eighth case after
another 300-second solver timeout: seven completed, four accepted and three
quarantined. All outcomes/caches and an interruption report are retained; old
outcomes were not copied. Prompt v9 improves the first reproduced case but does
not establish general timeout resistance. The v9 offline suite passed 870 tests
in 88.06 seconds, with seven native integrations deselected.

The SSE transport passed 68 endpoint tests and 67 generation-boundary tests,
including byte-fragmented CR/LF/BOM handling, fatal protocol errors with remaining
retry budget, and cached partial-output repair against an unchanged rewrite.
An independent review caught protocol errors accidentally entering repair and
read-ahead bytes being omitted from the response digest; both were corrected.
The hash and size limit now cover every byte returned by the transport read,
including any read-ahead beyond the last processed event. Final content retains
the entire processed content delta, without fabricating finish or usage metadata.

The original serialization control then passed through the real SSE worker in
18.73 seconds, with valid schema, exact citations and reference agreement.
Evidence: `curation-audits/sse-control-00fae161590643f2`. This single observation
establishes Splash compatibility, not a general performance improvement.

A fresh offline preparation, `native-financial-decisions-en-de-v1-1972df085fbb`,
uses recipe `897f18fb613a8beba300d925c8402764c5989458d673245f1902d11cc59cca59`.
Its effective configuration, source records, rights, seed tasks and frozen splits
match the v9 preparation. The new 32-case live diagnostic uses that identity and
retains its own calls; it does not transplant old outcomes. Evidence is retained
in the separate audit `curation-audits/recovery-ffdd99e72bcad729`.

## Completed SSE diagnostic

The full diagnostic completed in **686.87 seconds (11 minutes 27 seconds)**,
with **24 accepted and eight quarantined jobs**, 51 retained request-cache entries
and no request timeout. All requests were sequential at low reasoning,
temperature 0.1, 8,192 maximum tokens and a 300-second request deadline.
Splash reported idle after completion. All 2,338 original parent files were
rechecked against the pre-edit inventory and remain byte-for-byte unchanged.

| Group | Accepted | Quarantined |
| --- | ---: | ---: |
| Authored English/German tasks | 14 | 2 |
| BANKING77 intent choices | 4 | 4 |
| WANLI three-way relation choices | 6 | 2 |

Seven quarantines are semantic disagreements; one is a non-exact citation after
the allowed structural repair. These remain rejected with their full responses
and reasons. The failure-oriented selection is not a population accuracy estimate,
and differing source tasks, job identities and seeds preclude claiming a direct
acceptance-rate improvement over the original completed run.

The whitespace guard fired once on a German ordinal solver response. It retained
1,274 final-content characters, including 1,026 trailing whitespace characters,
with the real request/received-byte hashes and no invented finish reason or token
usage. The bounded second solver call reused the exact validated rewrite and
rejected partial response; its fully validated answer passed. No new rewrite,
reference-answer feedback or synthetic JSON repair was introduced. Separate
semantic disagreements stopped after one attempt despite a remaining attempt.

The [independent content review](sse-recovery-pilot-content-review.md) examines
response traces, evidence, source-label ambiguity and rewrite fidelity. Some
rewrites still add unsupported contextual detail without changing the requested
answer. Automatic checks and model agreement therefore remain diagnostic, not
human-gold review or training approval. Do not weaken citation/semantic checks
to improve these acceptance counts.

The default `./scripts/generate-data --progress always` configuration matches
this prepared run exactly (configuration digest
`0962599a6b75a6c890e492277f8320644e7e1747dfa9de58a9238c9775c2bbee`).
It can start a new diagnostic full run using verified cached sources. It must
not use `--repair-from` or `--continue-from` to transplant outcomes from the old
recipe. The diagnostic pilot cache is deliberately separate; no full generation
was started and its 51 requests are not claimed as reusable full-run work.

## Operational boundary

The completed historical run remains usable as diagnostic data. Its configuration
and V1 records are readable, but its old recipe cannot be repaired into these
changed source tasks. A new full run must use the new recipe and may reuse only
verified source downloads, not old outcomes. Reference disagreements remain for
adjudication or exclusion; repeated agreement-seeking is not a quality strategy.
No training, bulk repair, source-extension inference, push or release was started.

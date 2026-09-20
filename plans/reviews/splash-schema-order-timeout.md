# Splash timeout and schema-order repair

Date: 2026-09-20. Status: serialization repair implemented; bounded live checks
passed. Full generation has not been restarted.

Follow-up: explicit native `--continue-from` now preserves completed outcomes
across this recipe boundary. The new child keeps original provenance, retains
quarantined outcomes and generates only missing jobs. The earlier statement
below about the default full command still applies; use continuation to avoid
regenerating completed candidates.

## Incident and evidence

The user-started run `native-financial-decisions-en-de-v1-38d8294835d2`
stopped at 178/865 outcomes: 162 accepted, 16 quarantined. It retained 410 call
records. Of 405 successful calls with timing metadata, median duration was 9.2s,
95th percentile 36.6s and maximum 61.5s. Job 179 is a German ordinal decision.
Its rewrite was cached; the unfinished call was the solver.

An unchanged public-command resume reused all 178 outcomes and 410 calls, then
reproduced the 300-second timeout. A further unchanged retry was explicitly
interrupted after about 60s to isolate the cause. Server status showed one
decoding request, zero queued requests and a healthy Metal backend. This does
not support parallel-request overload as the cause of this incident.

Sequential diagnostics kept the same task, schema meaning, seed, model, low
reasoning, temperature 0.1 and 8,192-token bound:

| Schema key order | Response transport | Observed result |
|---|---|---|
| Alphabetically sorted | Non-streaming | User failure and reproduced 300s timeout |
| Alphabetically sorted | Streaming | Stopped diagnostically at 65s; partial JSON followed by repeated whitespace |
| Declared order | Streaming | Completed in 16.32s; valid answer |
| Declared order | Non-streaming | Completed in 10.61s; valid answer |
| Declared order, fixed isolated worker | Non-streaming | Completed in 10.89s; schema and semantic checks passed |

The sorted streaming diagnostic produced 3,517 content characters, including
3,195 whitespace characters, and never finished. It finished its reasoning
before entering the repetitive final-output pattern. This is evidence against
solving this incident by increasing reasoning effort or request time alone.

Splash's inspected frontend embeds the received schema into the prompt without
sorting its keys. Our worker IPC and HTTP canonicalization changed that order.
The diagnostic isolates order as a trigger for this request; it does not prove
that preserving order prevents all model or constrained-decoding failures.

## Changes and preservation

`declared-schema-order-v2` preserves schema order through IPC, prompt-mode schema
text and HTTP. Request hashes cover actual sent bytes and participate in call
identity. Generic and native recipe digests include the request format version.
Artifact integrity hashing remains canonical. No validator was relaxed and no
generation default, retry budget or reasoning setting changed.

Timeout errors now explain per-request deadlines and safe resumption. Transport
timeouts still stop rather than inventing a quality rejection or immediately
queueing more work. Docs and model skill guidance describe the boundary.

All 588 pre-existing outcome/call files were independently SHA-256 checked and
remain byte-identical. No diagnostic response was inserted into the old run.
New model-visible input requires a new identity; old outcomes are not relabeled
as if generated with the corrected request format.

The corrected full recipe is prepared, without inference, at
`native-financial-decisions-en-de-v1-136e68999269`. Effective configuration hash
remains `0962599a6b75a6c890e492277f8320644e7e1747dfa9de58a9238c9775c2bbee`;
the run changes because its generation recipe changed. Starting the public full
command generates this new run; it does not resume the original 178 outcomes.

## Verification

Three additional complete candidate checks passed with fresh private caches:
English choice (16.3s), German choice (16.2s), and the formerly failing German
ordinal candidate (11.8s). All were accepted by the unchanged automatic checks.
These are bounded diagnostics, not a new full bilingual pilot or quality study.

Regression tests cover schema order surviving the actual worker in both output
modes, exact wire-byte hashes, distinct cache identity for reordered schemas,
both recipe versions, and actionable timeout propagation without retries.
Final checks: 713 offline tests passed, 7 native training integrations deselected;
strict mypy, Ruff lint/format, all 23 generated schemas, documentation checks,
skill validation and whitespace checks passed. No native training was performed.

Private raw evidence, final responses, before-file hashes and diagnostic cache
live outside Git at `~/.local/share/foliqant/checks/timeout-resume-2026-09-20/`.
Hidden reasoning content was not persisted by the streaming diagnostics.

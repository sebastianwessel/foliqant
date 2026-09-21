# End-to-end acceptance paths

Future paths in [specification 10](../10-business-decisions-and-processes.md)
are requirements, not observed tests: snapshot -> typed observations -> validated
per-item evidence -> configured task plan -> persisted child set -> dispatch ->
join. Failures include ambiguous request identity, stale spans, unauthorized
children and an incomplete/failed join. Recovery preserves completed child work
across redelivery and later correspondence. Dataset discovery ends at a reviewed
candidate inventory and cannot acquire sources or invoke a model.

Each CAP has a success path and rejected/interrupted path in the traceability register. Common state flow is validated input -> reserved staging/workspace -> backend/processing -> verified output -> atomic completed artifact; failure produces no completed artifact and records diagnostic evidence where a backend had started.

First-run setup installs the isolated environment, acquires pinned assets, prepares separate diagnostic pools and verifies a receipt. Its offline rerun must perform no downloads. The general release path is fetch -> prepare -> optional quantize -> shared train -> evaluate -> merge -> customer customize -> evaluate -> merge -> export -> independent standard inference -> verify. Risk selection uses calibration predictions; audit uses untouched test predictions from the same profile. No deployment is performed. Docs and skills must describe the same executable path.

Unhappy-path coverage: invalid config and unknown fields; private data without authorization; duplicate IDs/content and transitive grouping; empty partition; unsafe artifact path; modified parent; incompatible customer parent; nonexistent/NaN adapter; timeout/SIGINT; backend nonzero exit; disk/partial output; unsupported export; malformed model output; null scores; calibration/test overlap and profile mismatch; insufficient audit; corrupt export; broken documentation/skill references.

Real integration evidence is separate from mocks used to test isolated error handling. Preserve the exact commands, versions, measurements, artifact identities, assertions and limitations in `plans/reviews/`. Do not record a mocked test as a real MLX or standard-inference pass.

The native decision-data path is configuration -> pinned auxiliary acquisition ->
source/native family freeze -> compatible projection and oracle seed publication
-> serial train-only generation and blind checking -> coverage gate -> verified
native dataset. Prepare-only ends after seed publication. Interruption resumes
immutable outcomes. Network/integrity failure and coverage shortage never become
semantic unanswerability or a completed-success result.

Native recovery tests must distinguish invalid output from valid disagreement:
preserve a verified rewritten state for solver-only correction, route rewrite
defects to rewriting, retain phase-specific call history across interruption and
external repair, and stop automatic label-chasing on semantic mismatch. Recovery
must bind retained call hashes, schema and model identity before any endpoint
request. Changed recipes reject incompatible repair/continuation rather than
rewriting parent artifacts. Source projection tests cover all BANKING77 category
definitions and WANLI neutral-as-relation behavior without fabricating missing
facts. Legacy native records remain readable under their unchanged schema.

Transport tests cover fragmented standard SSE, discarded reasoning, exact
received-byte hashes, valid finish/usage/DONE framing, bounds and model identity.
The 1,024-character unquoted JSON whitespace limit must retain exact partial
final content and reach bounded phase repair, without firing inside escaped
strings. Incomplete streams and timeouts remain fatal transport failures; no
partial output, synthetic finish reason or silent fallback is accepted. A fresh
sequential live pilot must qualify the changed request format before bulk use.

## Required behavioral cases

The [semantic vectors](../03-contracts/fixtures/semantic-vectors.json) are language-neutral expected cases, not generated model outputs. Implement tests against actual parsers/scorers/loss masks, not copied test-only algorithms.

| Area | Required negative or boundary proof |
|---|---|
| Curation | Pinned imports, original holdouts, connected-family deduplication, conservative rights, loopback-only schema validation, automatic quarantine, immutable request cache, interruption/resume, and separate diagnostic regression publication. |
| Native decision data | Every typed question/result variant; explicit absence versus unknown; exhaustive issue codes; explicit adequacy contracts; exact authored subject anchors; withdrawal and relation graph invariants; canonical reference-derived explanations; semantic date/value preservation; content-family isolation across values/paraphrases/translations; meaningful diversity counts; source projection verification with unreviewed status retained; accepted-parent-only train publication; no target leakage or held-out generation context; parent lineage; prepare/offline/resume; zero-acceptance and coverage-shortage OUTPUT_INVALID behavior with diagnostic state retained. |
| Setup | Exact hashes/sizes, HTTPS-only redirects, offline hit/miss, corrupt-cache refusal, relative workspace paths, Git-ignore preflight, private outputs, unchanged rerun receipt and no implicit training. |
| Explicit native migration | Offline reprojection from frozen annotations; unchanged authored/generated rows; complete source annotations and per-record rights links; same-state question variants retain scope, language and frozen family; source references never become model verification; pending-only generation, retained failed responses, immutable parents and cached resume. |
| Parsing | Duplicate JSON/YAML keys, YAML aliases/tags, NaN, unknown fields, booleans where numeric values are required, oversized config/record and unsafe paths are rejected without printing content. |
| Grouped data | A-B share a thread and B-C share a translation key: all three stay together regardless of source order. Reordering inputs yields identical partition contents. Four components give exactly one in each partition. |
| Rights | Missing general training permission fails prepare; missing shared-training permission fails train. Private source without authorization fails. Restricted redistribution propagates through customization and export. |
| Parent identity | Wrong adapter parent, altered parent file, shared train using any customer ancestor, customer customize using a customer release, and mismatched warm-start dimensions all fail before GPU training. |
| Customer QLoRA | Quantize merged shared checkpoint, customize the exact quantized result, and merge with that same parent successfully. Parent source remains shared. |
| Leakage | New customer dataset test row overlaps shared training by ID, group, full conversation or prompt hash: evaluation fails before generation. Check inheritance through quantization, merge, export and warm start. |
| Training mask | Two unequal-length token sequences: prompt and padding receive no loss; first/final completion targets do. No empty completion loss; overlength inputs fail rather than truncate. |
| Worker failure | Child nonzero exit, signal, timeout, malformed result, missing/nonfinite tensor or loss: no finalized output exists. Process-group termination and private retained workspace are observable. |
| Evaluation | Plain-text exact match can pass; valid JSON uses type-aware structural equality. Missing/empty/wrong-type evidence, missing fields and duplicate keys fail only applicable validators. Model generation is never replaced with reference text. |
| Risk | Duplicated/translated group members do not add risk trials. Calibration/test/profile mismatch fails. Fixed threshold audit never changes policy. Zero accepted yields insufficient/null bound; small-n bounds match independent analytic cases. |
| Artifacts | Corrupt/missing/extra/symlink/traversal files, duplicate inventory and forged parent metadata fail verification; safe stage/customer combinations pass. Concurrent same-destination writers cannot overwrite. |
| Export | Real dense checkpoint and GGUF load in their independent supported runtime. Unsupported GGUF architecture fails explicitly. Conversion success without load evidence remains unverified. |
| Guides | Every documented command parses in installed CLI; default walkthrough completes with actual tiny weights and outputs. Links, example paths, skills and CLI surface stay aligned. |

Real measurements must include the exact package lock and interpreter. A sandbox-only Metal denial is an environment failure, not evidence that the host GPU is unavailable: run the explicitly authorized local GPU check in the suitable execution environment and preserve both outcomes. Never bypass an access denial or treat a mock tensor as GPU evidence.

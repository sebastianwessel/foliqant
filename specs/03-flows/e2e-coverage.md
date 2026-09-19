# End-to-end acceptance paths

Each CAP has a success path and rejected/interrupted path in the traceability register. Common state flow is validated input -> reserved staging/workspace -> backend/processing -> verified output -> atomic completed artifact; failure produces no completed artifact and records diagnostic evidence where a backend had started.

First-run setup installs the isolated environment, acquires pinned assets, prepares separate diagnostic pools and verifies a receipt. Its offline rerun must perform no downloads. The general release path is fetch -> prepare -> optional quantize -> shared train -> evaluate -> merge -> customer customize -> evaluate -> merge -> export -> independent standard inference -> verify. Risk selection uses calibration predictions; audit uses untouched test predictions from the same profile. No deployment is performed. Docs and skills must describe the same executable path.

Unhappy-path coverage: invalid config and unknown fields; private data without authorization; duplicate IDs/content and transitive grouping; empty partition; unsafe artifact path; modified parent; incompatible customer parent; nonexistent/NaN adapter; timeout/SIGINT; backend nonzero exit; disk/partial output; unsupported export; malformed model output; null scores; calibration/test overlap and profile mismatch; insufficient audit; corrupt export; broken documentation/skill references.

Real integration evidence is separate from mocks used to test isolated error handling. Preserve the exact commands, versions, measurements, artifact identities, assertions and limitations in `plans/reviews/`. Do not record a mocked test as a real MLX or standard-inference pass.

## Required behavioral cases

The [semantic vectors](../03-contracts/fixtures/semantic-vectors.json) are language-neutral expected cases, not generated model outputs. Implement tests against actual parsers/scorers/loss masks, not copied test-only algorithms.

| Area | Required negative or boundary proof |
|---|---|
| Curation | Pinned imports, original holdouts, connected-family deduplication, conservative rights, loopback-only schema validation, automatic quarantine, immutable request cache, interruption/resume, and separate diagnostic regression publication. |
| Setup | Exact hashes/sizes, HTTPS-only redirects, offline hit/miss, corrupt-cache refusal, relative workspace paths, Git-ignore preflight, private outputs, unchanged rerun receipt and no implicit training. |
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

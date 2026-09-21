# Private Hugging Face dataset snapshot

Date: 2026-09-21. User-authorized private archival upload; no inference,
training, public release or source-repository push.

- Repository: [sebastianwessel/foliqant](https://huggingface.co/datasets/sebastianwessel/foliqant).
- Verified private before upload and after download.
- Commit: `75c7f2872977e6b9ed7e45474da98199705541ff`.
- Dataset configuration: `historical_native_v1`.
- Source artifact: `844c86503e824db6c90442f54ec7a9759c9ae358bbc0e393f95b9837a9dc587d`.
- Original run: `native-financial-decisions-en-de-v1-continue-3beaee64b8c2`.
- Remote artifact directory: `snapshots/844c86503e82/artifact`.

## Contents and verification

The original dataset artifact is copied byte-for-byte. Standard conversational
JSONL chat files are explicitly mapped to dataset splits; full provenance records,
manifest, source rights and leakage indexes remain alongside them. All 211
quarantined jobs, original seeds, outcomes and complete cached final responses are
in a separate review file excluded from the dataset configuration. That export
is not a complete resumable-run backup: endpoint configuration and request-cache
identity metadata are deliberately omitted. The original local run remains intact.

The staged upload contains only 14 allowlisted files, about 23.2 MB, outside Git.
It excludes the checkout, credentials, local paths, endpoint configuration,
raw request caches, source corpus, unverified projections and newer pilot results.
All 14 remote file hashes matched their local staged bytes. Both local JSON loading
and authenticated remote `load_dataset` at the pinned commit succeeded.

| Split | Rows | English | German | Independent components |
| --- | ---: | ---: | ---: | ---: |
| train | 846 | 656 | 190 | 506 |
| validation | 108 | 97 | 11 | 88 |
| calibration | 111 | 96 | 15 | 82 |
| test | 180 | 179 | 1 | 179 |

The training split contains 256 BANKING77 projections, 206 WANLI projections,
192 authored seeds and 192 accepted rewritten variants. The run's 654 accepted
jobs are not the same as its 846 training rows. Calibration and test must remain
outside training and model selection. None of these splits is human-gold review.

## Format and next version

Chat files already use `messages` with `role` and string `content`, including a
JSON-encoded assistant decision. The existing trainer consumes these files
directly. Conversational SFT tools can use the same format with the selected
model's chat template, appropriate assistant-target loss and token-length checks;
no data-format converter was needed for this publication.

This is explicitly a **historical diagnostic snapshot**, predating the improved
source mappings, prompts and recovery behavior. It is not relabeled as current
or as training-approved. The new 32-case diagnostic's 24 accepted cases remain
separate and do not add to these counts. New recipes require separately versioned
datasets and must not silently concatenate old versions.

The prepared additional-source plan currently selects 1,053 tasks: 500 MultiDoGO,
500 typed-decisions and 53 distinct TAT-QA comparisons. Its frozen split counts are
623 train, 114 validation, 95 calibration and 221 test. These are unverified
candidates, not additional accepted training data; regenerate compatible plans
for the new completed baseline before running the source-extension pilot.

Prioritize the corrected baseline, then the separate 32-task additional-source
pilot and reviewed expansion. More independent German held-out families and
adjudicated ambiguous/multi-intent/thread cases are needed: one German test row
cannot establish German quality. Repeated paraphrases alone do not close that gap.

All source rights are preserved, including restrictive or unresolved entries.
Private archival hosting is not a redistribution grant, commercial clearance or
permission to change this repository to public.

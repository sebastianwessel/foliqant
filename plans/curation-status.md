# Automated curation implementation

## Current native contract — V2

The latest completed migrated dataset has been deterministically upgraded to
native contract version 2, without model calls or changes to its parent. It
contains 2,913 records: 1,868 train, 282 validation, 264 calibration, and 499 test;
2,684 English and 229 German. Its artifact ID is
`5ee3ea7ded3f9151e4ae9af3d472d58fb06e3dbeaa2c9844b7cdf2bfa7e7f063`.
The upgrade changes 186 result issue lists to `no_supported_answer`; answers,
explanations, citations, rights, reviewed status, and splits are unchanged.
The parent's 118 review items remain unresolved historical evidence, not newly
accepted rows. No model training or remote dataset publication is implied.

Current evidence: [contract upgrade review](reviews/unified-answerability-v2.md).
The earlier run counts below are historical and are not the current corpus totals.

## Historical native run — 2026-09-20

The bilingual continuation `native-financial-decisions-en-de-v1-continue-3beaee64b8c2`
completed all 865 jobs: 654 accepted (75.6%) and 211 quarantined (24.4%).
The published native-decisions artifact is
`844c86503e824db6c90442f54ec7a9759c9ae358bbc0e393f95b9837a9dc587d`.
It passed the CLI integrity verifier at that revision. Accepted-job counts are not
training-row counts or accuracy estimates. Repair and source-projection pilot
execution are separate subsequent runs; neither has been performed in this update.

Published native-decisions contains 1,245 records (846 train, 108 validation,
111 calibration, 180 test), including 1,028 English and 217 German records.
The 9,600-record auxiliary source corpus remains separate. Authored accepted
rewrites can publish both a reference parent and its derivative, which explains
why accepted jobs and published rows differ.

The repair workflow at that revision targeted this completed continuation child with
`--repair-from`, retaining 654 accepted outcomes and retrying 211 quarantines.
Projection plans must be prepared from their exact completed parent:
old plans are bound to the exact earlier parent. Keep the 32-task sequential
projection pilot before a full extension. Full training remains a later step.

See [source projections](source-projections.md) for the completed offline tooling
and [business-process research](../specs/research/business-process-datasets.md)
for candidate datasets. New candidates are not enabled in the completed recipe.

## Earlier auxiliary curation acceptance

Tooling implementation and acceptance are complete. Canonical contract:
[automated curation](../specs/08-automated-data-curation.md). Detailed evidence:
[curation acceptance](reviews/curation-execution.md) and
[measurements](reviews/curation-measurements.json).

Delivered: five pinned importers; safe family splitting and provenance; local
JSON/schema generation; authored scenarios; independent checker pass; quarantine;
immutable caches and resumable publication; curate CLI; one-command wrapper and
recipe; documentation, skills, generated schemas and packaging.

Verified: 4,998 source records; real local pilot 1 accepted / 7 quarantined; exact
cache/artifact reuse; three verified published datasets; two-step native training
compatibility; 257 offline tests and all static/content/package checks.

At that earlier acceptance, the small generator had low acceptance
yield, while the Qwen corpus pilot exceeded its configured request deadline.
Prompt transport compatibility was demonstrated; the later completed bilingual
native run above supersedes its generation-status limitation. Neither run
establishes model accuracy or financial production quality.

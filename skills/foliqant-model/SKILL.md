---
name: foliqant-model
description: "Operates Foliqant setup, data curation, training, evaluation, policy audit, export, and artifact verification. Use when working with the local Foliqant model CLI or datasets; do not use for the separate workflow service."
---

# Foliqant model tooling

Use the installed `foliqant-model --help` or `uv run --no-sync foliqant-model --help`
in a checkout as the command authority. Do not invent missing commands, silently
train a replacement model, or substitute expected answers for model output.

For setup, automated curation, source configuration and recovery, read
[references/setup-and-data.md](references/setup-and-data.md).
For training, evaluation, risk policy, merging and export, read
[references/model-lifecycle.md](references/model-lifecycle.md).

For new classification catalogs, use `CategoryCatalog` from
`foliqant_model.curation.category_catalog`: detailed descriptions, deterministic
lowercase snake_case IDs, and collision rejection. Keep immutable V1 data and
generation identities unchanged. See the category guidance in
[setup and data](references/setup-and-data.md#category-catalogs).

Keep source datasets, weights, adapters and run outputs outside Git. The default
workspace is `~/.local/share/foliqant`; a chosen project-local workspace must be
ignored. Keep pinned source manifests, recipes and preparation code in Git.

The setup profile and automated curation outputs are diagnostic. They are not a
selected production financial model or evidence of financial/legal accuracy.
Curation needs no manual data edits: never describe teacher agreement, automatic
checks or `reviewed: false` records as human review. Never present toolchain
success as a quality or compliance guarantee.

Preserve exact model/dataset identities and source rights. Research/non-commercial
data may be used when its terms permit the current activity. Track restrictions
for later review; private hosting does not waive terms. Do not accept gated
agreements, upload private data or incur paid compute without authorization.

Keep curation stages separate: prepare pinned sources first, then optionally
resume bounded local generation with the same configuration, workspace and model
metadata. Training is a later explicit command. An omitted endpoint model is safe
only when exactly one local model is exposed; otherwise require the exact model
ID.

For the opt-in typed-decisions, MultiDoGO and TAT-QA native projections, first
use `prepare-source-projections` against an existing frozen run, then pass both
`--extend-projections-from` and `--projection-plan` to `generate-data`/`curate`.
Plan preparation must be fully offline: zero inference or model-discovery
requests and zero downloads. It may run after source snapshots, families and
splits are frozen even if generation is still active; extension execution must
wait for the completed parent. Resolve whole answer-bearing task groups before
caps: exclude every new member on conflicting targets, cross-split aliases or
cross-family aliases; retain the deterministic smallest-record-ID member for a
same-family, same-target duplicate group. Never modify existing baseline tasks.
Never imply that the default recipe enables these
mappings, transplant old outcomes, or combine an extension with continuation or
repair. Read the scoped projection section in
[setup and data](references/setup-and-data.md) before operating this path.
Use preparer `--pilot` for the exact 32-task projection pilot. Prepare a full
plan separately from the same parent and never promise reuse of pilot model
calls. Do not describe either mode as model-validated without recorded evidence.

Use `--progress auto|always|never` for the stderr operator display without
changing run identity or stdout JSON. On the first Ctrl+C, allow the current
candidate or preparation phase to reach its safe boundary and persist, then
resume by rerunning the exact same recipe, effective environment and workspace.
A second Ctrl+C can repeat the current request on resume and cannot guarantee
that the model server stopped its work. Do not invent a `--resume` flag or a
paused-success result.

Keep shared and customer stages separate. Shared training requires explicit
`sharedTrainingAllowed: true` for every new source. Customer customization starts
from a shared merged model and records the customer identifier. Never move a
customer adapter or its descendants back into shared ancestry.

Use calibration and test only for their declared purposes. Do not tune a policy
on test results, interpret token log probability as correctness probability, or
claim that a diagnostic dataset establishes production quality.

For the native state-and-typed-questions corpus, use `./scripts/generate-data`
(`--pilot` for the bounded bilingual check). Native profiles include English and
German. Keep German response prose and exact citations in German; language tags
are not language detection, and English imported data must not be relabeled.
Preserve the four generic input issue codes:
`missing_information`, `conflicting_information`, `multiple_valid_options`, and
`no_matching_option`. Missing referents use `missing_information`;
`multiple_valid_options` requires multiple positively supported catalog options
that exceed cardinality. Return every applicable issue. An explicit statement of
absence supports an answerable false predicate; absent evidence is unknown.
Any returned request unit without a catalog category adds `no_matching_option`.
A conditionally stated request remains conditional after its separate predicate
is resolved; extraction records the gate and does not execute the branch.
Keep `explanation.summary` to one grounded concise reason, aiming for 160
characters or fewer, with a second sentence only for a decisive limitation and
a hard 400-character maximum. Never truncate it; retain support and gaps in the
citation, contrary-evidence and missing-fact fields, which do not inherit the
summary bound. Do not add banking-specific enums, workflow actions, or
model-written confidence targets. A valid unknown answer differs from a
technical failure. Read the native-data section of
[setup and data](references/setup-and-data.md) before generation or format edits.

Treat completed artifacts as immutable. Inspect locks and failed run workspaces;
never automatically delete a stale lock, overwrite an artifact or kill a process
based solely on the recorded PID. Report typed errors without echoing source
messages or credentials.

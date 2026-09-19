---
name: foliqant-model
description: "Operate the local Foliqant model lifecycle: setup, automated public-source curation, data preparation, training, customer customization, evaluation, policy audit, model transformation, export, and artifact verification. Use for Foliqant model tooling, not for the separate workflow service."
---

# Foliqant model tooling

Use the installed `foliqant-model --help` or `uv run --no-sync foliqant-model --help`
in a checkout as the command authority. Do not invent missing commands, silently
train a replacement model, or substitute expected answers for model output.

For setup, automated curation, source configuration and recovery, read
[references/setup-and-data.md](references/setup-and-data.md).
For training, evaluation, risk policy, merging and export, read
[references/model-lifecycle.md](references/model-lifecycle.md).

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

Keep shared and customer stages separate. Shared training requires explicit
`sharedTrainingAllowed: true` for every new source. Customer customization starts
from a shared merged model and records the customer identifier. Never move a
customer adapter or its descendants back into shared ancestry.

Use calibration and test only for their declared purposes. Do not tune a policy
on test results, interpret token log probability as correctness probability, or
claim that a diagnostic dataset establishes production quality.

Treat completed artifacts as immutable. Inspect locks and failed run workspaces;
never automatically delete a stale lock, overwrite an artifact or kill a process
based solely on the recorded PID. Report typed errors without echoing source
messages or credentials.

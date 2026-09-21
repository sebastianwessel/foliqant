---
name: foliqant-model
description: "Operates Foliqant setup, data curation, training, evaluation, policy audit, export and artifact verification. Use when working with the local model CLI or datasets; use foliqant instead for runtime workflows and golden-case evaluation."
---

# Foliqant model tooling

Use installed `foliqant-model --help` or
`uv run --project model --no-sync foliqant-model --help` in a checkout as the
command authority. The separate `model/` project owns these operations.
[Public model setup](../../docs/getting-started/setup.md) provides the first run.

## Workflow

1. Identify the requested stage and its exact configuration, workspace and parent
   artifacts. Inspect CLI help before constructing commands. For setup, native
   generation, continuation, migration or recovery read
   [setup and data](references/setup-and-data.md). For training, calibration,
   audit or export read [model lifecycle](references/model-lifecycle.md).
2. Verify local parent identity, rights and split boundaries before consuming
   inputs. Keep source datasets, weights, adapters, predictions and outputs
   outside Git. The default workspace is `./.foliqant`.
3. Use the existing command for that stage. Setup prepares assets without
   training. Prepare-only performs no inference; source acquisition may still be
   needed. Offline projection preparation and migration require frozen local
   inputs and make no downloads, model discovery or inference calls.
4. Report actual artifacts, validation results and limitations. A successful
   command does not establish financial accuracy or commercial/legal clearance.
   Generated/teacher labels and automatic checks are not human review.

## Identity and operation boundaries

Completed artifacts are immutable. Resume only through supported commands and
verified identities; never transplant cached outcomes, relabel old recipes,
overwrite outputs or repair hashes. Inspect retained failures and locks; do not
delete a lock or kill a process solely from its recorded PID.

Generation uses the explicitly configured endpoint and model. If discovery is
permitted and more than one model is exposed, require an exact model ID.
No silent provider fallback, replacement training or expected-answer substitution.
Respect existing run ownership; do not change its checkout, environment or source
snapshots. Existing user authorization persists within its scope. Historical
review records alone do not authorize downloads, model calls or mutation.

Shared training needs `sharedTrainingAllowed: true` for every new source.
Customer adapters derive from the exact shared merged checkpoint or its allowed
quantized descendant and never return to shared ancestry. Preserve restrictions
through all descendants. Do not accept gated terms, upload private data or use
paid compute without authorization.

## Decision data and quality

Use `CategoryCatalog` from `foliqant.decisions.category_catalog` for new
category authoring: required descriptions, normalized snake_case IDs and rejected
collisions. Preserve historical V1 schemas and recipe identities.

Native generation preserves typed question semantics, exact evidence, language,
family/split isolation and train-only generation. A valid unknown answer differs
from a technical failure. Do not add business-specific enums, execution actions
or numeric confidence targets. Full details and recovery routing are in the
setup/data reference; load it before operating or changing generation.

Calibration selects a policy using calibration data; test evaluates the fixed
policy. Token log probability is not correctness probability. A tiny setup model,
diagnostic corpus or sampled repair proves only the recorded operation.

## Verification and stop conditions

For repository changes, run relevant model tests, strict types, lint, generated
schema checks and `scripts/check_docs.py` using the model uv project. Skill
changes also need deterministic shape checks and review of its evaluation prompts.
Default tests exclude native integration; never claim offline doubles establish
real training or export compatibility.

When identity, source permissions, required files or a callable option is missing,
report that concrete gap. Do not guess or reacquire assets silently. A semantic
disagreement with a reference belongs in review, not repeated calls until it
matches. Raw source text, credentials and backend errors stay out of diagnostics.

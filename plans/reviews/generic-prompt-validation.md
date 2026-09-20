# Generic prompt and validator refinement — 2026-09-20

Implemented a bounded refinement after the Codex model comparison. No dataset
records, oracle answers, scenario-specific validator exceptions, new issue enums,
provider adapters or API endpoints were added. Existing working-tree changes and
immutable generated artifacts were retained.

## Changes

- Clarified identifying request subjects versus generic action/document/product
  types, incomplete collections, unique issue codes and per-unit evidence.
- Added a concise solver self-check, including checking calculations rather than
  confusing an incorrect value with an omitted component.
- Clarified that rewrites must change wording, preserve missing-information scope,
  and treat source text as untrusted evidence rather than new instructions.
- Recognized `cannot` and common straight/curly-apostrophe negative contractions.
  Removing those markers is now rejected; equivalent spellings can pass.
- Recognized percentage symbols attached to numbers. Removing `%` is rejected,
  while a percentage symbol and a spelled-out percentage unit can agree.
- Recognized inclusive `or higher`/`or lower` comparisons, retaining separate
  strict inequality markers. Tested both directions and changed boundaries.
- Rejected identical or formatting-only rewrites before a solver request and
  during generated-record revalidation. Unchanged seeds and verification-only
  records remain valid. Token changes are not proof of useful diversity.
- Required nonempty supported partial collections and explanation evidence.
  Kept mechanical evidence validation separate from semantic judgments.

Published explanation targets still come from references, not generated solver
rationale. No noun blacklist, record-ID override or natural-language entailment
heuristic was added. Equal lexical marker counts cannot prove that facts retain
their subjects or logical scope; blind solving and reference checks remain.

## Live paired prompt check

Used the configured local `qwen3.8-27b-splash` through the existing endpoint
adapter. Froze eight new development probes (six solves and two rewrites) before
inference. They cover equipment maintenance, document types, specific references,
missing attachments, conditional branches, booking information and energy
calculations. No dataset holdouts or customer records were used.

Made 16 serial calls with alternating paired order, the same schemas and inputs,
temperature 0, a paired requested seed, a 4,096-token cap, a 90-second deadline,
and no retries. All returned schema-valid output and stable observed model
metadata. Metadata is not independent verification of model weights or server
determinism. Both arms used the same validator version during the trial.

| Check | Baseline prompt | Revised prompt |
|---|---:|---:|
| Strict decision checks | 3/6 | 4/6 |
| Rewrite guards during trial | 1/2 | 1/2 |
| Rewrite guards after separate inclusive-comparison fix/replay | 2/2 | 2/2 |

The revised prompt fixed the generic subject case without losing a previously
passing decision case. Both preserved explicit device references and conditional
relations. Both gave grounded, correct explanations for the calculation probe.
This measures bounded adherence to the clarified contract, not general accuracy.

Both failed the missing-attachment probe by inventing a placeholder request.
The revised prompt corrected its status to partial with missing information,
but that improvement is not counted as a complete pass. Both also disagreed
with the reference for a payment-confirmation question whose wording can mean
available proof or underlying payment state. That probe needs clearer caller
criteria; it is not counted as an unambiguous model failure or silently relabeled.

Both threshold rewrites preserved meaning but used `or higher`, which the
then-current guard did not recognize. The final general inclusive-comparison
rule was added after the fixed trial, tested across multiple values and both
directions, and applied to the unchanged saved responses. The improved replay
scores are validator evidence, not additional prompt gains. No prompts were
retuned after observing the trial.

One sample per case, six decision cases, and an ambiguous probe cannot establish
statistical improvement, full-recipe quality, financial accuracy or readiness
for unattended bulk generation. Remaining semantic failures stay rejected.

## Deterministic verification and provenance

- Full offline suite: **453 passed, 7 native integration tests deselected**.
- Strict mypy: 52 source files; Ruff lint passed.
- All 23 generated schemas unchanged; documentation checks passed for 24
  guide/skill references and 32 CLI examples; tracked-data audit passed.
- Newly added test module formatted. Existing formatting drift in other touched
  files was not swept into this change.
- Regression coverage includes supported/rejected negation transformations,
  percentage removal versus equivalence, strict/inclusive boundary changes,
  empty partial answers, no-op solver-call avoidance and persisted derivatives.
- Replaying the earlier frozen model rewrites: Sol 4/4 passes, Luna 4/4 passes,
  Terra 0/4 passes; every unchanged Terra output gets
  `rewrite-no-wording-change` before solving. No earlier artifact was modified.
- Seed recipe v8, rewrite prompt v5, solver prompt v6 and guard revision v7
  create new run identities; existing caches are never relabeled or reused as
  results of the new recipe. Specs, user guide and model skill reference aligned.

External private evidence is under
`~/.local/share/foliqant/checks/generic-prompt-validation-2026-09-20/`:
`before/`, `paired-manifest.json`, 16 immutable response records,
`paired-review.json`, `guard-replay.json`, `prior-rewrite-replay.json` and
`verification.json`. The paired manifest SHA256 is
`7e15972e94348c9088b46d7482c2c42468b9ec4c82f38203e754b8fb1dc807ed`.
No full generation, model training, native MLX lifecycle, paid API call, commit
or push was performed for this refinement.

# Spec and skill alignment

Reviewed the working tree based on `3cc1ec4` against public runtime exports,
deployment/step/envelope contracts, bootstrap, evaluation dataclasses/runner,
handler effect checks, model CLI and existing tests. This is an alignment record,
not a new product design or model-quality approval.

Corrected root/model project ownership, runtime snake_case versus existing model
camelCase boundaries, explicit migration support, private-network curation opt-in,
stale generation/test references and broken acceptance anchors. Added package
evaluation APIs/defaults, reuse ownership and E2E evidence. CLI configuration now
uses the implemented current-directory default with an explicit override.

The scope index, contributor rules and plans index separate current behavior
from historical proposals. Business-process IDs remain for evidence integrity
but are explicitly inactive. No persistence, workers, child jobs, application
authentication or packaged transport is implied. Existing explicit user
authorization remains valid; historical action records alone grant none.

The model skill entry and data reference now route to focused public procedures
instead of repeating run history and experimental settings. Both skills include
trigger/near-miss scenarios and concrete missing-input boundaries. No endpoint,
training or data operation was performed for this review.

Verification:

- `check_specs.mjs specs`: passed; structured spec file references also resolve.
- `check_skill.mjs` for both skills: passed. The model reference's public-guide
  links produce advisory progressive-disclosure warnings; they intentionally
  avoid copying procedures and do not require nested skill references.
- `plugin-eval analyze` for both skills: static analysis passed with no failing
  or warning checks. No observed usage or behavioral model benchmark was supplied;
  authored evaluation prompts are not claimed execution results.
- Model-project `scripts/check_docs.py`: passed, including both CLI surfaces.
- `scripts/check_tracked_data.py` and `git diff --check`: passed.

Remaining boundary: live model quality, full-recipe curation coverage and native
training are established only by their own recorded runs. These checks do not
establish new production or statistical reliability claims.

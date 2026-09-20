# Model lifecycle specifications

Status: the complete local lifecycle is implemented and has native execution evidence, including shared/customer training, QLoRA, held-out evaluation, policy/audit and independent exported-model inference. Final review repairs and packaging checks are recorded in `plans/`; no formal human digest approval or financial production qualification is asserted. This directory owns implementation intent. `research/` records background, not additional requirements. User documentation belongs in `docs/` and explains the working product without implementation history.

Read in order: [scope](00-vision.md), [lifecycle](01-model-lifecycle.md), [contracts](03-contracts/model-contracts.md), [operations](04-operations/security-release.md), [documentation and skills](05-documentation-and-skills.md), and [pinned backend](06-backend-and-dependencies.md), and [local setup](07-local-setup.md). The capability inventory and traceability register bind requirements to acceptance evidence. Later reviews must test behavior, not merely count files.

Active extension: [automated local dataset curation](08-automated-data-curation.md), requested after the original model-tooling acceptance. Its completion is tracked separately and must include real local generation evidence.

Accepted bounded quality repair: [native decision-data generation](09-native-decision-data.md)
turns the answerability research into a bounded local data contract. The final
verification establishes corrected generation, publication, artifact membership
and immutable resume for its sampled research recipe. Earlier audited rows remain
diagnostic and are not promoted. This acceptance does not establish full-recipe
coverage, model quality, training readiness, native MLX training, formal human
approval or production fitness.

Research basis: [input answerability and evidence-backed decisions](research/input-answerability-and-reliability.md) refines typed questions, complete request handling, explanations, and trustworthy numerical estimates. Specification 09 adopts only its data-generation slice; calibrated estimators, runtime decision APIs and production qualification remain research and are not implemented lifecycle claims.

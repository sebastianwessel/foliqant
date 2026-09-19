# Model lifecycle specifications

Status: the complete local lifecycle is implemented and has native execution evidence, including shared/customer training, QLoRA, held-out evaluation, policy/audit and independent exported-model inference. Final review repairs and packaging checks are recorded in `plans/`; no formal human digest approval or financial production qualification is asserted. This directory owns implementation intent. `research/` records background, not additional requirements. User documentation belongs in `docs/` and explains the working product without implementation history.

Read in order: [scope](00-vision.md), [lifecycle](01-model-lifecycle.md), [contracts](03-contracts/model-contracts.md), [operations](04-operations/security-release.md), [documentation and skills](05-documentation-and-skills.md), and [pinned backend](06-backend-and-dependencies.md), and [local setup](07-local-setup.md). The capability inventory and traceability register bind requirements to acceptance evidence. Later reviews must test behavior, not merely count files.

Active extension: [automated local dataset curation](08-automated-data-curation.md), requested after the original model-tooling acceptance. Its completion is tracked separately and must include real local generation evidence.

# Specification authority

These specs describe two implemented products. Start with the relevant row;
do not treat every historical document as an additional requirement.

| Scope | Behavior authority | Implementation | Evidence |
| --- | --- | --- | --- |
| In-memory Python library, compiler, model/MCP clients and evaluation | [Python package](11-python-package.md) | `src/foliqant/` | [Composition status](../plans/workflow-refactor-status.md), `tests/` |
| Local model setup, training, evaluation, calibration and export | [Lifecycle](01-model-lifecycle.md), [contracts](03-contracts/model-contracts.md), [setup](07-local-setup.md), [backend](06-backend-and-dependencies.md) | `model/src/foliqant_model/` | [Lifecycle status](../plans/implementation-status.md), `model/tests/` |
| Curation and native decision data | [Curation](08-automated-data-curation.md), [native data](09-native-decision-data.md) | `model/src/foliqant_model/curation/`, shared `foliqant.decisions` | [Curation status](../plans/curation-status.md) |

[Repository ownership](00-file-structure.md), [conventions](00-conventions.md),
[operations](04-operations/security-release.md) and
[documentation/skills](05-documentation-and-skills.md) apply within those scopes.
The registries link capabilities, representations and acceptance tests; they do
not grant permission to add features. Generated schemas derive from the typed
implementation. Runtime configuration, decision output, evaluation datasets and
reports are unversioned closed formats. Content revisions and fingerprints retain
reproducibility; native model-data format versions remain separate. CLI help defines callable options. End-user instructions live
in [the documentation site](../docs/index.md).

## Resolving drift

The current user instruction controls the requested change. Compare the active
spec with types, validators, tests and current behavior before editing. Fix stale
prose when implementation matches the accepted scope. For a semantic conflict,
state the conflicting evidence and resolve the decision explicitly; never add a
fallback or expand scope to make the documents appear consistent.

Private reversible details inside an authorized task can be decided locally.
New public behavior, data semantics, integration permissions and irreversible
operations need authority from that task or an explicit decision. Explicit user authorization remains valid within its scope until revoked;
historical execution records alone do not grant new permission.

## Non-normative material

[Business-process concepts](10-business-decisions-and-processes.md) and all of
`research/` are background, not active implementation work. Category catalog
normalization is already implemented; proposed evidence/thread/extraction and
durable parent/child process designs are not. Specification 11 supersedes older
service/durability proposals for the library. Persistence, queues, background
jobs, application authentication and packaged ingress are out of scope, not
pending package features.

`plans/` preserves dated execution and review evidence. A passing historical
pilot establishes only its recorded scope, never current full-recipe coverage,
financial accuracy or production qualification. The spec manifest records file
integrity, not human approval. No additional formal approval workflow is implied
by updating these implementation-aligned specs.

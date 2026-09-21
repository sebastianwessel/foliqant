# Capability inventory

## Workflow service implementation

Specification [11](../11-workflow-service.md) owns the in-memory service scope,
entrypoints, contracts and acceptance families. Persistence, queue workers,
application authentication and packaged transports are explicitly not capabilities.

- `CAP-SERVICE-CONTRACTS` → `ACCEPT-SERVICE-CONTRACTS`.
- `CAP-SERVICE-COMPILER` → `ACCEPT-SERVICE-COMPILER`.
- `CAP-SERVICE-RUNTIME` → `ACCEPT-SERVICE-RUNTIME`.
- `CAP-SERVICE-MCP` → `ACCEPT-SERVICE-MCP`.
- `CAP-SERVICE-PRIVACY` → `ACCEPT-SERVICE-PRIVACY`.
- `CAP-SERVICE-DX` → `ACCEPT-SERVICE-DX`.

## Proposed business-process extension

These are unimplemented target capabilities owned by
[specification 10](../10-business-decisions-and-processes.md), not new CLI/API promises.

| ID | Target | Planned verification |
| --- | --- | --- |
| CAP-BUSINESS-DECISIONS | Existing typed decisions plus per-answer evidence, thread snapshots and bounded extraction | ACCEPT-BUSINESS-DECISIONS; semantic, Unicode, temporal and omission cases |
| CAP-PROCESS-BRANCHING | One process with bounded configured child tasks and an explicit join | ACCEPT-PROCESS-BRANCHING; failure, ordering, authorization, retry and correction cases |
| CAP-DOMAIN-DATA | Evidence-backed candidate datasets for banking/funds, insurance and public administration | ACCEPT-DOMAIN-DATA; primary-source fit, annotation provenance and rights review; no acquisition |

## Implemented model lifecycle scope

The actor for every command is the local authorized operator or an agent acting for that operator. The entrypoint is the installed `foliqant-model` console script. Local filesystem access and explicit config are prerequisites; remote access is permitted only for explicit fetch, setup and curate acquisition; curate generation is loopback-only. The lifecycle and operations specs define common validation, logging, permissions, failure codes and atomic finalization.

| ID | Capability | Entrypoint | Success | Failure/recovery | Verification |
|---|---|---|---|---|---|
| CAP-CURATE | Automatically prepare and augment research data | curate / scripts/curate-data | pinned corpus, frozen partitions, checked local generation, separate diagnostic regression corpus | quarantine invalid candidates; fail and resume on network/integrity interruption | curation source/endpoint/generation/runner tests plus real local evidence |
| CAP-NATIVE-DATA | Generate native typed decision data without manual labeling | scripts/generate-data / curate with decisionData | separate auxiliary source corpus and diagnostic seeds; eligible native dataset contains held-out diagnostics plus only accepted verified projections or accepted rewrites with required parents, content-family isolation and coverage/diversity report | preparation remains resumable; invalid or ungrounded references quarantine; unattempted/quarantined train parents are excluded; zero acceptance publishes no native decision dataset; required coverage shortage is OUTPUT_INVALID/nonzero with diagnostic state retained | native contract/oracle/projection/isolation/publication tests plus independent inspection of a new bounded local pilot; prior runs are regression evidence only |
| CAP-SETUP | Prepare local assets in one command | setup / scripts/setup-model | pinned downloads, immutable model/data artifacts and receipt | offline miss, corrupt cache, unignored Git workspace, unsafe paths and locks fail explicitly | setup/acquisition/conversion tests and real offline rerun |
| CAP-ENV | Install and diagnose | doctor/schema | environment report or deterministic schemas | no backend import for help; unsupported training host reported | installation and CLI tests |
| CAP-DATA | Prepare rights-declared data | prepare | immutable grouped partitions | invalid rights/schema/leakage rejected before backend; fix input/new output | data tests |
| CAP-MODEL | Acquire and quantize | fetch/quantize | pinned verified model artifact | download/architecture/integrity failure; explicit retry/new output | artifact/backend tests |
| CAP-TRAIN | Shared and customer adaptation | train/customize | real loadable adapter with exact lineage | parent mismatch, overflow, cancellation, NaN; explicit warm start only | training and real lifecycle tests |
| CAP-EVAL | Measure real predictions | evaluate | profile-bound per-row/aggregate report | model/lineage/schema failures distinguished; no reference substitution | evaluation tests |
| CAP-RISK | Select and audit acceptance | calibrate/audit | fixed threshold plus separate test bound | unavailable score/profile mismatch/insufficient samples explicit | statistical and policy tests |
| CAP-EXPORT | Merge and export | merge/export | normal checkpoint/GGUF with provenance | unsupported conversion rejects; inputs preserved | export and independent-runtime tests |
| CAP-VERIFY | Artifact verification | verify | inventory/lineage validated | corrupt/missing/extra/unsafe files fail | adversarial integrity tests |
| CAP-GUIDES | Human and agent usage | docs/skills | novice completes real example; agent uses exact commands | drift checks fail broken references; independent content review | documentation and skill audit |

Admin/support capability is local doctor/verify plus documented failed-workspace inspection; there is no separate admin API. API/SDK scope is typed internal Python interfaces and CLI only, not a hosted API promise. Integrations are HF download, MLX subprocess/library and independent standard inference export. Jobs are finite foreground local processes, not a queue/daemon. Frontend/webhook/service capabilities are explicitly N/A to this scope.

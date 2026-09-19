# Capability inventory

The actor for every command is the local authorized operator or an agent acting for that operator. The entrypoint is the installed `foliqant-model` console script. Local filesystem access and explicit config are prerequisites; remote access is permitted only for explicit fetch, setup and curate acquisition; curate generation is loopback-only. The lifecycle and operations specs define common validation, logging, permissions, failure codes and atomic finalization.

| ID | Capability | Entrypoint | Success | Failure/recovery | Verification |
|---|---|---|---|---|---|
| CAP-CURATE | Automatically prepare and augment research data | curate / scripts/curate-data | pinned corpus, frozen partitions, checked local generation, separate diagnostic regression corpus | quarantine invalid candidates; fail and resume on network/integrity interruption | curation source/endpoint/generation/runner tests plus real local evidence |
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

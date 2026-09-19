# Operations, integrity and release

## Local execution and recovery

This is a local CLI, not a multitenant service. The OS user owns authorization. Private data/model/run artifacts use directories mode 0700 and files mode 0600 on POSIX. No external telemetry by default. Offline commands do not access the network. Disable MLX/HF background uploads and remote model code. Inherit token credentials only for explicit fetch; never print them or dump the full environment.

Create outputs in a sibling private staging directory after validating inputs; finalize with atomic rename only after all outputs and hashes pass validation. Reserve the destination using an exclusive sibling lock; never overwrite a concurrent writer. Model training has a separate run workspace that records state/logs while the final artifact remains absent. SIGINT/SIGTERM/timeout terminates the whole child process group, records interrupted/failed status, and leaves inspectable partial checkpoints outside completed artifacts. No automatic crash retry, stale-lock deletion, or process kill by PID-only lookup. Tell the operator the workspace/lock and recovery procedure. Remove a lock only when the owning invocation exits normally through cleanup; crash leftovers require explicit operator inspection/removal.

Disk-full, backend failures, nonfinite loss, missing adapter, and artifact validation failures never yield a completed artifact. Warm starts are explicit new runs. Completed artifacts are immutable by convention and hash verification, not filesystem/WORM guarantees. Do not silently repair corruption. Operators restore from trusted backups or rebuild; preserve earlier releases for rollback. A failed build does not change a deployment pointer.

Routine logs contain phase, counters, versions, error code and local output location. Private backend logs/predictions may contain source material and must be stored privately, never streamed unredacted to public CI. Do not echo invalid record content in schema errors. Record source identifiers and field paths instead. Raw data is not committed. File inventories reject unsafe paths, symlinks, duplicate entries and unexpected files; verify before and after backend consumption when producing derived artifacts to detect changes.

Training-rights fields are operator attestations, not legal determinations. Propagate all ancestor/dataset rights and attribution to descendants. If any source disallows redistribution, the release record says redistribution is disallowed; no automatic upload/publish command exists. No private/customer contribution enters a shared model without an explicitly authorized shared dataset. User deletion/withdrawal policy is manual: use lineage to find and withdraw affected descendants and retrain; deleting a source file is not machine unlearning.

## Resource bounds

Finite config/record sizes and maxRecords prevent accidental unbounded parsing. Backend timeout and token/sequence limits are enforced. Do not invoke sysctl or change macOS kernel/swap configuration. The pinned MLX trainer may use its normal per-process Metal wired-memory API at the device-recommended working-set limit; record this behavior and do not raise that limit. Never enable swap hacks, or silently offload to paid cloud resources. GPU memory exhaustion is a reported backend failure. Exact speed and model quality are measured. Concurrency is one training subprocess per invocation; local concurrent processes remain the operator's responsibility and doctor reports memory limitations without claiming reservation.

## Verification and release

Required automated checks: strict type checking, lint, unit/contract tests, CLI integration tests, deterministic schema generation/drift, file-link and documentation/skill reference audit, dependency lock verification and build/install test. Production code has no mock backend. Unit tests may isolate failures with controlled doubles; the final lifecycle test uses real model weights and training plus a separate standard inference implementation.

Required real acceptance record: exact source model revision and license, source/data hashes, dependency lock hash, all commands, real shared and customer adapter outputs, baseline/adapted evaluation, merge, export, independent inference output, elapsed time and available memory metrics, plus scoped limitations. Small diagnostic data is explicitly not a financial accuracy claim. Never require a tiny smoke model to pass invented financial quality thresholds to prove plumbing.

Release is a local built Python wheel and reproducible locked environment, not publishing to a registry. Include package metadata, exact dependencies and source attribution. Generate a dependency inventory; do not claim a vulnerability or license audit was run without command evidence. No signing keys, remote registries, paid cloud, model uploads, Docker daemons or account creation are required. Containers, hosted APIs, live auth, frontend accessibility, queue acknowledgment and service SLOs are N/A for this CLI scope; they remain requirements of the separate future workflow service.

## Research data and later commercial use

Private research is the current operating context. Dataset selection may include
research-only or non-commercial sources when their terms permit the actual use;
commercial eligibility is not a prerequisite for local experiments. Private
repository visibility is not permission to use otherwise prohibited data.
Unknown training rights, gated access and customer confidentiality still require
resolution before use; recording a license is not a substitute for permission.

Retain the source URL/revision, license identifier and evidence reference,
attribution, declared permissions, commercial-use assessment and restrictions.
Setup must retain downloaded license evidence beside external assets and pin its
hash in the committed acquisition manifest. For operator-supplied sources,
licenseEvidence points to retained terms or permission evidence; do not include
private agreements in Git. Model source licenses remain recorded with checkpoint
provenance and must also be considered in later commercial review.

Source rights travel with every dataset, adapter, merged checkpoint and export
through the existing immutable ancestry. A descendant containing a restricted or
unknown source cannot be described as commercially cleared. Shared models may be
private research derivatives; shared does not mean public or commercially licensed.
Keep a separate source selection for commercially permitted experiments so a
research mixture does not become an unnoticed dependency of a commercial release.

Before commercial use, review all ancestor model and dataset terms and obtain
needed permissions. A later license may not cover prior training or the resulting
weights; otherwise rebuild from an eligible ancestor without the affected data.
Deleting downloaded files does not remove their contribution from trained weights.
Do not rewrite old manifests to imply permissions existed at training time.

Primary guidance: [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
restricts commercial use and requires attribution; [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
permits commercial use under its conditions. Source-specific terms and other
rights still need review; these examples are not a universal license allowlist.

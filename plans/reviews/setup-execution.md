# Local setup execution evidence

Date: 2026-09-19. This is implementation evidence, not financial model quality or
full lifecycle acceptance. No model/data files are committed.

## Verified operations

- Native arm64 macOS, isolated CPython 3.12 environment and locked dependencies.
- Acquisition profile smoke-v1 digest
  `2638284b4ec91b0a2368e0a35266b7b0b20a1046678856474ef323fc9aa65594`.
- Downloaded all 14 pinned assets, exactly 273548800 bytes, into the external
  `~/.local/share/foliqant/setups/smoke-v1-2638284b4ec9/downloads` workspace.
  Every size and SHA256 matched. Acquisition plus conversion and offline reread
  took about 41.9 seconds on this host; this is not a throughput promise.
- Converted only official Banking77 train into disjoint 154-record shared and
  customer pools. Official test retained download-only.
- `./scripts/setup-model` installed the local package and created verified
  upstream/shared/customer artifacts, configs and setup receipt.
- `./scripts/setup-model --offline` returned success, zero downloaded files,
  fourteen reused files, with the same profile and artifact locations.
- First offline editable installation before build requirements had been cached
  correctly failed in uv; normal setup obtained the locked build requirement.
  Subsequent offline environment synchronization succeeded.
- Current focused root checks: 33 passed after workspace/FIFO fixes; strict mypy
  clean for setup/configuration/CLI. Earlier full suite: 90 passed, before new
  backend/review tests. Final combined suite remains a separate gate.
- New operating skill passed the standard skill frontmatter/scaffold validator.

## Independent review and repairs

Independent read-only reviewer reproduced four issues, despite passing original
tests: self-checksummed dataset metadata could contradict retained records;
explicit setup workspaces were not required to be Git-ignored; wrapper relative
paths were based on repository CWD; FIFO inputs could block before validation.

All four original findings were repaired and independently rechecked (43 focused
checks passed). Re-review found one additional regression: canonical retained
rows were incorrectly checked against the original input byte limit, so adding
an omitted default could invalidate otherwise valid input. That extra comparison
was removed; original ingestion limits remain enforced, with a new at-limit
omitted-default regression. A final combined run follows this last fix.

## Outstanding lifecycle acceptance

The complete public training/customization/evaluation/calibration/audit/merge/
export commands are not yet all wired. Real backend doctor, masking, bounded
generation and one-step training/quantization are separate backend evidence and
do not establish complete CLI lifecycle acceptance. Independent inference of
exported shared/customer models, full docs/skills, link/command audits, package
build/install verification and final requirement review remain outstanding.

## Latest broader checks

- Full non-integration suite before the final size-bound regression: 105 passed,
  three native tests deselected. Strict mypy: 23 source files clean. Ruff clean.
- Native explicit backend integration: three passed, seven non-integration tests
  deselected; this includes real Metal doctor, tensor loss-mask and one-step LoRA.
- Offline setup rerun passed after semantic dataset verification was added.
- `uv build --offline --wheel` succeeded. Wheel contained 29 files, including the
  pinned profile manifest and no JSONL/CSV/weight files. Installed-wheel isolation
  and complete lifecycle/export acceptance are still separate remaining checks.
- Draft spec structure audit and seven guide/skill link/command checks passed.


Final combined snapshot after the size-bound fix and parent runner were added:
115 offline tests passed, five native integration tests passed, strict mypy on
24 source modules passed, Ruff/schema/docs checks passed. Public verify accepted
the real shared dataset artifact
`5ecb6b9e2ff9ca446f773a3e2a27f6d39cdd1741a992c82dc55e2e814587e612`.
Additional runner hardening is subsequent work and must receive its own check.

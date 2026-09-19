# Final local implementation verification

Observed on 2026-09-19, native arm64 macOS, CPython 3.12.11.
This is implementation acceptance, not financial model qualification or legal clearance.

## Automated verification

| Check | Observed result |
|---|---|
| Default pytest suite | 187 passed, 7 native tests deselected (8.92 seconds) |
| Explicit native pytest suite | 7 passed, 179 deselected, 49.14 seconds |
| Strict mypy | 33 source files, no issues |
| Ruff | Source, tests and scripts pass |
| Generated schema drift | 15 files match |
| Documentation audit | 21 guide/skill references and 30 CLI examples pass |
| Operating skill validation | Pass |
| Tracked-data audit | No tracked datasets, weights or generated output roots |
| Specification structural audit | Pass |
| Built-wheel isolated installation | Schema export/drift, doctor without MLX, real artifact verification pass |

The native command used `FOLIQANT_TEST_MODEL=<setup>/downloads/model` and
`FOLIQANT_TEST_SETUP=<setup>` with `pytest model/tests -m integration`.
The completed setup was `smoke-v1-2638284b4ec9` in the external local workspace.
`model/tests/test_lifecycle_integration.py` exercises the public CLI, actual
training and tensor changes, warm starts, shared merge, quantization, customer
QLoRA, customer merge, both exports, calibration/test generation, policy/audit
and verification. It verifies exact ancestry and inherited commercial restrictions
in both customer exports. Each backend command has a bounded timeout.

The base wheel was installed by `uv run --isolated --no-project` outside the
checkout with locked requirements and offline cache. Its imported package path
was in the isolated uv environment, not the source checkout; MLX was absent.

- Root uv.lock SHA-256: `d9ddc238b8cb7de2a812880ff3d89487e0ac7eeef00847b98a1528bd3a213cc0`
- Built wheel SHA-256: `3f810aae7bd0412a48ff3680879aff82843d5290366b8e55e610b4b0a5ee4b24`
- MLX 0.32.2; MLX-LM 0.31.3; Transformers 5.17.0; Torch 2.14.0.

## Independent runtime evidence

Shared and customer checkpoint/GGUF exports were loaded outside MLX LM.
Final hardened scripts produced these customer evidence IDs beneath
`<setup>/acceptance/20260919/release-evidence/`:

| Evidence | ID | Observation |
|---|---|---|
| customer-transformers-hardened.json | 09fe761564114d33b9a8648cbbf426627491f16384ba3940eb4eb0bb96bea9e3 | Transformers 5.17.0 CPU, 16 generated tokens |
| customer-llama-hardened.json | db76b1d8c684bdc18653f96b9dbf3094445246197570cfe8b3512fe42e2ede6a | llama.cpp 10180, exit 0, bounded single-turn inference |

Reports remain separate from immutable export manifests. Their canonical IDs,
result hashes and mode 0600 were checked. The scripts reject outputs nested in
their input artifacts and reap process groups on timeout or SIGTERM.

## Review findings resolved

- Central safe copy and path-disjointness checks prevent artifact mutation,
  symlink/FIFO races, overwrite and unbounded copying of changed inputs.
- Quantize/merge retain the exact parent tokenizer, chat template and notices;
  architecture and inference configuration drift is rejected.
- Fetch validates metadata and expected model files before downloading weights.
- Prediction validation handles JSON null, root evidence pointers and strict
  schema/equality semantics; risk uses grouped trials and exact bounds.
- Schema generation uses exclusive creation; lineage counts unique ancestors.
- Runtime verifiers use explicit single-turn generation and process-group cleanup.
- Guides and agent skill cover all 14 commands, source rights and the actual
  setup/shared/customer/evaluation/export sequence.

Independent reviewers covered execution/contracts, orchestration and evaluation,
transformations, docs and public operations. Regression tests and the final native
replay cover the repaired implementation, not just earlier pre-review builds.

## Limits and retained evidence

See [lifecycle artifacts](lifecycle-execution.md), [setup](setup-execution.md),
[dependency qualification](dependency-qualification.md) and
[dependency license inventory](dependency-inventory.json).
The dependency inventory records observed metadata; no vulnerability scan or
legal clearance is claimed. The tiny model's baseline evaluation was 0/4 exact
on each diagnostic calibration/test sample; its policy abstained and its audit
was insufficient. This proves honest execution, not useful financial accuracy.
The workflow service, production checkpoint selection, cloud deployment and
commercial/regulatory qualification remain outside this completed tooling scope.
No data, weights, adapters or reports containing private records were committed
or uploaded. No commit or push was performed in this implementation phase.

The final customer QLoRA exports were also independently executed after all source repairs; see [the preserved measurements and runtime evidence](lifecycle-execution.md#final-reproducible-cli-acceptance). The clean wheel was installed from a content-specific local path to avoid reusing an older wheel installation.


Final required failure coverage also passes: wrong exact adapter parent and
customer ancestry, malformed/nonfinite adapter output, transitive grouping,
four-component splitting, and interrupted setup transfer without publication.
Evaluation rejects inherited cross-dataset overlap by record ID, group,
conversation and prompt through warm-start, merge, quantize and export before
generation. Policy tests cover wrong split, profile/dataset mismatch and an
unchanged threshold applied to worse test results. A forged overlapping split
is rejected by the manifest contract before publication; the later overlap
guard remains defense in depth. These last additions change tests only; the
final native replay and uniquely installed wheel contain the final production
source, including the nonblocking evaluation file-read repair.

# Lifecycle execution evidence

Execution date: 2026-09-19. Host: native arm64 macOS with MLX 0.32.2 and
MLX-LM 0.31.3 from the locked repository environment. This records local
acceptance evidence, not a release or quality claim.

The reusable setup root was
`~/.local/share/foliqant/setups/smoke-v1-2638284b4ec9`. Outputs are retained
under `acceptance/20260919`. Every artifact below was finalized through
`ArtifactTransaction` and passed standalone `load_verified_artifact` after
publication.

## Shared dense path and exports

The two-step shared adapter `26d231ca3a987963af988f90e32c2b226e98e99fcf9c7c990d67b1df4de31078`
was fused with its exact setup upstream parent. The resulting artifacts are:

| Output | Artifact ID | Observed evidence |
|---|---|---|
| `shared-merged` | `883a07027b0aa62d8d05c37795f28c692b1eda472367f3dd60c6ead4cea3cef2` | 272 BF16 tensors; fuse recorded non-lossy; 124 exposure rows |
| `shared-checkpoint` | `a29f0304db31ed88d2cb529d489f08b442f8def0944088282d6ac10b5390d6b2` | 272 tensors in one Safetensors file; `transformers` compatibility remains `unverified` |
| `shared-gguf` | `5f3fffec52da0e765034f6411370626b7a459cde855ef7efb00d49caeb8c31bb` | GGUF v3, 272 F16 tensors, 21 metadata keys; `llama.cpp` compatibility remains `unverified` |

The parent orchestration independently inspected saved Safetensors headers and
GGUF tensor metadata rather than accepting only child result counts.

## Customer path and exports

The public CLI ran the setup's two-step configuration against `shared-merged`
and the separate customer dataset with customer ID `smoke-customer`:

```text
foliqant-model customize --config <setup>/config/train.json --model <acceptance>/shared-merged --dataset <setup>/artifacts/customer --output <acceptance>/customer-adapter --customer smoke-customer
foliqant-model merge --model <acceptance>/shared-merged --adapter <acceptance>/customer-adapter --output <acceptance>/customer-merged --timeout-seconds 600
foliqant-model export --model <acceptance>/customer-merged --format checkpoint --output <acceptance>/customer-checkpoint --timeout-seconds 600
foliqant-model export --model <acceptance>/customer-merged --format gguf --output <acceptance>/customer-gguf --timeout-seconds 600
```

| Output | Artifact ID | Observed evidence |
|---|---|---|
| `customer-adapter` | `921876c38d97a06d64c020198145d1de665ae9a6adbb31586c6a326ce8aeb9e9` | LoRA; final loss 2.4640612602233887; 28 trainable tensors; customer scope retained |
| `customer-merged` | `88414524454ed0d0033a47f87a999aac0e14b015a59d4f99bb49f0cce152c978` | 272 BF16 tensors; two non-lossy fuse history entries; 248 exposure rows |
| `customer-checkpoint` | `7cca10ebc3ae2c5a214b85be3b44354f0a1bd2cf34ac4a7cfe8b418d32978526` | 272 tensors; `transformers` compatibility remains `unverified` |
| `customer-gguf` | `acaeb316af1940c1e5fedcf5a9886b29a4c8ca6fd8659edfef04febd2b5164f2` | GGUF v3, 272 F16 tensors, 21 metadata keys; `llama.cpp` compatibility remains `unverified` |

## Quantized shared path

The public CLI exercised QLoRA and lossy dequantization for fusion:

```text
foliqant-model train --config <setup>/config/train.json --model <acceptance>/upstream-4bit --dataset <setup>/artifacts/shared --output <acceptance>/shared-qlora-adapter
foliqant-model merge --model <acceptance>/upstream-4bit --adapter <acceptance>/shared-qlora-adapter --output <acceptance>/shared-qlora-merged --timeout-seconds 600
```

| Output | Artifact ID | Observed evidence |
|---|---|---|
| `upstream-4bit` | `41224517268b8f84cd6194309dda48f9beb87dc3d6ff3798d82024952bfe654d` | 4-bit affine, group size 64; 694 stored tensors; zero upstream exposure rows |
| `shared-qlora-adapter` | `646f97f22489b76dfca30a01daad8bd78b6b6b59d8034c83ab9dfcc4771e2df0` | QLoRA; final loss 4.326038837432861; 28 trainable tensors; 124 exposure rows |
| `shared-qlora-merged` | `84b96f51ced4878c6f4118945fc0e6252a4b77b4726e033a85da15d68e130a41` | precision history records 4-bit quantization, lossy dequantization to BF16, then non-lossy fusion |

The export compatibility records intentionally remain `unverified`. Independent
Transformers and llama.cpp smoke evidence must be recorded separately and bound
to these immutable artifact IDs; file conversion alone does not promote them.

## Final reproducible CLI acceptance

The final implementation replay passed all seven native tests in 49.14 seconds.
Its actual artifacts are retained outside Git at
`~/.local/share/foliqant/setups/smoke-v1-2638284b4ec9/acceptance/20260919/final-cli-lifecycle`.
The [measurement record](lifecycle-measurements.json) binds the package lock,
dataset content/file hashes, exact model/adapter parents, configurations,
observed training/validation loss, elapsed time, peak memory and evaluation/risk
results. It also retains baseline and adapted evaluation identities from the
earlier acceptance runs. It contains no prompts or dataset rows.

The final path was shared train -> warm start -> shared merge -> 4-bit quantize
-> customer QLoRA -> merge with the exact quantized parent -> checkpoint/GGUF.
It verifies scope, inherited rights, changed tensor files and held-out evaluation.

| Training | Final observed loss | MLX peak memory (bytes) | Reported training seconds |
|---|---:|---:|---:|
| Shared | 3.9482626914978027 | 822036082 | 0.2746033340226859 |
| Shared warm start | 3.4426186084747314 | 862160536 | 0.28589900000952184 |
| Customer QLoRA | 2.276196002960205 | 670713772 | 0.2920136658940464 |

These are tiny two-step worker measurements, not end-to-end process latency,
host total memory, larger-model capacity or financial quality benchmarks.

Both final customer exports also ran independently:

- Checkpoint artifact `3798a5152d7017dbeb8b9da1f6949ab84541de0f0e85d0af923d312e4f7fb41e`:
  Transformers 5.17.0 CPU produced 16 tokens, evidence
  `ca82872968d728b62f546934cfdf9ac080f1d3942a099f89b83e41fe02d3332d`.
- GGUF artifact `163856e05ebc1aaab5fc11d5bfdfc282902bb3a88f07282677a020a5bced6029`:
  llama.cpp 10180 exited 0, evidence
  `ce351a07d849ab9daf58e36d8da17f696afa0dcc7b40555eafed06c0b45d944f`.

The reports are `release-evidence/final-cli-transformers.json` and
`release-evidence/final-cli-llama.json` beside the retained acceptance directory.

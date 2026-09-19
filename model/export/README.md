# Serving artifact export

Future tooling will merge adapters where appropriate, export compatible checkpoints, convert/quantize for supported runtimes, and run parity/regression checks. Do not assume every architecture supports every exporter or quantization.

An artifact manifest should identify lineage, weights, tokenizer/template, schema/prompt profile, quantization, tested runtime versions, evaluation, and calibration provenance. Store large files outside Git. Export code must not become a required custom inference engine.

No export command is implemented yet.

See [local training and model lineage](../../docs/local-training-and-model-lineage.md) for Apple Silicon and sequential adaptation guidance.

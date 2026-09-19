# User documentation and agent skills

`docs/` contains end-user material only. Move research, proposals, implementation status reports, review findings and decision history to `specs/` or `plans/`. Do not tell readers about our refactor, corrections, previous designs, agents, ticket process, or a "storyline". Explain the supported product as it works. Do not advertise unimplemented commands.

Documentation order: overview and concepts; install/check hardware; one-command setup and first complete small local model run; prepare one's own data; build a shared model; customize a shared release; evaluate and select acceptance policy; merge/export and use standard inference; configuration reference; troubleshooting/security and reproducibility. Use clear English, short task-focused pages, explanations before commands, tables for choices and fields, nested lists only where hierarchy helps, Mermaid for lineage, and runnable command/code blocks. Tell users what a command produces and how to recognize success. Distinguish LoRA/QLoRA, a new shared derivative checkpoint, customer adapters, quantization and inference. Include M1/M5 64 GB guidance without fabricated benchmarks.

Every command/example must be checked against the built CLI. Examples use relative repository paths and standard installation, not copied dev packages, undeclared environment hacks or absolute author-machine paths. Pin the smoke model's exact revision and clearly label it a small toolchain exercise. User guides must contain complete actionable instructions and real output examples; simulated success or fake backends are prohibited.

Create `skills/foliqant-model/SKILL.md` plus concise references for data preparation, shared training, customization, evaluation, export and troubleshooting. It is a user skill, not internal process documentation: it must be usable without reading specs/plans. Explain triggers, prerequisites, safe command sequence, model-parent checks, dry validation/doctor, local artifact locations, interpretation of outputs, exact failure handling, and when human permission is needed for paid compute, uploads or private data. Do not embed secrets or environment-specific paths. Keep references progressive and avoid copying all documentation into the skill.

Update root AGENTS/CLAUDE and implementation guidance to use specs as intent and executable contracts as API authority. Include actual checks only. Add a documentation/skill drift audit that validates local links, referenced CLI commands/example paths and required lifecycle coverage. Static checks complement but do not replace executing the complete example and an independent content review.

The first-run guide starts with the setup command, explains uv as its prerequisite, and identifies the external local workspace containing downloaded datasets, prepared records, weights and subsequent outputs. Re-running setup verifies/reuses assets. Do not put training datasets or model binaries in examples or docs; examples contain recipes and preparation code only.

Explain private research versus commercial use in the data guide and agent skill.
Research/non-commercial datasets may be used when their terms permit the current
activity. Show how to record license evidence, attribution, commercialUse and
restrictions, and inspect inherited rights before commercial use. Never imply
private Git hosting waives terms or that a later license is guaranteed to cover
existing weights. Explain a separate commercially eligible data recipe and the
possibility of rebuilding from an unaffected ancestor.

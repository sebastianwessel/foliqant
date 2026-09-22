# Implementation specifications

These documents define the supported package. They are implementation contracts,
not a backlog or permission to add unrelated infrastructure.

- [Runtime](runtime.md): scope, architecture, authoring, execution and adapters.
- [Evaluation](evaluation.md): gold datasets, isolated/full runs, metrics and reports.
- [Prompt trust](prompt-trust.md): instructions, input boundaries and quality checks.

The current user request controls authorized changes. Verify these requirements
against public types, validators, CLI help and tests. Resolve inconsistencies in
all affected surfaces together: implementation, generated schemas, examples,
[public docs](../docs/index.md) and [runtime skill](../skills/foliqant/SKILL.md).
Schemas are generated from code; CLI help defines available commands. Content
fingerprints identify configurations and evaluation inputs, not format versions.

The repository provides one current format. Do not add migration loaders,
compatibility aliases, inferred business routes or hidden infrastructure.
Verification commands live in [the contributor guide](../.agent/IMPLEMENTATION.md).

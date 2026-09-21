# Workflow bundles

Each workflow directory contains a versioned process definition, prompt documents, and synthetic fixtures. Common data schemas live in `contracts/`. Endpoints, credentials, HTTP routes, and Redis connections belong in deployment configuration instead.

`financial-triage/` is an illustrative proposal, not a runnable process. Relative paths resolve from the file that declares them within an approved bundle/project root. Pin an immutable bundle revision for each future run.

The current [modular service proposal](../specs/research/modular-workflow-service.md)
recommends named Markdown steps with YAML frontmatter and an explicit workflow
entry point. Existing examples preserve the earlier illustrative YAML syntax;
they are not implementations of the proposed compiler or service CLI.

---
hide:
  - toc
---

<p class="docs-eyebrow">Tutorials · The learning path</p>

# Build workflows in six stages

This learning path adds one capability at a time. Each stage starts from a
business boundary, shows the files that encode it, runs offline by default, and
adds reviewed English and German gold. Work through the stages in order if you
are new to the runtime.

New here? [Install the runtime](../getting-started/runtime.md) first. Each tutorial
links its runnable example and explains the capability it adds.

Read [Configuration and folder layout](../configuration/index.md) before editing
the examples. The [step guides](../steps/index.md) explain each configuration
in depth; [ground-truth authoring](../evaluation/ground-truth.md) explains how
to replace the small synthetic datasets with your own reviewed cases.

<div class="docs-grid" markdown>

<div class="docs-card" markdown>

<span class="docs-step">01 · DECISIONS</span>

## Classify one request

Make one evidence-backed decision and send uncertain results for review.

[Build your first decision →](decision-basics.md)

</div>
<div class="docs-card" markdown>

<span class="docs-step">02 · EXTRACTION</span>

## Add structured extraction

Select the context a step needs and extract a result that matches a schema.

[Add extraction →](structured-extraction.md)

</div>
<div class="docs-card" markdown>

<span class="docs-step">03 · ROUTING</span>

## Route between flows

Use exact deterministic rules to choose which flow runs next.

[Connect your flows →](multiflow-routing.md)

</div>
<div class="docs-card" markdown>

<span class="docs-step">04 · MCP</span>

## Call a read-only tool

Pass selected context into one declared MCP call with a bounded result.

[Add an MCP step →](read-only-mcp.md)

</div>
<div class="docs-card" markdown>

<span class="docs-step">05 · TOOL LOOPS</span>

## Let a model use a tool

Give a model a read-only tool while keeping its execution bounded.

[Build a tool loop →](model-tool-loop.md)

</div>
<div class="docs-card" markdown>

<span class="docs-step">06 · COMPOSITION</span>

## Process several requests

Plan and collect bounded work, then apply your use case's disposition policy.

[Handle multiple requests →](multi-request-processing.md)

</div>
</div>

## What each example includes

Every example uses `config/settings.yaml`, conventional workflow/flow/step
paths, and `evaluation/dataset.json`. Model-backed commands use a local
`FunctionModel` unless `--live` is explicit. Offline wiring checks establish
contracts and control flow; they do not establish model quality.

The examples directory also contains focused deep dives: decision question
forms and evidence, selected context passed to MCP, prompt-security cases, and a
thin HTTP host. Use those after the capability path rather than as prerequisites.

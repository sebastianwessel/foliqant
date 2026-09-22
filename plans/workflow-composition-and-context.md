# Workflows, sequential flows and explicit context

Status: proposal for review, not implemented or an approved execution ticket.
Inspected revision: `ffa1249`, 2026-09-22.
Authority: owner requests a plan for reusable workflows, simpler colocated
configuration, prompt placeholders and evidence-based context optimization.
The owner endorsed the layout/template direction and proposed separating an
overall workflow from sequential flows. The semantics below are recommendations
for review, not approval to implement an invented public API. Specification 11
remains current-behavior authority until changes are accepted and implemented.

## Objective and boundaries

Let a caller run one intake workflow. Its triage flow categorizes and prioritizes
a message, then the workflow deterministically selects a specialist flow. Keep
the package an asynchronous, in-memory library: await a result, no persistence, background
jobs, queue workers, transport framework, authentication or agent-selected graph.
No new dependency, native V2 training contract change, dataset generation or
model call is authorized by this planning document.

## What already exists

| Asset | Current public behavior | Reuse/change boundary |
| --- | --- | --- |
| `contracts/deployment.py`, `settings.py` | Register multiple workflow directories; compile all before I/O | Reuse registry, confined paths, schema checks and frozen revisions |
| `WorkflowApplication.run` / `run_step` in `bootstrap.py` | Select a named workflow or isolated step | Keep the top-level run concept; add explicit flow scope for execution/evaluation |
| `compiler/compiler.py` | Inline steps OR direct `steps/*.yaml`/`*.md`; explicit acyclic routes | Replace step-file discovery with explicit references; validate workflow flow-transition graphs |
| `core/bindings.py` | Named literal/pointer inputs, explicit optional defaults | Reuse for flow inputs, boundary routing, templates and structured decision sources |
| `core/runner.py`, `core/budget.py` | Top-level admission, per-run deadline/step limit, per-step attempt limits | Share root execution bounds across all flows; do not reset them |
| `adapters/models/executor.py` | Separate instructions and JSON input; fresh conversation per step | Preserve default and within-step tool history; optional user-message template |
| `adapters/decisions/native.py` | Nonempty text sources; validated results reordered by question ID | Add explicit JSON source rendering, retain original/source distinction |
| `adapters/mcp/catalog.py` | Schema validation and configurable output byte bounds | Reuse; do not create a second truncation or result-limit system |
| `evaluation/` | Pipeline/step gold, saved full results, usage and comparisons | Extend for flow execution; no copied evaluator or hidden judge |

Paths above are relative to `src/foliqant/`. The CLI already supports
`run --workflow NAME`; an embedding application already uses
`await app.run("triage", envelope)`. Running a second workflow in host Python is
possible today, but those are separate top-level executions, not one process
with shared bounds and flow-level evaluation. Current workflow graphs can branch inside their steps;
there is no implemented flow abstraction yet.

## Proposed vocabulary and execution model

- A **workflow** is the complete process: its input/output, entry flow and
  deterministic transitions between flows.
- A **flow** is an ordered sequence of steps performing one coherent task. It
  has explicit input/output and can be evaluated independently.
- A **step** is one decision, LLM call, MCP call or registered handler operation.

A workflow is a graph of flows; a flow itself has no branching graph. Successful
steps advance in list order. At a successful flow boundary, the workflow selects
another flow or finishes. Uncertainty, failure and cancellation can stop a flow
before its final step; they must never wait for the end of the sequence.

This replaces the earlier nested-workflow-call/switch-step proposal. There is
no `workflow` operation inside a flow and no return/call stack. A one-flow
workflow is valid; multiple top-level workflows remain available by name.

Not every current workflow can simply be renamed to a flow: current graphs with
branches must be split into sequences joined by workflow-level transitions.
Single-step flows are allowed when a branch boundary requires them; do not split
all steps into individual flows mechanically.

## P01 — Explicit files and colocated step assets

Recommend this layout for a multi-flow example:

```text
config/
  foliqant.yaml
  workflow/
    workflow.yaml
    triage/
      flow.yaml
      assess.md
    billing/
      flow.yaml
      extract/
        step.md
        output.schema.json
```

A small flow can instead be inline or one explicitly referenced file such as
`triage.yaml`; a small step can also be inline. `flows/`, `steps/` and `schemas/`
are optional organizational folders, not discovery mechanisms. Additional
workflow directories become siblings only when needed. Configuration filenames
remain arbitrary with `--config`; fix errors that hard-code `foliqant.yaml`.
Keep Python `__init__.py` package markers and exports.

The workflow explicitly declares named flow instances and their definition
references. A flow explicitly declares its ordered steps, each with a stable
step ID and either an inline definition or an explicit `.yaml`/`.md` reference.
Use an ordered list for execution, not filenames or accidental map ordering.
Moving a definition file must not rename the step or flow instance. One file
contains one referenced definition; no recursive fragment/include language.
Freeze the smallest unambiguous list/reference syntax in phase 1 rather than
letting implementation agents invent competing forms.

Paths resolve relative to the declaring file, including schemas in external
steps. JSON Schema `$ref` stays relative to its schema resource. Step/schema
references, including shared-schema `../` paths and symlinks, remain inside their
flow bundle. Workflow-to-flow and deployment-to-workflow references remain
inside the configuration directory. This permits an explicitly referenced shared
flow definition to be reused by several workflows without a separate registry
or allowing arbitrary filesystem access.
Read/freeze exact dependency bytes once for revision hashing. Duplicate IDs,
conflicting instruction sources, unknown fields and invalid paths fail offline.

This replaces old step discovery and intra-flow routes cleanly. Update all
shipped examples, scaffolding, tests and docs together, with no legacy loader.
Keep root `.env` loading explicit in examples and document config-local behavior
for installed applications. Rebase evaluation-only paths without reading gold
at startup. Document how one-flow setups stay small; no empty placeholder folders.

Acceptance: equivalent inline/referenced definitions behave identically; moving
a step folder with its schema works; unrelated YAML is not loaded; ambiguous IDs,
missing/escaping files and invalid order/references fail offline with locations.

## P02 — Selected context and small prompt templates

Continue passing named fields or complete results through existing bindings.
Do not automatically inject the full envelope or all step records. `/steps`
already exposes records, including skipped steps; it is not a completed-results
projection. Optional values require explicit defaults and preserve missing/null.

For decision sources, propose an optional `format: json` on a source binding;
the default remains nonempty text. JSON rendering accepts JSON values, uses
deterministic serialization, and does not change the native V2 task shape. The
renderer wraps it as that named source's text. Known type mismatches should fail
offline; runtime validates unresolved types. Preserve the entire prior assessment
when its answerability/reason is relevant. Keep original documents in separate
source entries; previous model assessments are derived claims, not independently
corroborating evidence. No extra inference is needed to convert values.

Add optional `prompt` to LLM steps only:

```yaml
input:
  message: {pointer: /payload/message}
  triage: {pointer: /payload/triage}
instructions: Explain the recommended next action using the supplied data.
prompt: |
  Message: {{ message }}
  Prior assessment: {{ triage }}
```

The template replaces the default JSON user message; do not append the same input
again. Instructions remain static, higher-priority guidance. Only exact declared
input names may be substituted. Strings render as text; other JSON values render
as deterministic JSON. Render once: inserted braces are data. Unknown names or
malformed placeholders fail offline; no expressions, attribute access, filters,
loops, environment expansion or executable template engine. Define/document one
literal-brace escape before freezing this syntax. Only referenced inputs appear
in the rendered user message. Decision steps keep their canonical question/task
message; templates cannot replace or omit that contract.

Acceptance: arrays/objects/null and EN/DE text render predictably; missing inputs
respect existing bindings; source text containing placeholders is not rerendered;
unknown variables fail before I/O; data never moves into system instructions;
default rendering is unchanged and rendered input is not duplicated.

## P03 — Workflow-level routing between sequential flows

An intake workflow first executes `triage`, then chooses `billing`,
`cancellation` or a review outcome from the returned category. Priority is passed
as data to the selected flow. Routing uses only authored targets; models cannot
invent a flow name or change the graph.

```text
workflow: intake
  triage flow: assess category + priority -> project public result
    billing_dispute -> billing flow -> finish
    cancellation    -> cancellation flow -> finish
    unresolved      -> needs_review
```

Use one grouped decision call for the example's category and priority, followed
by an existing registered deterministic handler to project validated answers by
question ID. No second inference or array-index routing is needed. Preserve full
assessments in the flow report. Missing/uncertain category OR priority stops for
review before successful dispatch; strength cannot override that gate.

At a successful flow boundary, configure exactly one static next-flow/terminal
outcome or an exact-match route based on an existing binding. Matching is over
strings, without expressions or coercion; require a default (the example uses
review). Null/unmatched strings take the default. Other types are invalid input.
Missing values fail binding resolution unless an explicit optional default was
authored. Record selected transitions for evaluation. There is no model call or
new switch step for routing.

A flow's steps advance sequentially only after success. `needs_review` stops the
remaining steps. By default this finishes the workflow as `needs_review`; an
explicit workflow-level `on_unresolved` may instead select a review-handling
flow. Successful category routing is never evaluated on that unresolved result.
Terminal disposition is explicit: do not silently convert review into completion.
Technical failures fail the workflow with the existing safe error; cancellation
propagates. Do not add automatic retries, technical-error recovery or skips.

Data and execution ownership:

- The workflow owns one execution context, deadline, root admission slot and
  visited-step budget. All flows share them; advancing to another flow does not
  reacquire top-level admission or reset counters. Count each executed operation
  against `max_steps`; also bound flow transitions and reject cyclic flow graphs
  offline. Every flow contains at least one step, so flow transitions cannot
  introduce an unbounded zero-operation loop.
- Each step retains existing model/tool attempt limits and provider admission.
  Reuse async adapters and cleanup; no background task or SDK message state is
  introduced by a flow boundary.
- Workflow-boundary bindings can access root `/payload` and prior named
  `/flows/<instance>/result` values. Explicit bindings construct the next flow's
  input. Inside a flow, `/payload` means its input and `/steps` contains only its
  own step records. Cross-flow data arrives through declared inputs, not ambient
  access to other flows' internal steps.
- Retain existing per-question allowed-source restrictions. A previous assessment
  is not treated as authoritative evidence merely because it was schema-valid.
- Preserve trusted caller identity/metadata and trace ancestry across all flows.
  Tool authorization must identify workflow, flow instance and local step, and
  enforce the selected flow's declared capabilities. No auth system is added.
- Reuse definition files under different explicit flow-instance IDs if needed;
  instance IDs identify observations. Reject cycles, but allow two distinct
  sequential instances of the same definition. No cross-flow ID collisions or
  transcript sharing.
- Proposed result model: keep workflow-level execution metadata/total usage and
  expose named flow reports, each with payload, status, local step records and
  measured usage. Reuse current step/usage/error types. Do not invent synthetic
  child `ExecutionResult` copies or duplicate input/output through a call stack.
  Freeze the exact public schema and binding/report mapping before implementation.
- Root totals count each actual model/tool call once. Flow totals are subtotals,
  never extra calls to add again. Workflow wall time is measured, not computed by
  summing inclusive parent and child durations. Retain unknown usage as unknown.
- Compile the full reachable graph, declared references, available bindings,
  known schema incompatibilities and transitive revisions before clients open.
  Runtime checks still validate data that cannot be proven statically.

Standalone flow/step evaluation must use the same executor with a resolved input
and finite limits. Keep `app.run(workflow_name, envelope)` for the whole process;
add explicit flow scope to isolated execution/evaluation APIs in phase 1. Do not
make isolated evaluation secretly execute earlier flows or bypass tool policy.

Acceptance: category/priority reaches the right specialist; unresolved values
stop early; no wrong branch is invoked; all flows share bounds; concurrency one
works; cancellation stops further steps; per-flow reports preserve reasons and
failures; definitions can be reused without state leakage; changed definitions
invalidate workflow revisions. Migrate existing branched examples into equivalent
workflow graphs without changing their business outcomes.

Trade-offs: a branch in the middle of a sequence requires splitting the flow at
that point. Work that needs arbitrary step graphs may become more verbose. This
first delivery does not support parallel multi-label fan-out/join, cyclic
processes, nested workflows or resumable human tasks. Do not claim two-intent
parallel processing is covered. Sequential specialist flows remain possible;
add a distinct fan-out boundary only if a later use case justifies it.

## P04 — Evaluate composition and optimize without changing meaning

Apply the findings and data-readiness gate in
[prompt-order/cache research](../research/prompt-order-cache-and-evaluation.md).
Quality and stability take precedence over latency and cost. Existing evidence
gold has only 10 development and 2 validation families; the latter has no limited
strength examples. Historical native datasets are not independently reviewed
runtime V3 gold. Expand and freeze family-separated, EN/DE business cases before
using prompt-layout comparisons to choose production defaults. The research
memo proposes an initial annotation budget, not a reliability guarantee.

Extend the existing evaluator with examples/gold for isolated triage, isolated
specialists and complete intake routing. Retain flow and step results in reports;
support assertions against both and avoid double-counting metrics/usage. Keep
gold outside deployment startup and generated reports outside Git. Include EN/DE,
ambiguous intent, conflicts, missing priority, wrong branch, flow failure and
resource/cancellation cases. Synthetic correctness is not production accuracy.

Default remains a fresh conversation per step with explicit selected context.
Within a tool-using step retain the SDK's valid assistant/tool sequence. An email
thread is source input, not the AI's conversation history. No history/summary
store, transcript concatenation or automatic model summarization is proposed.

Measure baseline before optimizing. Compare one change at a time:

1. Selected bound fields versus forwarding complete prior records.
2. Default JSON versus the optional user-message template, with equivalent data.
3. Stable instruction prefixes and duplicated business-instruction removal where
   semantics can be preserved (single-question calls currently repeat text).
4. Existing bounded tool results with smaller declared projections, not silent
   truncation of validated data.
5. An isolated experimental explicit-history challenger, not a public default.
   Preserve valid tool-call pairs/provider requirements and use equivalent
   source information. Distinguish its extra transcript content from task data.

Record field/case accuracy, unresolved/review correctness, reason/strength errors,
schema-valid coverage, input/output/reasoning tokens, cache hits when reported,
latency and failures. Keep errors in denominators. Distinguish warm/cold cache
effects and original inference timing from replay. Use repeated paired runs and
fresh family-separated validation; previous evidence-study failures are known
development evidence now. Freeze model/settings/prompts/schema identities and
run local requests sequentially, separate from generation. Stop after timeout
until backend state is known. No new live run starts merely from this plan.

Keep only repeatable gains without meaningful correctness regressions; no
unsupported percentage target. If history does not help, ship no history feature.
Prefix caching does not require persistent application conversation state and
does not remove output-generation cost. Check actual backend behavior rather
than assuming a reusable cache exists across different schemas/instructions.
The saved selected-baseline validation already reports 79.5% cached input tokens;
this is cache-use evidence, not a cold/warm performance comparison. Separate
unchanged-input cache reuse from prompt reordering and history reuse. Inspect
provider-transformed tools/schemas and actual breakpoint requirements. Test
fixed rules first and questions before dynamic state independently; preserve
instruction authority, source chronology, tool permissions and native artifacts.
Reject quality regressions even when cache metrics improve. Inconclusive
comparisons retain the baseline; lack of statistical significance is not proof
of equivalent quality. Provider-specific cache controls remain a measured-need
decision, not an automatic new configuration layer.

## Delivery order and completion gates

| Phase | Work | Required evidence |
| --- | --- | --- |
| 1 | Freeze the proposed contracts, template escape and flow/report semantics; update spec 11 and affected registries | Review of public behavior, reuse boundaries and acceptance cases; no silent new defaults |
| 2 | P01 authoring/files and P02 selected context/templates | Offline compiler/rendering tests; updated schemas, CLI init/help, examples, guides and skill |
| 3 | P03 sequential flows and boundary routing | Offline end-to-end routing, limits, identity, cancellation and flow-report tests |
| 4 | P04 example/evaluation coverage and bounded measurements | Private full reports, comparisons, honest failures and a keep/reject decision per optimization |
| 5 | Align and verify the release | Runtime/model regressions, type/lint, schema, docs/MkDocs, skill/spec and staged-data audits; reviewed commit, no push unless requested |

Each implementation phase must update its affected source, generated contracts,
tests, end-user docs and skills together. Native V2 model/training artifacts stay
unchanged; do not add conversions, queue services or compatibility branches.
Before execution, turn this reviewed proposal into bounded tickets with exact
acceptance criteria. Agents must not treat optional research challengers or
deferred fan-out as authorization to add product features.

## Research used for the context direction

- [PydanticAI instructions](https://pydantic.dev/docs/ai/core-concepts/agent/#instructions):
  current-agent guidance is distinct from retained system prompts/history.
- [PydanticAI message history](https://pydantic.dev/docs/ai/core-concepts/message-history/):
  history reuse is explicit and must preserve message/tool semantics.
- [vLLM prefix caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/):
  matching query prefixes can reuse input computation independently of application
  session storage. This is not evidence of a measured Splash optimization.

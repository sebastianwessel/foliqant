# Build with Claude or Codex

The repository skill at
[`skills/foliqant`](https://github.com/sebastianwessel/foliqant/blob/main/skills/foliqant/SKILL.md) helps an agent build and
review applications that use the installable `foliqant` package.

Use it for workflow, flow, and operation definitions; in-memory execution; model
and MCP adapters; decision contracts; schema validation; telemetry; evaluation;
and runnable examples. It keeps application-specific HTTP, authentication,
persistence, and queue handling outside the package core.

## Install the skill

With Node.js/npm available, run this from your **application's directory**:

```sh
npx skills add sebastianwessel/foliqant --skill foliqant
```

Select your coding agent when prompted. This installs the `foliqant` skill and
its bundled references for the current project. Choose either explicit command:

**Claude Code**

```sh
npx skills add sebastianwessel/foliqant --skill foliqant --agent claude-code
```

**Codex**

```sh
npx skills add sebastianwessel/foliqant --skill foliqant --agent codex
```

Add `--global` if you prefer a user-wide installation. Check installed skills
with `npx skills list`. These commands use
[Vercel's Skills CLI](https://github.com/vercel-labs/skills).

If the repository is private, your Git credentials must grant access. An
SSH-based installation can use your existing GitHub SSH setup:

```sh
npx skills add git@github.com:sebastianwessel/foliqant.git --skill foliqant
```

The skill supplies agent instructions, not Python dependencies or a model.
Complete [runtime setup](../getting-started/runtime.md) separately, or ask the
agent to follow it. Keep credentials in the configured environment, never in
your prompt. Review the skill before allowing an agent to act on your project.

## Open your application project

Open the same directory in Claude Code or Codex after installation. Start a new
conversation and explicitly ask it to use the `foliqant` skill. If the skill is
not visible, confirm the installer targeted the correct agent and project with
`npx skills list`, then start a fresh agent session.

The skill guides the coding agent; it is not an agent running inside your
workflow. Your finished application needs the Python package, configuration,
and selected adapters, not Claude Code, Codex, Node.js, or the Skills CLI.

The agent should start with the [configuration layout](../configuration/index.md),
then use [workflow](../configuration/workflows.md), [flow](../configuration/flows.md),
and [step](../steps/index.md) definitions to encode your process.

## Build a solution with the skill

Give the agent a small business brief: the incoming data, desired result,
category meanings, required fields, routing rules, and cases that require human
review. Include reviewed examples of both ordinary and ambiguous inputs. State
which model endpoint and external tools it may use, without sharing secrets.

| Provide | Why the agent needs it |
| --- | --- |
| Input and expected final output | Defines workflow boundaries and schemas |
| Category descriptions, exclusions, and overlap rules | Separates single-choice triage from multilabel tagging |
| Exact business routes and review policy | Keeps process decisions authored rather than invented |
| Tool descriptions and permitted operations | Defines MCP catalog and allowlists |
| Reviewed positive, ambiguous, and negative examples | Supplies ground truth independent of generated answers |
| Runtime and cost constraints | Bounds model requests, tools, deadlines, and concurrency |

For example, adapt this prompt to your process:

```text
Use the foliqant skill to build a request-intake application.

Input: a JSON object with a message containing an English or German email.
Classify one intent: billing or cancellation. Billing means a question about
an invoice or charge; cancellation means an active request to end a subscription.
If both apply, neither applies, or the information conflicts, return needs_review.

For billing, extract the invoice reference if present; leave it null if absent.
For cancellation, extract the requested end date if explicitly stated; otherwise
leave it null. Use a trusted async Python handler to prepare the final result.
Do not contact customers, change accounts, or add persistence or authentication.

First propose the workflow, flows, steps, schemas, and exact routes. Ask me about
missing business rules instead of inventing them. Then implement the agreed
configuration and an async Python entry point using the public lifecycle API.

Use the conventional config/ structure and environment references for the model.
Add offline tests and evaluation cases from the reviewed examples I provide.
Keep any additional synthetic cases clearly identified for my review.
Validate and explain the compiled plan. Do not call model endpoints until I ask.
Use the installed public APIs. Add only the dependencies needed by this process.
Finish with commands to run the application and evaluate it, plus any open gaps.
```

Work through three checkpoints:

1. **Review the process.** Confirm category boundaries, required fields, routes,
   and review outcomes before treating them as policy. Keep classification and
   extraction in model steps; put calculations and exact business rules in
   trusted handlers. Flows own transitions.
2. **Review the runnable application.** Expect `config/settings.yaml`, workflow,
   flow, and step files, output schemas, registered Python handlers, and an async
   entry point. Keep any optional HTTP adapter outside the core. The agent should
   show the compiled plan and passing offline tests.
3. **Measure with your data.** Put reviewed cases under `evaluation/`, check them
   offline, then explicitly authorize a live evaluation. Inspect wrong answers,
   review outcomes, reasons, and evidence strength before changing prompts or
   rules. Synthetic wiring tests do not establish model accuracy.

Use [Inputs, results, and errors](../reference/inputs-and-results.md) when
reviewing the generated integration. It explains which fields are for
deterministic routing and which are assessments for people to inspect.

From the generated application directory, the agent can check the result with:

```sh
foliqant validate
foliqant doctor
foliqant explain --workflow request_intake
foliqant evaluate --check
```

Replace `request_intake` with the actual workflow ID. The evaluation check
requires the reviewed dataset; it does not run inference. Keep live evaluation
and deployment as separate, deliberate steps.

## What the skill enforces

The skill treats the current package implementation and CLI help as the command
authority. It preserves the boundary between deterministic workflow policy and
untrusted model observations, and requires explicit expected results for
evaluation cases. Give the agent your workflow and independently reviewed gold;
it can add the optional dataset reference, write strict JSON cases and explicit
label catalogs, and run offline validation. It does not invent expected business
outcomes, score thresholds, or implicit judge calls. See the evaluation guide for
the difference between offline checks, saved-result replay, and model execution.

For the human review checklist, follow [configuration](../configuration/index.md),
[ground-truth authoring](../evaluation/ground-truth.md), and
[running evaluations](../evaluation/running.md). Keep the reviewed rules and
gold in the application project so future agent changes can be checked against
the same expectations.

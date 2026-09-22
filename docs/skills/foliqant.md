# Build with an AI agent

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
its bundled references for the current project. To select Codex explicitly:

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

## Build a solution with the skill

Give the agent a small business brief: the incoming data, desired result,
category meanings, required fields, routing rules, and cases that require human
review. Include reviewed examples of both ordinary and ambiguous inputs. State
which model endpoint and external tools it may use, without sharing secrets.

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

Start with [runtime setup](../getting-started/runtime.md), then continue with
[workflow authoring](../guides/build-workflows.md) and
[testing and evaluation](../guides/testing-and-evaluation.md).

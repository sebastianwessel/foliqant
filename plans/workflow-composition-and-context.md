# Workflow, context and evaluation delivery

The owner authorized the complete refactor and later clarified three requirements:
convention-based configuration, no runtime format versions or compatibility code,
and committed synthetic evaluation datasets in each example. The active behavior
contract is [specification 11](../specs/11-python-package.md); this record describes
the completed delivery, not another competing API definition.

## Product scope

A workflow owns the process and transitions. A flow is an ordered sequence of
steps; steps perform decisions, LLM work, read-only MCP calls or registered host
functions. Keep execution asynchronous and in memory. No persistence, queue
workers, application authentication, transport framework or agent-defined graph.
Model acquisition, data generation, training and fine-tuning are deferred.

Recommended project structure:

```text
config/
  settings.yaml
  intake/
    workflow.yaml
    triage/
      flow.yaml
      classify.step.md
      extract/
        step.md
        output.schema.json
evaluation/
  dataset.json
```

Convention lookup removes definition paths and redundant names when unambiguous.
It never derives step order, routes, source authority or tool permissions from
filenames. Explicit references and inline definitions remain customization
options through the same compiler, not alternate compatibility implementations.

Every step receives selected named inputs. Templates JSON-encode inserted values;
JSON decision sources preserve structure. Fresh conversations remain the default,
with valid tool-call history retained only inside a step. The process retains one
execution ID, deadline, step budget and admission slot across flow boundaries.

## Verification work

1. Validate inline and conventional configuration with the same contracts:
   ambiguous/missing files, path escapes, duplicate IDs, invalid graphs, unavailable
   bindings, type mismatches, prompt placeholders and local schema references.
2. Exercise whole workflows, isolated flows and isolated steps. Test early review,
   conditional routing, identity continuity, shared budgets, safe errors and
   cancellation. Never execute downstream work after malformed results.
3. Make every example runnable and evaluable from a clone. Public authored
   synthetic gold is canonical JSON inside each example; outputs and real data
   remain ignored. Gold is read only by an explicit evaluation invocation.
4. Align schemas, CLI, docs, specs and the runtime skill. The skill must map business
   requirements into the workflow/flow/step design and cover all configuration
   surfaces, customization, evaluation and verification without inventing features.
5. Run the full offline suite, packaging checks, strict types/lint, schema checks,
   documentation/site build and tracked-data audit. Review changes independently.
6. Run bounded sequential local-model comparisons, preserve private full results,
   resolve observed problems and record supported keep/reject decisions. Commit
   verified work; do not push without a request.

## Model evidence

Use the [cache research](../research/prompt-order-cache-and-evaluation.md) as
experimental background. Accuracy and stability outrank cost. Compare security
policy, question ordering and instruction ordering separately with unchanged
model, sampling, schemas, tools and inputs. Preserve originals and chronology;
previous model claims are not independent evidence.

Use clean/adversarial EN/DE pairs, legitimate customer imperatives, fake role/tag
boundaries, poisoned derived results, missing facts and contradictions. Retain
whole-case failures, review correctness, reasons/strength, usage and latency.
Repeated scopes and translations do not create independent families. Synthetic
fixtures support bounded regression evidence, not enterprise security or accuracy
certification. No automatic judge can replace independent ground truth.

Do not overlap inference. After timeout, stop new calls until backend state is
verified. Preserve actual cache counters as observed; missing is unknown. A warm
cache hit without a matched cold control does not establish a speedup. Do not
add persistent history, semantic caching, padding, broad tool access or a screening
model just to improve cache statistics. Inconclusive comparisons retain the
baseline. An experimental history challenger must stay outside the public product
unless its quality benefit is demonstrated.

See the [execution ledger](workflow-refactor-status.md) for the final evidence and
measured limitations. Focused tests alone were not used as completion evidence.

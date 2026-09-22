# Workflow composition and prompt verification

Date: 2026-09-22. Scope: the in-memory runtime refactor from `eecba17`, its
examples, configuration, documentation and skill. Model generation, training and
fine-tuning were left unchanged. This is implementation and measurement evidence,
not a production accuracy or security certification.

## Delivered behavior

- One workflow owns flow transitions; each flow runs an explicit ordered sequence
  of decision, LLM, read-only MCP or registered handler steps.
- Conventional `config/settings.yaml`, workflow/flow/step lookup, colocated
  schemas, selected data bindings and JSON-safe prompt placeholders replace
  redundant path configuration. Explicit paths and inline definitions remain
  customization through the same compiler.
- One root identity, deadline, admission slot and step budget span flow boundaries.
  Review stops downstream operations; cross-flow access is restricted to projected
  results. Whole-workflow, flow and step execution use the same runner/validators.
- Evaluation supports those three scopes, conventional gold lookup only when
  explicitly evaluating, full private reports, replay and comparison. Public
  authored synthetic gold lives inside each example; generated reports do not.
- Runtime configuration/results are unversioned closed formats. No compatibility
  loader or migration layer was added. Existing model artifacts remain separate.
- Fixed input-authority instructions, fresh conversations per step and local
  output/tool validation provide layered protections; they do not make model
  semantics deterministic.

The standalone runtime skill contains business decomposition guidance and the
configuration, provider, environment, tool, template, route and evaluation field
maps. An independent agent used it to create a new bilingual email-intake bundle
with classification, review, extraction and a selected-context follow-up flow.
Public `validate`, `explain` and `evaluate --check` passed for six suites and 24
scoped cases, without model calls. Those cases are a configuration forward-test,
not additional model-quality samples. Skill snippets were separately compiled or
validated against public contracts; a one-category choice snippet was corrected.

## Live design and results

All calls used the explicitly configured local Qwen3.8-27B-Splash compatible
endpoint, native structured output, low reasoning, temperature 0.1, max output
8192, provider/evaluator concurrency one, a 300-second request limit and a
610-second workflow limit. No endpoint discovery, cloud calls, overlapping
inference, model-data generation or training occurred. Loaded weight digests and
backend/tokenizer revisions were not independently verified.

The research runner froze source/configuration/gold bytes before each run and
retained full private reports and hash receipts under
`.foliqant/experiments/workflow-refactor/`. Its standalone commands and bounds are
in [the experiment guide](../../research/experiments/README.md). The first pilot
preceded a research snapshot-collector hardening; each full baseline/candidate
pair used identical source bytes and gold, differing only in its declared
renderer intervention. No gold was relabeled from model predictions.

| Run | Fully matching cases | Passed checks | Reported cached input | Median workflow latency |
| --- | ---: | ---: | ---: | ---: |
| `security-baseline-pilot` | 4/4 | 36/36 | 23.9% | 18.91s |
| `security-questions-first-pilot` | 4/4 | 36/36 | 93.8% | 8.72s |
| `security-baseline-full` | 20/20 | 172/172 | 92.8% | 9.53s |
| `security-questions-first-full` | 20/20 | 172/172 | 97.4% | 9.14s |
| `security-policy-control-pilot` | 4/4 | 36/36 | 50.3% | 12.12s |
| `security-common-first-pilot` | 4/4 | 36/36 | 23.9% | 18.30s |
| `evidence-baseline-full` | 10/15 | 421/437 | 64.3% | 24.06s |
| `evidence-questions-first-full` | 12/15 | 424/437 | 97.7% | 14.06s |

There were 134 model requests in total, with no timeout or transport failure.
Pilots overlap the full suites. The two full corpora contain 20 security inputs
and 15 decision-evidence inputs; repeated variants/scopes are not independent
samples. The security set has ten clean/attacked business families, five English
and five German. The smaller reserved validation set was not used to choose a
layout and remains unmeasured in this experiment.

The cached-input percentage is the provider-reported cached token count divided
by its reported input token count. There was no matched cache reset or randomized
run order. Output and internal reasoning token counts also differed. These data
show observed cache reuse and latency, not an isolated causal speedup or a
provider-independent cost guarantee. Internal reasoning text was not inspected.

## Keep/reject decisions

Keep the current production prompt order. Questions-first is promising: all
security checks remained equal, and broader complete-case matches improved from
10/15 to 12/15. However, its improved aggregate includes one mixed case with new
classification errors. The saved-report comparison records two improvements,
one mixed result and twelve unchanged cases. This evidence does not establish
baseline superiority, nor enough stability to silently change the default for
all consumers. Accuracy and semantic stability take priority over cache savings.

Keep fixed input-authority instructions, selected inputs and fresh step-local
conversations. The small policy-removal control also passed; it retains authored
business safeguards, so it neither proves the new policy unnecessary nor measures
its incremental security effect. The common-instructions-first pilot passed,
but four security cases are insufficient evidence to promote that alternative.
Do not add history reuse, padding, semantic caching, screening models or public
switches disabling the policy on this evidence.

## Semantic findings and limits

Independent AI-assisted review inspected public reasons and configured criteria,
not hidden model reasoning. It found the broader gold defensible:

- `two_requests`: baseline used the wrong issue for multiple positively supported
  choices. Questions-first corrected it.
- `suggested_purpose`: baseline converted missing dispute information into false.
  Questions-first corrected it.
- `tentative_document` and `_de`: the baseline mishandled the dispute predicate;
  one public reason explicitly contradicted its structured Boolean. Tentative
  document interpretations were also assigned excessive evidence strength.
- `tentative_document`: the candidate corrected the predicate but rejected a
  supported tentative communication purpose because no action was settled,
  conflicting with criteria that deliberately distinguish purpose from action.
- `mixed_tentative_document`: both layouts overstated strength when a whole
  conclusion depended on one weak material premise.

These are recurring model semantic failures, not invalid JSON or transport
failures. Do not weaken validators, erase failed cases, interpret strength as
calibrated confidence or claim the baseline is production-qualified. The runtime
can enforce structure, allowed capabilities and deterministic process transitions;
it cannot guarantee that a schema-valid model conclusion is factually correct.

The security public reasons and reference extractions were coherent on the tested
inputs. `missing_referent_attacked` never exposed its poisoned prior assessment to
an executed model step: this is isolation/review-gate evidence, not resisted model
injection. Some uncertainty/quotation cases explicitly signpost their ambiguity,
so lexical shortcuts remain possible. Statement examples are English-only and
address examples German-only. English public reasons on German input are allowed
by these authored instructions. None of these fixtures is customer-derived,
independently human-adjudicated gold or a representative held-out benchmark.

## Verification

After inference, seven identical case arrays were factored into shared JSON
files inside their examples, using the existing one-level case-reference format.
All seven expanded datasets, 26 suite fingerprints and eight recorded experiment
gold identities were preserved. This removes 9,578 duplicate lines; expectations
and model predictions were not edited. The shared loader and exact referenced-file
snapshot checks are covered by the final offline regression.

Final offline gate results are recorded in the linked
[execution ledger](../workflow-refactor-status.md). Full details and predictions
remain private; only source fixtures and this aggregate review are committed.

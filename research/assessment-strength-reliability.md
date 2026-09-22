# Assessment-strength reliability research

Checked: 2026-09-22. Status: non-normative research for a bounded local
experiment. No model calls, downloads, training, or implementation changes were
made for this review.

## Scope and fixed meaning

The current runtime output remains the existing short `reason` plus
`evidence_strength` value. This note does not propose citations, evidence spans,
new issue or outcome enums, confidence percentages, judge calls, ensembles, or
cloud inference.

`evidence_strength` assesses support for the **whole reported assessment**:
answerability, issues, and any substantive answer. Its values keep their current
meaning:

- `strong`: the supplied information decisively supports the whole assessment;
  this may include a justified indirect inference or clear grounds for
  abstention, such as an unresolved conflict or a clearly absent required fact;
- `limited`: the supplied information gives weaker support for a permissible
  interpretation satisfying the stated criteria; and
- `null`: no strength assessment was made. It is not the default for an absent
  answer, an unknown result, or abstention.

Strength is not model confidence, probability, correctness, urgency, or signal
severity. A severe signal can be weakly supported, while a conflict or missing
fact can strongly support an abstaining assessment. Evaluation must preserve
that independence.

## Primary-source findings

| Source | Grounded finding | Limit for this project |
| --- | --- | --- |
| [JSONSchemaBench](https://arxiv.org/abs/2501.10868) (Geng et al., 2025) | Constrained decoding masks structurally invalid continuations. The paper evaluates schema coverage/compliance separately from downstream semantic quality and finds substantial differences in supported JSON Schema features across engines. An engine accepting a schema is not proof that it implements the schema precisely. | The study covers Guidance, Outlines, llama.cpp, XGrammar, OpenAI, and Gemini, with Llama 3.x in the open-engine experiments. It does not test the local Qwen 27B through Splash. Schema compliance cannot establish that a reason or strength judgment is semantically correct. |
| [AbstentionBench](https://arxiv.org/abs/2506.09038) (Kirichenko et al., 2025) | Across 20 datasets, models struggle with unknown answers, false premises, stale information, subjectivity, underspecified context, and underspecified intent. A system prompt that describes when not to answer improves abstention, but the authors state that prompting does not solve reasoning about uncertainty. Reasoning models can express uncertainty in a trace and still give a definitive final answer. | Qwen2.5-32B-Instruct was evaluated and performed well on average but varied strongly by scenario. This does not establish behavior for the exact Qwen version, quantization, chat template, decoding settings, or Splash backend used locally. The paper's LLM-judge evaluation is not proposed here. |
| [Language Models can Exploit Cross-Task In-context Learning for Data-Scarce Novel Tasks](https://aclanthology.org/2024.acl-long.621/) (Chatterjee et al., 2024) | Cross-task demonstrations improved results on average for the studied models, but the effect depended strongly on the source task. The paper reports that greater task diversity could confuse models and that performance did not scale with more source-task examples. | The experiments use Llama-2 7B/13B and GPT-3.5, not the local Qwen/Splash configuration. They justify testing task mixing; they do not justify adopting a mixed exemplar bank by default. |
| [FLUKE](https://aclanthology.org/2026.findings-eacl.269/) (Otmakhova et al., 2026) | Systematic minimal variations expose task-dependent brittleness. In the reported experiments, natural fluent changes in syntax or style, especially negation, were often more damaging than corruption-style changes such as letter flips. Scale mainly helped with surface-level modifications. | The reported averages do not predict this application's failure rate. Perturbations must preserve the intended assessment unless a contrast is explicitly designed to change it, and all expected outputs must be authored independently of observed model behavior. |

## What structured output can and cannot establish

Native structured output is useful for keeping `evidence_strength` within
`limited`, `strong`, or `null`, bounding `reason`, and representing supported
answer/status combinations. It can reduce malformed responses when the backend
implements the relevant schema keywords correctly.

It does not establish that:

- the selected answer follows from the input;
- the answerability status and issue set are correct;
- the reason supports the whole assessment;
- `strong` and `limited` were applied according to their semantic rubric; or
- an abstention is justified.

Some relationships can be represented with JSON Schema branches, but
JSONSchemaBench shows that keyword support and true constraint coverage vary by
engine. The exact schema produced by the current adapter therefore needs an
offline conversion check and a bounded run against the actual local endpoint.
Independent Python validation remains necessary for represented invariants. It
still validates contracts rather than semantic truth.

## Bounded hypotheses for the local model

These are experiment candidates, not established improvements.

| ID | Change under test | Expected benefit | Main risk | Required comparison |
| --- | --- | --- | --- | --- |
| H1 | Keep one generic instruction that defines strength as support for the whole assessment, states that it is not confidence or severity, and explicitly permits strong justified abstention and decisive indirect inference. | Reduce answer/strength coupling and discourage confidence-style ratings without adding task-specific prompt branches. | A longer rubric can be ignored, copied mechanically, or reduce attention to the task criteria. | Current concise instruction versus the generic rubric on the same frozen cases and settings. |
| H2 | Add a small, balanced set of independently authored examples covering a strong substantive answer, a limited permissible interpretation, strong abstention, and `null` only when assessment is unavailable. | Clarify boundary cases in the existing output shape. | Example selection, order, and task mixture can bias the result; more examples may not help. | Zero-shot versus few-shot; fixed order permutations; task-local versus deliberately mixed examples. Do not select examples from final holdout families. |
| H3 | Submit the existing structured schema through Splash native output and retain independent Python validation. | Reduce syntactic and represented cross-field violations without another model call. | Splash may support only a subset of the submitted schema, or constraints may alter generation without improving semantic decisions. | Record schema acceptance, valid-response coverage, represented-invariant failures, semantic gold results, and backend diagnostics separately. |
| H4 | Evaluate human-authored meaning-preserving pairs and meaning-changing contrasts. | Reveal brittle behavior hidden by aggregate accuracy and distinguish invariance failures from missed semantic changes. | Poorly authored pairs can change unintended facts or encode the expected label through artifacts. | Pair-level consistency for invariances and targeted-change accuracy for contrasts, alongside ordinary field-level gold results. |
| H5 | Keep support strength and signal severity as separate evaluation dimensions. | Detect false `strong` ratings on dramatic but weakly supported signals and false `limited` ratings on well-supported abstentions. | Reviewers may still conflate operational impact with evidential support. | Cross-tab gold strength against scenario type/severity; inspect errors in each cell rather than treating scenario type as a strength proxy. |

## Prompt and exemplar constraints

The prompt should remain generic across decision kinds and defer substantive
meaning to the supplied question criteria. It should ask for a concise public
reason explaining the decisive support or limitation, without requesting a
confidence estimate or hidden reasoning. A useful variant can make the following
points explicit:

1. Judge the whole assessment, including answerability, every reported issue,
   and any answer.
2. Use `strong` for decisive supplied support, including valid indirect
   inference or clear support for abstention.
3. Use `limited` only for a weaker but still permissible interpretation; never
   invent an essential fact.
4. Use `null` only when strength was not assessed.
5. Judge support independently of urgency, severity, and whether the result is
   affirmative or abstaining.

Examples should use the production shape and avoid source-specific vocabulary
that can act as a label shortcut. Because the cross-task ICL results are
model-dependent, a mixed example set is a challenger, not the default. Example
count and order are experimental factors and must be frozen before evaluation on
unused families.

## Paired evaluation design

Use independently authored gold for every pair. Do not revise expected outputs
after observing Qwen predictions. Preserve the current result fields and compare
the full assessment rather than strength alone.

Meaning-preserving pairs should usually keep answer, answerability, issues,
reason meaning, and strength unchanged. Candidate variations include:

- polite or formal versus terse wording;
- controlled paraphrase and active/passive alternation;
- harmless formatting, whitespace, or HTML noise;
- benign spelling variation; and
- sentence reordering only when chronology and precedence are explicitly
  irrelevant.

Meaning-changing contrasts should alter the smallest relevant fact and have a
reviewed expected change. Prioritize:

- adding or removing negation;
- replacing a decisive date, amount, entity, or condition;
- adding or resolving incompatible information;
- removing or supplying a required fact;
- withdrawing or correcting one request; and
- adding a second independent intent while preserving the first.

Report at least:

- schema-valid and semantically usable coverage separately;
- field-level exact results for answer, answerability, issues, and strength;
- `strong`, `limited`, and `null` confusion counts, with special attention to
  false `strong` and abstention-strength errors;
- pair consistency for meaning-preserving variants;
- targeted-change accuracy for contrasts; and
- concise reason review against the whole gold assessment.

Repeated prompt attempts are not independent cases. Group translated,
paraphrased, and contrast-related records into families for splits and reporting.
Failures, truncations, invalid outputs, and endpoint rejections remain counted;
they must not disappear from semantic accuracy denominators without a separate
coverage report.

## Transfer boundary and stopping rule

Only AbstentionBench directly studies a nearby Qwen model, and that is
Qwen2.5-32B-Instruct rather than the current local model. None of the four
sources tests the complete local stack. Prompt, example, and schema changes are
therefore hypotheses until measured with the exact checkpoint, quantization,
chat template, reasoning setting, temperature, token limit, schema, and Splash
version.

Prefer the smallest variant that improves preregistered gold results and paired
robustness without materially reducing usable coverage. If gains disappear on
unused families, depend on example order, or trade semantic correctness for
format compliance, retain the simpler baseline and record the negative result.

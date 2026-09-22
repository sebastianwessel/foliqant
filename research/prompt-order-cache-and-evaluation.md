# Prompt order, provider caching and evaluation readiness

Research date: 2026-09-22. Inspected implementation: `b9f20b3`.
Status: research and proposed experiments, not implemented behavior or permission
to run inference. This investigation used repository inspection, saved artifacts
and primary sources; no model requests or dataset downloads.

## Recommendation

Optimize decision quality first, stable behavior second, latency and cost third.
Keep fresh conversations per step and selected, explicit context. Improve prompt
layout only after paired evaluation on broader, independently reviewed cases.
Do not add history storage, a semantic answer cache, another evaluator or a new
configuration surface merely to enable provider prefix caching.

Three different interventions must stay separate:

| Intervention | What changes | Required evidence |
| --- | --- | --- |
| Exact-prefix cache reuse | Reuses input computation for the same effective model input | Provider support, actual cache counters and unchanged request semantics |
| Reordering/shortening a prompt | Changes model input, even if the facts are equivalent | Accuracy, abstention, reason/strength and stability comparisons |
| Reusing prior AI conversation | Adds prior answers, assumptions and tool history | Separate quality experiment; caching alone is not justification |

Cache reuse generates a new answer; it is not response reuse and cannot guarantee
identical outputs across repeated calls. OpenAI explicitly makes this distinction.
Model version, sampling and execution can still affect results.
[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## What primary sources establish

| Source | Finding relevant to Foliqant | Limit |
| --- | --- | --- |
| [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching) | Stable prefixes, tool definitions and schemas affect reuse. Current GPT-5.6+ guidance supports explicit breakpoints; a shared prefix is not sufficient if only the changing final message is cached. | Breakpoints, minimum lengths, routing keys, write costs and retention differ by model/API generation. Do not implement one universal cache flag from older advice. |
| [Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Exact matching covers tools, system and messages in that order. Put explicit breakpoints at the last stable block. Tool/thinking settings can invalidate reuse. | Writes have costs and lifetime/minimum-length rules. Retain tool permissions even if that reduces hits. |
| [Gemini](https://ai.google.dev/gemini-api/docs/caching) | Implicit reuse benefits from common content first. Interactions supports implicit caching; explicit cache objects require generateContent. | Endpoint and model differences matter; this is research, not an implemented Foliqant adapter. |
| [vLLM](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/) | Prefix caching reuses KV computation and primarily saves prefill work. | It does not remove the cost of generating the answer. |
| [Splash development guide](https://github.com/incoai/splash/blob/main/DEVELOPMENT.md) | Documents exact-prefix reuse, separate tokenizer/grammar caches, and separate cold-prefill, cached-TTFT and decode measurements. | Upstream documentation does not establish our installed server revision or configuration. Do not enable experimental offloading or change KV precision for this experiment. |

There is also evidence that layout is a quality variable:

- [Lost in the Middle, TACL 2024](https://aclanthology.org/2024.tacl-1.9/)
  finds sensitivity to evidence position in long-context retrieval and QA.
- [Fantastically Ordered Prompts, ACL 2022](https://aclanthology.org/2022.acl-long.556/)
  finds few-shot example order matters and good orders do not transfer reliably
  between models.
- [LLMs Get Lost In Multi-Turn Conversation, 2025](https://arxiv.org/abs/2505.06120)
  studies simulated underspecified tasks and reports premature assumptions and
  difficulty recovering from earlier mistakes.
- [FLUKE, EACL 2026](https://aclanthology.org/2026.findings-eacl.269/)
  evaluates controlled linguistic variations with human validation, showing
  task-dependent brittleness including natural syntax and negation changes.

These motivate tests, not numerical predictions for Qwen/Splash or proof that
one layout is best. An email thread supplied as source material is not the same
as the AI's own multi-turn conversation in these studies.

## Repository and saved-result findings

Paths in this subsection are repository-relative.

| Current implementation | Consequence / candidate experiment |
| --- | --- |
| `src/foliqant/adapters/models/executor.py` creates a fresh Agent, supplies instructions separately, and does not pass cross-step `message_history` | Already supports independent requests with reusable prefixes. Keep valid within-step assistant/tool history. |
| `adapters/decisions/instructions.py` appends common output rules after business instructions | Test common rules first for reuse across compatible tasks; do not silently change instruction precedence. Repeated calls to the same step already share these instructions. |
| `decisions/contracts.py` declares `state` before `questions`; executor sends `model_dump_json` | Changing source text precedes reusable question criteria/catalogs. Test runtime rendering with questions before state without altering the native contract or historical artifacts. |
| Single-question compilation uses authored instructions both as business guidance and question prompt | Test removing duplication separately. Repetition might help quality; fewer tokens alone is not success. |
| Generic LLM input uses compact sorted-key JSON | Stable serialization is useful, but alphabetical ordering does not intentionally put static content first. Never reorder chronological messages or semantic lists mechanically. |
| `adapters/models/accounting.py` and `evaluation/summaries.py` already retain reported cache read/write and reasoning counters | Reuse these measurements. Missing counters remain unknown. No parallel accounting subsystem is needed. |
| `contracts/models.py` has explicit provider/sampling options but no public cache policy | Automatic backend caching can still work. Add narrow provider options only if a measured need and supported SDK/API justify them. |

The saved selected-baseline validation report
`.foliqant/experiments/assessment-strength/runs/20260922T084121.580276Z-5c717514/report.json`
contains four requests: **18,350 input tokens, 14,592 cache-read input tokens
(79.5%), 6,341 output tokens, including 4,959 reported reasoning tokens**.
Each request reports 3,648 cache-read tokens. Cache writes are unknown. These
are suite totals, not sums of both suite and step totals.

This is evidence of reported cache reuse already, not a measured caching speedup
or proof of which prompt segment was cached. There is no matched cold control.
The same suite passed 113/118 checks and 2/4 complete cases. Reasoning tokens are
a subset of output tokens; do not add them again. Reducing input prefill cannot
eliminate that output work. Keep the selected low reasoning/temperature settings
fixed in a layout experiment instead of mixing in another sampling change.

## Proposed prompt layout to test

Conceptual layout, not new configuration syntax:

```text
Provider-controlled schema/tool encoding (inspect the actual request/template)
Trusted instructions:
  fixed decision contract
  stable task rules and category definitions
User task:
  fixed question definitions, where safely separable
  original document/thread, preserving chronology and source IDs
  explicitly selected prior results, clearly identified as derived assessments
  optional short task reminder (separate challenger)
```

Keep original data in its lower-trust role. Do not move emails, caller strings or
tool output into system instructions to get hits. Prior AI conclusions are not
independent evidence; propagate unresolved status and keep allowed-source rules.
Never drop dates needed for deadlines, omit material facts, change tool access,
or weaken criteria to stabilize the prefix. Category descriptions, output schema
and tool schemas are part of the effective prompt, not just the visible text.

There is an important competing layout: **shared document first, task last**
could reuse a long document across different steps. It only works if everything
before the document, including instructions/tools/schema, matches. Different
step capabilities often prevent this. Test it as an alternative on genuinely
compatible tasks; do not expose all tools or use one oversized universal prompt
to manufacture a common prefix. A fixed task prefix is usually easier to reuse
across many documents, but that is a hypothesis about our workload.

Do not pad prompts to meet cache thresholds. Do not concatenate all prior
results or accumulate chat history for hit rate. A short uncached request can
be better than a bloated cached one. Keep task criteria before source content
as the first challenger, and test whether a brief final reminder improves
long-thread accuracy enough to justify its tokens.

## Evaluation data is the limiting factor

Offline inventory on the inspected revision:

| Asset | Actual coverage | Limitation |
| --- | --- | --- |
| `examples/decision_evidence/gold.py` development | 15 messages / 10 families; 13 EN, 2 DE; 75 assessments: 69 strong, 6 limited | Small authored development set. Two extra predicate controls reuse existing messages; they do not add families. |
| Same file, validation | 4 messages / 2 families; 2 EN, 2 DE; 20 assessments, all strong | No held-out limited-strength positives. Already-inspected failures are development evidence for future optimization. |
| Historical publication `hf-844c86503e82` | 846 train, 108 validation, 111 calibration, 180 test records | Explicitly marked historical-diagnostic, no human gold, not training-approved. Test has only one German record. Native V2 references cannot establish runtime V3 reason/strength quality. |
| Prepared projection `35c499b53423…` | 1,053 selected records / 877 families: 500 MultiDoGO, 500 typed-decisions, 53 TAT-QA; 623 train, 114 validation, 95 calibration, 221 test | Prepared candidates, not new verified gold. Related preparation artifacts must not be summed as independent corpora. |

Historical generation accepted 654 jobs and quarantined 211; accepted generation
is not independent ground truth. The larger source corpus and repeated example
checks do not solve the gold gap. Preserve frozen splits, family lineage and
rights. Do not relabel old artifacts or deterministically invent evidence-strength
ratings from confidence, answerability or teacher probabilities.

### Targeted expansion

Start with a proposed **400 independent business families**, 200 development and
200 untouched validation, balanced across original EN/DE cases within each split.
This is an initial annotation budget, not statistical certification. Split before
creating translations, paraphrases, quoted-thread variants or adversarial copies;
all relatives stay together. Native German examples matter beyond translations.
Keep a representative business slice separate from deliberately enriched hard
cases; do not present their combined average as a production error rate.

Coverage should include single-intent triage, true multi-label requests, blurry
category boundaries, insufficient information, contradictions, out-of-catalog
requests, priority/deadlines, and thread corrections/cancellations. Cross these
with short/long inputs, decisive evidence near beginning/middle/end, negation,
irrelevant quotations, and misleading prior-step outputs. Test configurable
category descriptions and hold out some taxonomies as well as message families.
Preserve thread chronology: position tests may move irrelevant surrounding text,
not reorder meaningful events while pretending the answer should stay the same.

For each task annotate value, answerability/issues, expected process destination,
and grounded rationale/strength under a written rubric. Exact rationale wording
is not gold. Have a second reviewer resolve material disagreement, especially
`limited` versus `strong`, incomplete sets and latest-intent interpretation.
Strong support for abstention remains valid. Keep unresolved annotation disputes
out of headline accuracy and report them separately. Model-generated candidates
can accelerate drafting later; the evaluated model must not certify its own gold.

Useful source material, subject to rights and split review:

| Source | Best use | Not supplied by the source |
| --- | --- | --- |
| [MultiDoGO](https://github.com/awslabs/multi-domain-goal-oriented-dialogues-dataset) | Finance/insurance conversation turns with intents, slots and explicit multi-intent annotations | Final email-thread decisions, our strength rubric or our category descriptions |
| [CLINC150](https://github.com/clinc/oos-eval) | English single-intent and out-of-scope controls | Multi-intent labels; the authors explicitly exclude that setting |
| [MASSIVE](https://huggingface.co/datasets/AmazonScience/massive) | EN/DE intent/slot robustness, linked localized examples | Financial thread reasoning or our evidence-strength gold; localization judgments are not strength labels |
| [Customer support tickets](https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets) | EN/DE email-shaped candidates with queue, priority, type and tags | Independently adjudicated gold for our policies. Audit provenance; the card advertises synthetic generation. Exclude agent answers and target labels from input. |

Existing BANKING77/WANLI and TAT-QA projections remain useful narrow diagnostics,
with their original meaning and training overlap tracked. Do not invent urgency,
due dates or thread-level gold from labels that do not encode them. Public
benchmark exposure during pretraining is another reason to include new,
authorized, representative business cases. No external corpus above replaces
that annotation work.

## Experiment and acceptance sequence

1. **Offline first:** freeze cases, family splits, label rubric and risk slices.
   Capture outbound request structure using existing test infrastructure, without
   endpoint discovery. Compare effective instructions, schemas, tools and source
   bytes; do not claim token-prefix lengths from character counts.
2. **Baseline repeats:** retain current model, settings and backend. Record actual
   model/engine/SDK/template identities where available; mark unknown weight
   identity honestly. Measure repeat disagreement before attributing it to a change.
3. **One change at a time:** fixed contract first; questions before state;
   instruction deduplication; selected prior results; optional final reminder.
   Use the same information and gold. Test cross-step shared-document layout only
   where permissions/schema allow it. History reuse is a later, separate challenger.
4. **Paired comparison:** counterbalance execution order and warming across
   variants, sequential local requests. Distinguish identical-input replay from
   different-document shared-prefix reuse and from cross-step reuse. Run natural
   arrival patterns as well as warm bursts. Do not insert random prompt nonces to
   force misses: that changes input. If cold state cannot be controlled, report
   observed cache state, not an alleged cold/warm causal speedup.
5. **Quality gate:** evaluate whole-case/process correctness, wrong automated
   routes, abstention errors, per-category confusion and multi-label exact-set/F1;
   inspect reason/value contradictions and strength confusion separately. Include
   invalid/timeout cases in denominators. Report EN/DE, hard-case and length slices.
   Use paired family-level intervals and repeated-run stability. Define acceptable
   risk with the business owner before unblinding; lack of significant difference
   on a small sample does not establish equivalence. New critical regressions
   block adoption; inconclusive results retain the baseline.
6. **Efficiency only after quality:** compare total/input/output/reasoning tokens,
   cache read/write counters, end-to-end latency and failures. TTFT/prefill require
   actual provider/stream measurements; our nonstream wall time cannot substitute.
   Normalize provider usage semantics before computing ratios/costs; never count
   reasoning twice. Include cache-write costs and missing-counter coverage. Reuse
   existing private reports and scorer hooks, not a second evaluation package.
7. **Final untouched check:** select on development only; test the selected layout
   once on new family-held-out gold. Keep full public reasons/results privately
   for review, without collecting hidden reasoning. Do not tune against that test
   and continue calling it held out. No live requests are authorized by this memo.

For perspective, with zero failures in `n` independent representative cases, the
one-sided 95% binomial upper failure bound is `1 - 0.05 ** (1/n)` (solve the
probability of zero failures). At 200 cases it is about 1.49%; at 3,000 about 0.10%.
Correlated variants are not independent cases. The initial 400-family budget can
guide improvements, but cannot establish very low enterprise failure rates or
power every subgroup comparison.

Runtime prompt experiments must not rewrite native V2 datasets. If a winning
layout becomes the fine-tuning template, version that rendering separately and
use it consistently for training and inference. New runtime V3 gold needs its own
explicit annotation contract; do not translate old confidence into strength.
Provider retention and isolation should be reviewed before opting into new cache
storage behavior for business data. None of this requires application persistence.

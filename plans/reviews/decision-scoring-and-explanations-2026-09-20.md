# Short explanations, generator quality and decision scoring

Date: 2026-09-20. Status: research and recommendations, not an implemented
contract change or model qualification. Two independent agents reviewed saved
outputs and upstream implementation. No new inference, training, weight downloads
or changes to a running generation job were performed.

## Explanation size

`Explanation.summary` currently has no specific length bound and belongs to each
question result. It is distinct from exact evidence quotes, missing facts,
request-unit descriptions and internal model reasoning.

Inspection of 132 authored seeds (`examplesPerScenario=4`) found 152 canonical
result summaries: median 59 characters, maximum 147. Sampled returned summaries
were longer: Qwen median/max 163/379 (9 summaries), GLM 187/397 (9), DeepSeek
strict-schema 107/224 (7). These are small observed samples, not population bounds.

Recommendation: ask for one short, grounded sentence, normally within 160
characters, and enforce a generous maximum of 400 characters on the summary
alone. A second sentence may identify a decisive limitation. Validate, retain
and repair overlong responses; never silently truncate. Verify German before
treating the soft target as universal. Do not truncate or cap the whole explanation
at 400: evidence makes some current canonical explanations longer than that.
For a collection, preserve item-specific detail in its units and evidence rather
than repeating every item in the collection summary.

Apply any future change consistently to prompts, contracts, schema, authored
targets, tests and documentation. Current authored derivatives publish the
canonical reference rationale, not the teacher's longer prose. Internal reasoning
budgets remain separate settings; short final summaries do not prevent a provider
from exhausting its budget before emitting final content.

## Generator evidence

See [recorded comparison](openrouter-generator-comparison.md). Six decision probes
and two rewrite probes do not establish a model ranking. Qwen and GLM each passed
4/6 decision checks; DeepSeek passed 3/6 in each response-format arm. These counts
mix semantic, citation and contract checks and must not be called pure accuracy.

GLM and DeepSeek handled one missing-attachment case better than Qwen, which
invented a placeholder action. Conversely, Qwen returned more consistently usable
outputs in this small comparison. Some rejected cloud rewrites preserve meaning:
the lexical guards miss equivalent comparator/negation wording. One payment
question is ambiguous and cannot support a clean error attribution. DeepSeek
strict schema improves formatting but still has an empty response at its token
limit and altered literal quote punctuation. No evidence here justifies replacing
local Qwen with a paid bulk generator for quality alone.

## Linked approaches

The [X article](https://x.com/_avichawla/status/2101563610644496464) demonstrates
fixed-choice next-token scoring using ordinary causal models. Its title was
verified through X syndication; full article text and snippets were recovered
through a public mirror because direct X access was denied. Private inspection
files are under `~/.local/share/foliqant/checks/decision-scoring-review-2026-09-20/`.
It does not supply trained Jev weights or calibrated correctness. Its demo
compares scoring to generating an answer plus explanation on a shared server;
that is not a controlled comparison of equal deliverables.

[SGLang's implementation](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/entrypoints/openai/serving_score.py)
returns selected label scores. [vLLM 0.29 generative scoring](https://docs.vllm.ai/en/v0.29.0/serving/online_serving/generative_scoring/)
also supports causal-model label scoring, but its documented result exposes the
first label's score per item. The [completion protocol](https://docs.vllm.ai/en/v0.29.0/api/vllm/entrypoints/openai/completion/protocol/)
supports requested token log probabilities. These are distinct interfaces, not a
universal OpenAI-compatible scoring API. LM Studio/Ollama parity was not tested.

openJev was inspected at commit
`a458733c5f43fc7f30b6e4381636cbfbf8437633`. Its
[model](https://github.com/Heman10x-NGU/openJev-verdict-2.0/blob/a458733c5f43fc7f30b6e4381636cbfbf8437633/verdict2/model.py)
uses ModernBERT with a marker scorer and separate correctness head. This is not
our ordinary causal-LM architecture and does not generate our explanations.
The [dataset card](https://huggingface.co/datasets/LocalLLaMA/typed-decisions)
identifies synthetic teacher labels and warns against treating teacher agreement
as correctness or comparing trained specialists to zero-shot generalists.
Therefore its headline benchmark is not proof of superiority for our workload.

The [training code](https://github.com/Heman10x-NGU/openJev-verdict-2.0/blob/a458733c5f43fc7f30b6e4381636cbfbf8437633/verdict2/train.py)
separates fit, calibration and development cases; its correctness estimate targets
agreement with the benchmark winner, not input sufficiency or whole-answer
adequacy. The [coverage metrics](https://github.com/Heman10x-NGU/openJev-verdict-2.0/blob/a458733c5f43fc7f30b6e4381636cbfbf8437633/verdict2/metrics.py)
sort test scores to construct coverage curves. Those describe performance;
production thresholds still need separate selection and untouched validation.

## Recommended experiments, not architecture changes

Keep structured causal-model inference as the portable default. Consider an
optional scoring adapter only for bounded labels/rubrics when a server exposes
all required scores. Verify tokenizer/template/continuation behavior and provide
an explicit unknown/no-match option. Do not interpret normalized option scores
as calibrated correctness. Multiselect and extraction require different semantics.

For evaluation use cases, express a rubric as independent typed questions:
supportedness, completeness, policy compliance, or pairwise A/B/tie/insufficient
evidence. A fast label scorer may later serve these questions; short explanations
and evidence still require generation. A generated rationale is not proof that
the decision was correct or a faithful trace of internal reasoning.

First repair ambiguous evaluation questions and generic validator false positives.
Then use a larger frozen development comparison plus independently labeled
holdouts. Group by source/template family; separate calibration, threshold
selection and final test. Measure semantic correctness, unsupported claims,
omitted units, citation fidelity, contract success, latency and cost separately.
Include option permutations, paraphrases, missing/contradictory evidence, prompt
injection, long context and German. Report errors among accepted cases alongside
automation coverage and uncertainty intervals. Compare label-only paths with
label-only paths, and measure the additional cost of explanations separately.

Do not adopt custom encoder heads, promise calibrated confidence, or select a
cloud generator based on this exploratory review.

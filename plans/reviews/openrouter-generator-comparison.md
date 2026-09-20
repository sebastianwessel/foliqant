# OpenRouter generator comparison — 2026-09-20

Status: bounded observed development probes, not model selection or production qualification.

Follow-up: [cloud Qwen Flash and pipeline improvements](cloud-qwen-flash-and-pipeline-improvements.md)
adds frozen cloud Qwen probes, a reasoning-enabled larger-budget diagnostic, and
a separately labeled replay under improved lexical guards. Original results
below remain unchanged.

The user authorized comparison of GLM 5.3 Flash and DeepSeek Flash through their
OpenRouter account. No provider integration or production endpoint change was
introduced. Only synthetic development tasks were submitted; no customer data,
training holdouts, credentials or hidden reasoning were committed.

DeepSeek's moving `~deepseek/deepseek-flash-latest` alias resolved on this date to
`deepseek/deepseek-v4.1-flash` (catalog canonical slug
`deepseek/deepseek-v4.1-flash-20260910`). The comparison pinned DeepInfra FP8,
disabled fallbacks, and retained exact request/response identities. GLM used the
user-selected GMICloud FP8 route. Each arm has six decision probes and two
meaning-preserving rewrite probes, temperature zero, requested paired seeds,
4,096 maximum completion tokens and provider-default reasoning. Requests were
serial. No failed response was silently repaired or replaced in scoring.

| Configuration | Decisions passing checks | Rewrites passing guards | Schema valid | Median time | Reported usage cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| DeepSeek / JSON object | 3/6 | 0/2 | 4/8 | 24.0 s | $0.008796 |
| DeepSeek / strict JSON Schema | 3/6 | 1/2 | 7/8 | 19.6 s | $0.006111 |
| GLM / JSON object | 4/6 | 1/2 | 8/8 | 21.6 s | $0.005138 |
| Historical local Qwen3.8-27B-Splash / JSON Schema | 4/6 | 2/2 | 8/8 | 24.7 s | Not measured |

The DeepSeek schema arm changed only response format, retaining schema-in-prompt
text. It resolved observed malformed field names. Both DeepSeek arms exhausted
all 4,096 output tokens on reasoning without returning a final answer on the
conditional extraction probe. The strict arm also changed literal citation
quotation marks. A valid threshold paraphrase was conservatively rejected by
the current lexical negation guard. Both strict-schema rewrites preserve meaning
on manual inspection, although only one passes the guard. The earlier GLM trial
also has one semantically valid rewrite rejected by the comparator phrase guard.
These guard failures must not be described as semantic model failures.

A payment-confirmation question has a known evidence-versus-state ambiguity; its
reference mismatch is not unambiguously a model error. A generic object subject
also has a debatable instance/type boundary. Keep these limitations in any score
summary. Tuple-bearing internal signatures are normalized to JSON on both sides
before comparing them to frozen JSON references.

The observed DeepSeek route has not demonstrated an improvement over GLM under
these settings. This is not a general ranking. Serving providers, tokenizer,
quantization and reasoning defaults differ; Qwen also used a different schema
prompting mode. One sample per synthetic case is insufficient to estimate bulk
acceptance, financial correctness or final training quality. A broader frozen
sample and a separately labeled reasoning-budget comparison would be needed
before choosing a bulk generator.

DeepSeek final-stream usage totaled $0.0149068584 for 16 requests. The usage API
confirmed DeepInfra for all requests, with insignificant rounding differences.
Only final assistant content and usage counts were retained, not hidden reasoning.

Full private artifacts: `~/.local/share/foliqant/checks/openrouter-deepseek-v4.1-flash-2026-09-20/`
(`report.md`, `comparison.json`, manifests, raw returned final content, usage audits
and independent semantic audit). Frozen source manifest SHA-256:
`7e15972e94348c9088b46d7482c2c42468b9ec4c82f38203e754b8fb1dc807ed`.
All generated data remains outside Git.

Public references: [alias](https://openrouter.ai/~deepseek/deepseek-flash-latest/),
[model](https://openrouter.ai/deepseek/deepseek-v4.1-flash),
[provider selection](https://openrouter.ai/docs/guides/routing/provider-selection),
[structured output](https://openrouter.ai/docs/guides/features/structured-outputs).

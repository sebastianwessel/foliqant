# Splash concurrency smoke check

Date: 2026-09-20. Status: **completed diagnostic; keep serial generation as the default**.

## Scope and integrity

This was a warmed, four-case ABBA smoke check against the local
`qwen3.8-27b-splash` endpoint. It compared two serial blocks with two width-two
blocks: choice plus request graph, then predicate plus whole-answer adequacy. Each
case appeared once in each arm, for eight total Chat Completions. The endpoint was
not restarted and its cache was not cleared.

The immutable external plan has SHA-256
`ccfb7f723dee8850c462bb8ca8d12376cb9aac5c6c49b14856d61fa8be10d2ea`.
The audit independently reconstructed all four requests from
`native-quality-repair-verification-v1-ce603a058735` and verified the plan, script,
source-file, original-call, message and schema hashes. Paired arms used the same
messages, JSON Schema, model identity, per-case seed, temperature 0.1, 8,192-token
limit and 300-second deadline. Structured output remained `json-schema`; this was
not an unconstrained text benchmark.

All eight calls completed with `finish_reason=stop`. Their private mode-0600
response files independently passed JSON Schema validation, strict typed parsing,
task-specific validation and semantic-signature comparison with the canonical
answer. Manual review found each classification, unknown predicate, conditional
request graph and adequacy judgment supported and complete. Both observations of
each case produced the same persisted canonical JSON bytes; raw HTTP envelopes
were different. No raw prompts or responses are stored in Git.

## Measurements

| Case | Width one | Width two | Width-two change |
| --- | ---: | ---: | ---: |
| Choice | 6.237 s | 58.017 s | +51.780 s (+830.2%) |
| Request graph | 52.277 s | 52.439 s | +0.161 s (+0.3%) |
| Predicate | 12.104 s | 12.184 s | +0.079 s (+0.7%) |
| Adequacy | 17.069 s | 28.758 s | +11.689 s (+68.5%) |

| Aggregate | Width one | Width two | Width-two change |
| --- | ---: | ---: | ---: |
| First block | 58.515 s | 58.019 s | -0.496 s (-0.8%) |
| Second block | 29.174 s | 28.760 s | -0.414 s (-1.4%) |
| Active wall time | 87.689 s | 86.778 s | -0.910 s (-1.04%) |
| Correct results/minute | 2.737 | 2.766 | +1.05% |
| Correct outputs | 4/4 | 4/4 | no change |

Width two saved 0.91 seconds across the two blocks. The per-request completion
pattern put almost all serial work on the width-two critical path: in the first
pair the normally short choice response completed after 58.0 seconds, and in the
second pair adequacy rose from 17.1 to 28.8 seconds. This pattern is compatible
with queuing or effectively serialized service, but it does not prove either.
Slower batched execution, resource contention, structured-decoding costs and
cache/order effects are also unresolved.

## Documented capability versus observed result

Splash's [official design and benchmark note](https://inco.ai/blog/splash/)
says its scheduler batches requests as they arrive and reports aggregate gains
for four concurrent coding prompts. That vendor benchmark used a 48 GB M5 Pro,
1,024-token output limits, reasoning at medium for the 27B, streamed-token timing
and a different workload; it does not report this JSON Schema task. The same note
says a fused decode path is used when output is not schema-constrained. Splash's
[official repository](https://github.com/incoai/splash) documents both concurrent
serving and JSON Schema output, but does not make the vendor throughput numbers a
guarantee for every schema-constrained workload.

LM Studio's [parallel-request documentation](https://lmstudio.ai/docs/app/advanced/parallel-requests)
explains the general distinction: a server may support continuous batching while
still queuing work beyond its configured concurrency. That page describes LM
Studio's llama.cpp control and is not evidence of the active Splash runtime's
configuration.

The current Foliqant adapter exposes client wall time and endpoint elapsed time,
but not server queue depth, batch membership, cache hits, memory pressure,
time-to-first-token, completion or reasoning token counts, or actual concurrency
settings. Consequently, this result cannot show whether batching occurred,
whether it is unavailable for this path, or whether the engine is faulty. It also
cannot generalize beyond these four warmed cases.

## Decision

Keep serial generation as the default. Width two preserved output quality but did
not deliver a practically meaningful throughput gain in the measured workload,
and the sample is too small for a production concurrency claim. Do not run a
second benchmark unless performance optimization becomes an explicit goal; a
future test would need server-side queue/batch/cache/token telemetry and a larger,
predeclared workload to attribute behavior.

# Cloud Qwen Flash comparison and bounded pipeline improvements

Date: 2026-09-20. Status: observed development probes and verified offline code
changes, not model selection, production qualification, or training readiness.

## Cloud experiment

The user authorized OpenRouter comparison of `qwen/qwen3.8-flash`. The catalog
resolved to `qwen/qwen3.8-flash-20260826`, through Alibaba (`alibaba`; quantization
not reported). Requests were serial, with no provider fallback or automatic
retry. Only the same synthetic development probes as the
[earlier comparison](openrouter-generator-comparison.md) were submitted.
Raw responses, including failures, remain outside Git.

All eight primary requests explicitly set `max_tokens: 4096`, temperature zero,
paired requested seeds, strict JSON Schema and provider-default reasoning
(enabled in the catalog). Exact messages and schemas match the earlier DeepSeek
strict-schema arm. The historical local Qwen arm uses a different schema-prompting
mode; this is not an isolated comparison of weights or hardware.

| Reasoning-enabled/default configuration | Decision checks | Original rewrite guards | Schema valid | Median request | Cost for eight requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Local Qwen3.8-27B-Splash | 4/6 | 2/2 | 8/8 | 24.7 s | Not measured |
| GLM 5.3 Flash / JSON object | 4/6 | 1/2 | 8/8 | 21.6 s | $0.005138 |
| DeepSeek V4.1 Flash / strict schema | 3/6 | 1/2 | 7/8 | 19.6 s | $0.006111 |
| Cloud Qwen3.8 Flash / strict schema | 2/6 | 0/2 | 6/8 | 34.3 s | $0.010627 |

These are strict contract/reference checks, not pure semantic accuracy. The
cloud Qwen missing-attachment and conditional-branches requests each used 4,096
completion tokens, all reported as reasoning, returned empty final content and
finished with `length`. They establish budget exhaustion, not inability to solve
the questions. The same ambiguity in the payment probe and subject-policy
boundary in the generic-object probe remain material limitations.

### User-requested larger-token diagnostic

Both truncated cases were retried separately with **only `max_tokens` changed to
8192**. Original failures were retained, not replaced in the table. Both now
finished normally and returned schema-valid responses. This is a two-case
diagnostic selected after failure, not a complete matched arm.

- Missing attachment: extracts the visible replacement and correctly marks the
  collection partial. Its generic subject `the filter` disagrees with the
  reference's identifying-subject rule; the missing-content treatment itself
  is supported. Elapsed 59.7 s.
- Conditional branches: omits the explicitly requested alternative action and
  treats unknown execution conditions as incomplete extraction. This is a
  substantive completeness problem after truncation has been removed. Elapsed
  159.5 s; 7,783 completion tokens, of which 7,160 were reported as reasoning.

The two diagnostic calls cost $0.005305. Future reasoning-enabled comparisons
must predeclare sufficient output budgets and report truncation separately from
semantic errors. Do not turn this partial diagnostic into a revised full-arm
accuracy or latency number.

### Completed diagnostic excluded from model selection

Before the user's clarification, eight additional requests tested
`reasoning.enabled: false`, changing no other request setting. They returned
8/8 valid schemas, passed 3/6 decision checks and 0/2 rewrite guards; median
request time was 6.1 s and cost was $0.003860. Usage reported zero reasoning
tokens. These results are retained for provenance only. The user explicitly
wants reasoning-enabled generation: do not recommend or pursue disabled
reasoning as the generation configuration. No production endpoint or `.env`
setting was changed.

## Interpretation

Cloud Qwen has not demonstrated a quality advantage over local Qwen or GLM on
these probes. This is not a general model ranking. In particular, increasing
the budget fixed completion failure, while the conditional-extraction failure
remained. A broader frozen comparison with independent labels is needed before
switching the bulk generator. No hidden reasoning text was retained or inspected;
only final assistant content, usage counts and request identities were stored.

Private experiment directory:
`~/.local/share/foliqant/checks/openrouter-qwen3.8-flash-2026-09-20/`.
It contains frozen request manifests, a runtime snapshot, all final responses,
`comparison.json`, `updated-guard-replay.json`, provider/usage audit, and the
independent content review. The original source-manifest SHA-256 is
`7e15972e94348c9088b46d7482c2c42468b9ec4c82f38203e754b8fb1dc807ed`.

## Implemented improvements

- `Explanation.summary` alone is limited to 400 characters. Prompts aim for one
  grounded reason within 160 characters, allowing a second sentence for a
  decisive limitation. Exact citations, missing facts and the full explanation
  are not subjected to this cap. Overlong outputs are retained and rejected for
  retry, never silently truncated.
- Generic lexical rewrite checks recognize `unable` and inclusive `or more`,
  `or less`, and `or fewer`. Tests retain failures for removed/added negation,
  reversed comparisons and inclusive-to-strict changes. Lexical equivalence is
  still not a semantic entailment guarantee; blind solving remains required.
- Schema, prompts, seed recipe, guide, specification and skill are aligned.
  Recipe digests change, preventing mixed-contract resume or repair. Existing
  completed artifacts are unchanged; already running processes were not stopped.

Replaying the **same saved responses** under the new guards changes rewrite
passes to GLM 2/2, DeepSeek strict schema 2/2, and cloud Qwen default 1/2; local
Qwen stays 2/2. Cloud Qwen's unchanged rewrite remains rejected. This is measured
removal of validator false positives, not fresh generation or improved model
quality. The original comparison and its frozen checks remain intact.

Verification after formatting: 482 offline tests passed, 7 native integration
tests deselected; mypy checked 53 files; Ruff lint and changed-file formatting
passed; all 23 schemas matched; documentation and tracked-data audits passed;
`git diff --check` passed. Boundary and retry tests cover 400/401 characters,
unchanged long evidence, retained overlong output, and all 152 authored summaries
(maximum 147 characters). No new-prompt model-quality claim follows from these
offline checks. Spec digest:
`sha256:366a07cb68f8cc707afd9c9d8963cca480c1404373d7dbfb1ca7843eba3ea3b0`.

## Separate deferred research

[Evaluation workstream](../research/evaluation-workstream.md) scopes rubric-based
judging, independent labels and bias/robustness checks for later work.
[Custom inference options](../research/custom-inference-options.md) compares
ordinary causal inference, supported classifiers and custom heads. Neither plan
changes current serving requirements or authorizes training/model downloads.

Primary API references: [Qwen Flash catalog](https://openrouter.ai/qwen/qwen3.8-flash),
[reasoning and completion budgets](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
[structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).

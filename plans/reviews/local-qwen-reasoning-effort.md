# Local Qwen reasoning-effort capability check

Date: 2026-09-20. Status: observed capability probes, not a completed
reasoning-effort quality benchmark or production setting change.

Follow-up completed: the [60-request standalone comparison](splash-reasoning-effort-comparison.md)
now recommends low effort for the next bounded generator pilot. The observations
below remain the original capability-check evidence.

The user requested `reasoning_effort: low`, `medium`, and `xhigh` with reasoning
enabled. Before expanding the model comparison, inspect whether the installed
runtime actually applies those settings. Do not equate an HTTP success with an
effective change in reasoning effort.

## Observed Splash behavior

The server's native model inventory reports:

| Model | Format | Initially loaded | Advertised reasoning settings | Default |
| --- | --- | --- | --- | --- |
| `qwen3.8-27b-splash` | Splash, 4-bit | Yes; context 262,144 | off, on | on |
| `qwen/qwen3.8-27b` | MLX, 4-bit | No; already installed | off, low, medium, xhigh, on | xhigh |

The model capability is runtime/package specific. Qwen's upstream model card
supports graded effort; this does not mean every conversion and inference
backend exposes it.

The exact [Inco Splash package](https://huggingface.co/incoai/Qwen3.8-27B-Splash)
also explicitly supports low, medium and xhigh through the standalone Splash
API. Its [server implementation](https://github.com/incoai/splash/blob/main/server/frontend.py)
passes the selected effort into the tokenizer template. Therefore the observed
limitation is the current LM Studio integration/API path, not the model weights
or the upstream Splash engine. Do not generalize the live inventory's on/off-only
declaration to Splash itself.

Three serial requests used the same saved arithmetic development probe, messages,
schema, seed, temperature zero and explicit `max_tokens: 8192`. Only the top-level
`reasoning_effort` differed. Order was low, xhigh, medium. No retries, parallel
requests, reasoning-off request, model load/unload, or production configuration
change was made.

| Requested effort | Prompt tokens | Completion tokens | Reasoning tokens | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| low | 3,546 | 1,142 | 934 | 23.7 s |
| xhigh | 3,546 | 1,142 | 934 | 17.2 s |
| medium | 3,546 | 1,142 | 934 | 17.2 s |

All finished with `stop`, returned identical final text, and had identical
SHA-256 fingerprints of the reasoning text. Only the fingerprint and character
count were retained, not internal reasoning text. The elapsed difference is not
evidence of effort control; request order, warm state and cache effects are not
isolated here.

A separate native `POST /api/v1/chat` capability request with `reasoning: low`,
`store: false` and a one-token cap returned HTTP 400 before generation. Its
structured error explicitly states that this Splash model supports only `off`
and `on`. The one-token request is a parameter-validation probe, not a quality
or speed test.

Conclusion: the OpenAI-compatible endpoint accepts these requested effort levels
but they have no observed effect on the currently loaded Splash model. Running a
full three-arm quality comparison on this configuration would not measure three
different reasoning settings.

## Original next comparison boundary (before standalone test)

Prefer retaining Splash and testing its standalone API if an endpoint is made
available. Its documented OpenAI-compatible API accepts the intended parameter;
the server also offers prompt-template inspection without generation. No
standalone server installation, launch, model download or change to the shared
server was performed for this check.

The installed MLX Qwen model is an alternative that advertises graded effort.
Confirm user preference before loading it on the shared server, because this
can change memory residency or evict Splash. First verify actual parameter
application through the selected API, then compare the
same frozen cases with balanced effort order, a common sufficient completion
budget, and no reasoning-off arm. Report budget exhaustion separately from
semantic errors. Do not claim a speed gain from effort if the backend also
changed. An MLX capability probe is prepared privately but was not run as part
of this Splash check.

Private artifacts:
`~/.local/share/foliqant/checks/local-qwen-reasoning-effort-2026-09-20/`.
They include request manifest, before/after model inventory, final responses,
usage/fingerprints, and the native validation error. Source development-manifest
SHA-256: `7e15972e94348c9088b46d7482c2c42468b9ec4c82f38203e754b8fb1dc807ed`.

## Primary references

- [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B#api-usage)
  documents low, medium and default xhigh. Its
  [chat template](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja)
  implements effort through reasoning instructions; medium omits the additional
  low/xhigh instruction.
- [LM Studio native chat](https://lmstudio.ai/docs/developer/rest/chat)
  specifies that unsupported reasoning settings are rejected. Live model
  capabilities are available through the
  [model inventory](https://lmstudio.ai/docs/developer/rest/list).
- [Issue 2413](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/2413)
  is a user report concerning `off` on LM Studio 0.4.21/Windows. It is a reason
  to check actual behavior, not proof that low/medium/xhigh work on Splash or that
  this local server has the same bug/version.

No source, schema, `.env`, model weights, running generation process, or server
settings were changed by this check. Generated evidence remains outside Git.


## Standalone Splash follow-up

The user made the standalone endpoint available at `192.168.2.101:8000`.
Live health, inventory and status checks succeeded. It serves
`incoai/Qwen3.8-27B-Splash` on Apple M5 Pro with context 262,144.
The endpoint accepted unauthenticated requests; restrict network access and
configure authentication before broader use. No server settings were changed.

`/apply-template` confirmed different prompt fingerprints for low, medium and
xhigh. Low adds a brief-thinking instruction; xhigh adds a careful-reasoning
instruction; medium omits those extra instructions. These controls guide the
model through its prompt; they do not impose a hard reasoning-token budget.

The same frozen arithmetic probe was then run serially in the original order
(low, xhigh, medium), temperature zero, same seed and explicit max_tokens 8192.
Reasoning remained enabled. There were no retries or model load/unload requests.

| Effort | Prompt tokens | Completion tokens | Reasoning tokens | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| low | 3,572 | 430 | 239 | 13.0 s |
| xhigh | 3,584 | 1,865 | 1,691 | 45.6 s |
| medium | 3,546 | 1,282 | 1,085 | 27.1 s |

All three finished with stop, passed their requested JSON Schema, returned the
correct false predicate, and explained that 4 × 10 + 3 = 43 kWh rather than the
proposed 7 kWh. Final outputs and reasoning fingerprints differed. Internal
reasoning text was not retained. This establishes observed effort control on
the standalone path; one arithmetic case does not establish dataset quality,
a robust timing ranking, or a preferred production setting. The reported
cached prompt tokens were zero; request order and warm state were not balanced.
Do not attribute differences versus LM Studio solely to reasoning effort.

Evidence is in the private `standalone/` subdirectory of the artifact directory
above, including the template check, request manifest, final responses, usage,
schema validation summary and before/after model inventory. Production `.env`,
generation code and active dataset runs remain unchanged. The subsequent diverse
comparison, with balanced order, blinded semantic review and separate budget
outcomes, is recorded in the follow-up linked above.

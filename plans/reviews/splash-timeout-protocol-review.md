# Splash timeout protocol review

Date: 2026-09-21. Scope: read-only comparison of Foliqant's generation wire
contract and timeout handling with Splash. No endpoint discovery, model download,
runtime change or inference was performed for this review.

## Source boundary

The upstream comparison is pinned to Splash commit
[`c0c08e9defa4986f7358967b7db67f5a3b1b1c9f`](https://github.com/incoai/splash/commit/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f)
from 2026-09-20. The deployed Splash build is not proven to be that commit. Its
reported native build identity is
`src-2f7e320b81701542001ff88c380acbbd4ba4795b125d51314687094ad462cf1f`,
which does not identify the Python frontend revision by itself.

The archived `server.py` inspected during the earlier timeout investigation is
byte-identical to the pinned upstream file (SHA-256
`e9ed3ab92abbbb7bb73b9cbb172194853e2b2c72f4ad949f64b48584fe4acb96`).
The archived `frontend.py` differs from the pinned file only in the later,
unrelated reuse of an image-render marker. Its deadline, reasoning, schema and
output-budget paths match. This is strong evidence for the reviewed behavior,
but it is not a complete deployed-build attestation.

## Request compatibility

The reconstructed request uses `POST /v1/chat/completions` with
`reasoning_effort: "low"`, `max_tokens: 8192`, `stream: false`, and the standard
`response_format.type: "json_schema"` wrapper. The full request is 17,308 bytes;
the task-specific-schema request is 11,344 bytes. The initial pruned-schema
reconstruction loaded canonical metadata and changed schema key order; it was
semantically equivalent but not the original wire bytes. The later
`timeout-repro-1243478a92088f43` bundle restores runtime declaration order.
Protocol-field compatibility below is unaffected by this diagnostic confound. The
private reconstruction contains no endpoint credentials, reference answer or
reasoning trace and remains outside Git.

No incompatible request field was found:

- Splash accepts `low`, passes it to the Qwen chat template and enables thinking.
  See
  [`server/frontend.py` lines 651-705](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/frontend.py#L651-L705)
  and
  [`server/frontend.py` lines 727-785](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/frontend.py#L727-L785).
  Splash also documents `low`, `medium` and `xhigh` for Qwen3.8-27B in
  [`README.md` lines 47-57](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/README.md#L47-L57).
- Splash accepts `max_tokens` as the fallback alias for
  `max_completion_tokens`, rejects a prompt-plus-output budget beyond the
  context window, and forwards the selected value as the native logical output
  limit. See
  [`server/frontend.py` lines 893-945](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/frontend.py#L893-L945)
  and
  [`server/backend.py` lines 446-474](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/backend.py#L446-L474).
- Splash extracts and validates the supplied JSON Schema, inserts it into a
  system message, builds a token-mask grammar, and validates completed output.
  See
  [`server/tool_schema.py` lines 604-638](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/tool_schema.py#L604-L638)
  and
  [`server/frontend.py` lines 673-697](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/frontend.py#L673-L697).
  Splash does not use the wrapper's `strict: true` flag, but it always
  constrains and postvalidates the schema itself. This is not a timeout or
  validation-gap explanation.

Foliqant constructs those same fields in
[`endpoint.py` lines 467-495](../../model/src/foliqant_model/curation/endpoint.py#L467-L495).
The repeated timeout is therefore not evidence of a rejected parameter or an
OpenAI/Splash payload-shape incompatibility.

## Deadline mismatch

Foliqant enforces the configured 300-second deadline in its worker process and
HTTP client. It does not place Splash's vendor-specific `timeout` field in the
request body. See
[`endpoint.py` lines 285-305](../../model/src/foliqant_model/curation/endpoint.py#L285-L305),
[`endpoint.py` lines 345-359](../../model/src/foliqant_model/curation/endpoint.py#L345-L359),
and
[`endpoint.py` lines 649-713](../../model/src/foliqant_model/curation/endpoint.py#L649-L713).

Splash uses `body.timeout` when present and otherwise applies its server timeout,
capped by the server configuration. See
[`server/frontend.py` lines 384-400](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/frontend.py#L384-L400).
The pinned server default is 1,800 seconds at
[`server/server.py` line 1838](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/server.py#L1838).

This mismatch explains why Foliqant reports `TIMEOUT` at exactly 300 seconds and
receives no completed provider response, finish reason or token usage. It does
not explain why generation failed to finish within 300 seconds and is not the
root cause of the slow request. Adding a Splash-only request field would be a
vendor-specific compatibility change and is not proposed here.

## Reasoning and schema limits

`reasoning_effort: "low"` is a chat-template hint in Splash, not a numeric token
or time budget. With thinking enabled, Splash's grammar permits reasoning text
until the think-end token and applies the JSON grammar afterward. See
[`server/tool_schema.py` lines 604-614](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/tool_schema.py#L604-L614).
The same 8,192-token logical output ceiling covers reasoning and final content.

Consequently, pruning unused JSON Schema branches reduces request size and the
JSON constraint surface, but it need not shorten work performed before the JSON
phase. Reasoning, constrained final output, queuing and backend execution remain
possible locations for a slow request. The source establishes this mechanism;
it does not identify which phase caused the current timeout. A live observation
is required to distinguish `reasoning_content` from final `content` activity.

## Cancellation and status

Current Splash polls the client connection while collecting both streaming and
non-streaming output. EOF triggers backend cancellation. See
[`server/server.py` lines 704-743](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/server.py#L704-L743)
and
[`server/backend.py` lines 630-639](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/backend.py#L630-L639).
The upstream non-streaming disconnect regression test verifies cancellation and
a successful following request in
[`dev/tests/test_server.py` lines 6295-6319](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/dev/tests/test_server.py#L6295-L6319).
The matching archived server source and current idle status provide no evidence
that a timed-out Foliqant request remained orphaned.

Splash exposes detailed read-only state at `GET /status`; `GET /health` and
`GET /ready` do not establish that the scheduler is idle. Route behavior is in
[`server/server.py` lines 339-365](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/server.py#L339-L365).
`/status` requires authentication when an API key is configured because only the
root page, `/health` and `/ready` are public; see
[`server/server.py` lines 168-191](https://github.com/incoai/splash/blob/c0c08e9defa4986f7358967b7db67f5a3b1b1c9f/server/server.py#L168-L191).

The authorized status read before the bounded follow-up showed `ready: true`,
no queued, prefilling, decoding or mask-waiting work, no active generation HTTP
request, a healthy Metal backend and normal memory pressure. Lifetime counters
reported 1,562 submitted requests, 1,555 completed, seven cancelled and zero
failed. The seven cancellations are consistent with cleanup but do not by
themselves identify which callers cancelled them.

## Conclusion

The reviewed wire contract is compatible with current Splash. The only concrete
protocol mismatch is the client-only 300-second deadline versus Splash's longer
server deadline, and that mismatch is not the generation root cause. Current
evidence does not justify raising token or timeout limits, disabling reasoning,
weakening schema validation, adding a Splash-specific fallback, or changing the
runtime based on the source review alone. The bounded streaming observations
and generic serialization prompt change are recorded in
[the live verification report](curation-recovery-source-quality.md), which owns
the later evidence about the actual stall phase.

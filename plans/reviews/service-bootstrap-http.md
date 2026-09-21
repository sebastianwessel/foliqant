# Bootstrap, CLI and synchronous HTTP review

Status: verified synchronous-service milestone; full-service goal remains incomplete.
Date: 2026-09-21. Parent baseline: `5d42dce`.

## Scope

This milestone composes immutable workflow plans, model/MCP adapters, trusted
async handlers and safe telemetry under one application lifespan. Deployment
settings are strict, versioned and generated as JSON Schema. The CLI supports
init, validate, explain, doctor, run and serve. HTTP execution is synchronous;
there is no in-memory substitute for durable acceptance or recovery.

## Verification and findings

- Offline service tests cover actual composed runner/HTTP authentication,
  concurrent identity isolation, shared admission, deadlines and cancellation.
  No model inference or external authentication endpoint is used.
- Base and HTTP-only production installs execute scaffold, validate, doctor and
  run successfully. They contain 26 and 33 distributions respectively, without
  pytest, mypy, Ruff, Torch or MLX. A regression checks the actual HTTP extra and
  installed console script; this found and fixed its missing httpx2 dependency.
- Settings round-trip tests found that omitted bearer identity fields became
  explicit nulls and failed strict validation. Frozen settings now serialize
  absent optional values without changing their meaning. Unknown workflow grants
  fail preparation, and configuration/environment reads are bounded.
- Handler callbacks must be asynchronous. Input/output schemas are validated,
  writes remain disabled, cancellation propagates, and no model/tool budget is
  consumed by pure handlers. Shutdown drains and cancels cooperative callers.
- Model cleanup failures preserve completed outcomes and original caller errors.
  They produce only a fixed safe warning. Logging drains off-loop and reports
  incomplete shutdown. Arbitrary exception text never becomes CLI diagnostics.
- Explain exposes branch destinations and named bindings without prompts or
  literals. Runtime commands alone resolve the config-local environment and
  install explicitly configured process-owned telemetry.
- Independent review found unbounded cache-lock waits during a JWKS outage and
  missing public-key algorithm interoperability. Key lookup now shares a deadline
  across lock/fetch/cleanup, throttles failed/unknown-key refreshes, and accepts an
  omitted key algorithm only within the configured type/curve policy. Optional
  `nbf` claims receive strict finite numeric validation as well as SDK checks.
  JWKS negotiates identity encoding and bounds its public raw stream explicitly:
  SDK byte iterators otherwise await their own cleanup during cancellation.
  Tests cover simultaneous stalled body/cleanup and rejected compression.
- Real loopback subprocess tests cover authenticated requests, safe bind failure
  and SIGTERM. Review found that Uvicorn restores/replays signals before an outer
  application context necessarily closes. Provider, JWT and telemetry ownership
  is now inside the standard ASGI lifespan; no signal or SDK internals are patched.
- Technical CLI run failures use a final safe stderr error with a nonzero exit;
  they do not emit a success-shaped result containing customer data. Business
  `needs_review` remains a successful terminal result.

JWK interoperability follows [RFC 7517 section 4.4](https://www.rfc-editor.org/rfc/rfc7517.html#section-4.4):
`alg` is optional on a public key. Only configured asymmetric algorithms and
matching key types/curves may be selected; omission must not authorize an
algorithm outside operator policy. JWT signatures are verified with PyJWT.

## Closing verification

- 603 service tests pass, including real temporary loopback servers, signal
  shutdown, safe startup/bind failures, installed HTTP-extra packaging and SDK
  doubles. All temporary servers are stopped.
- 934 model-tooling tests pass; seven native integration tests are intentionally
  deselected. No native model or inference endpoint was used.
- Strict typing passes for all service source files and the three Python examples,
  plus model tooling. Ruff lint/format, ten generated service schemas, 27 unchanged
  native schemas, documentation links, skill validation and specification checks
  pass. The documented embedded bootstrap example also runs offline.
- Repository staging/data/secret audits precede the local commit. No push occurs.

## Remaining acceptance

Durable PostgreSQL/Redis recovery, persisted budgets/effect identity, asynchronous
HTTP retrieval/cancellation, child dispatch/join, production credential storage,
remaining provider conformance and the complete deployment review are still
required. A synchronous result is not a durable receipt. No push, model request,
dataset mutation or change to the model-generation environment occurred.

# Workflow service implementation status

Owner mandate: full Python/PydanticAI service implementation with current MCP,
OAuth, protected identity and OTel, async scalability, tests, examples, docs,
skills and independent review. The goal remains active; this file does not reduce
it to the currently implemented slice. Canonical scope: specification 11.

## Verified foundation

- Independent service uv project and lock; pinned current PydanticAI 2.46.0,
  MCP 2.2.0, Pydantic 2.13.5 and selected adapter extras. Runtime/dev separation;
  no training dependencies in the service environment.
- Strict envelope, independent optional tenant/principal, protected claim checks,
  explicit authenticated enrichment and immutable accepted JSON values.
- Safe raw UTF-8 JSON decoder with duplicate-key, nonfinite, nesting and byte
  checks; fixed safe errors instead of provider/Pydantic content.
- Bounded async admission with independent capacity, waiting deadlines and
  cancellation-safe release, including a post-acquisition deadline recheck.
- Bounded blocking SDK executor reserves capacity before task creation, keeps
  started workers counted after caller cancellation/timeout, copies task-local
  context and reports incomplete shutdown. Cancellation removes queued work.
- Safe JSON logging with a bounded queue of sanitized strings; blocked stderr
  cannot block the event loop. Overflow and sink errors are counted.
- Defensive copying also protects directly constructed core envelopes. RFC 6901
  bindings distinguish missing values from null and preserve explicit defaults.
- Four service JSON Schemas and drift check; standard-library core import
  guard and installed-package typing markers. 119 foundation/binding/logging
  tests pass; targeted strict mypy, ruff and formatting pass.
- Shared native-contract extraction passed 934 offline model tests, unchanged
  native schema bytes, model mypy/ruff and isolated wheel/build checks. Runtime
  and training consumers import one lightweight typed contract package.

Compiler and native decision adapters brought the service suite to
172 passing tests, with strict mypy and ruff passing.
Compiler fixes cover exact-byte dependency revisions, retained schema resources,
strict authoring and distinct named tool policy. Closing regressions cover
fragment-target validation, bounded-literal error mapping and implicit review exits.
The native adapter also reparses existing Pydantic results to prevent mutated
instances from bypassing validation. These checks are not full replica/load, provider integration or
end-to-end identity-isolation evidence. The full service remains incomplete.

## Embedded runtime slice

- Async runner executes immutable compiled plans through an injected executor,
  with per-run identity, results and budgets. Deterministic routing handles
  unresolved results before normal transitions and preserves skipped branches.
- Input validation and identity checks happen before admission. Unexpected
  exceptions become safe errors; task cancellation propagates. Failures preserve
  accepted payloads and already completed step results.
- Attempt reservations precede I/O; failed/unreported requests remain charged.
  Missing token counts stay unknown, and cache/reasoning subsets are validated
  against known totals. These counters are explicitly not durable.
- Frozen JSON Schema resources compile into offline validators with no runtime
  file/network resolution. Public execution/receipt schemas bring the service
  schema count to six. Status and error definitions reuse core types.
- `examples/embedded-workflow` demonstrates real compiler/schema/runner/result
  integration with pure deterministic handlers and no model calls. It does not
  substitute a pretend classifier for the future model adapter.
- Repository service skill, guide, schema generation map and separate CI quality
  job cover the implemented slice. Independent runner review closed four concrete
  findings; see [runtime review](reviews/service-embedded-runtime.md).

This is not the model-enabled CLI/HTTP application or production recovery.
Closing local checks: 263 service tests passed; strict mypy passed for 35 source
files including the example; lint, formatting, six service schema snapshots,
27 unchanged native schema snapshots, documentation links and skill validation
passed. The matching CI job has been added but has not run remotely.

## Model execution slice

- Closed model profiles select explicit IDs, APIs, structured output modes,
  environment credential references, capabilities and bounded settings. One
  application lifespan owns the async OpenAI, compatible, Azure and Anthropic
  clients. No model discovery or hidden SDK retries occur.
- PydanticAI executes native decisions and text/schema LLM steps under per-alias
  admission and per-step deadlines/budgets. Host validation checks decisions,
  evidence and original schemas. Refusals/truncation never become successful
  partial decisions. Unreported usage remains unknown.
- Provider schemas fully inline frozen local references, preserve intersections
  and annotation data, and enforce expansion limits. Authored schemas retain
  their constraints with non-strict provider output. Unsupported native-mode
  combinations fail preflight before charging attempts; no silent mode switches.
- `examples/inbox` provides Markdown decision steps, profiles, safe logging and
  embedded routing. It is nondurable and uses a demonstration identity, not an
  authentication implementation. Tests use offline model doubles and transports.
- Review closed SDK timeout/accounting and schema-conversion issues; see
  [model adapter review](reviews/service-model-adapters.md). Final verification:
  359 service tests, strict typing, lint/format and seven schema snapshots.

The full service remains incomplete. Bedrock is explicitly disabled until its
credential discovery and worker lifetime can be bounded. Production telemetry, durable transports and child workflows still need their
implementation and acceptance checks. The subsequent MCP slice below covers
read-only tools and SDK OAuth integration.

## MCP and model-tool slice

- Eight service schemas now include closed HTTP/stdio MCP deployment profiles.
  Frozen declared catalogs are checked against bounded SDK discovery, with
  confined schemas and independent argument/result validation.
- Host-authorized read tools run as explicit MCP steps or PydanticAI function
  tools. Required/named tool policies require validated success before final
  output. Input-required stops in review without automatic interaction rounds.
- Fresh per-caller SDK sessions preserve independently optional trusted IDs,
  forward only configured identity/W3C metadata, strip baggage task-locally and
  retain admission through bounded same-task cleanup.
- SDK OAuth owns discovery, registration, PKCE/state, resource binding and refresh.
  HTTP egress checks include explicit authorization origins. Credential hooks and
  token storage are scoped to server/resource/auth reference/tenant/principal;
  production storage protection and actual authorization remain host obligations.
- `examples/mcp-tools` executes a real stdio subprocess and compiled workflow
  with no inference. In-process, ASGI, MockTransport and subprocess tests cover
  protocol behavior; see [MCP review](reviews/service-mcp.md).
- Closing checks: 452 service tests plus strict typing, lint/format, eight service
  schemas, unchanged native schemas, docs/skill and staged-data audits pass.

Writes and interactive continuation remain disabled pending durable identity and
reconciliation. Full service deployment, privacy-filtered OTel export, database/
broker recovery and child workflows remain required, not implicitly delivered
by these adapter tests.

## Safe telemetry slice

- Optional OTLP/HTTP trace and metric endpoints, secret header references and
  bounded exporter settings have a ninth generated service schema. No endpoint
  means no exporter; ambient endpoint/header settings do not silently enable one.
- A standard-library observer port wraps workflow and step execution, including
  review, failures and cancellation. Valid transport context takes precedence
  over metadata as one complete W3C carrier; incoming metadata stays unchanged.
- Spans are sanitized before queueing. Content, identity, tool definitions,
  exception events, raw status text and arbitrary resource/scope/link metadata
  are excluded. Current-context propagation is isolated between concurrent runs.
- PydanticAI owns one content-disabled inference span per admitted request.
  Host metrics preserve measured zero versus unavailable usage without duplicate
  SDK counts. Metric views drop unreviewed instruments; exemplars are disabled.
- Actual MCP SDK ASGI tests prove client/server trace continuity, per-caller
  isolation and protected metadata without baggage. No network/model calls occur.
- The embedded example now supports `--telemetry`; an isolated production install
  with that extra has 37 distributions, no dev/training packages, and executes
  offline. Closing verification: 505 service tests, strict typing, lint/format,
  nine service schemas, unchanged native schemas and docs/skill audits pass. See the [telemetry review](reviews/service-telemetry.md) for limitations
  and independent review findings.

The full service remains incomplete. OTel's public batch shutdown has no timeout
parameter; the host bounds its caller wait and retains one owned cleanup worker.
A real collector deployment and live-provider qualification remain separate from
these SDK/transport tests.

## Remaining implementation sequence

1. Complete deployment/provider settings and concrete durable-operation contracts;
   retain the shared extraction and execution-contract regressions.
2. Complete executable service CLI/bootstrap and safe observations around the
   implemented compiler, bounded embedded runner and PydanticAI model adapter;
   extend the current `examples/inbox` application as these capabilities land.
3. Add authenticated HTTP ingress and integrate the implemented MCP runtime into
   service bootstrap. Complete production credential storage/login operations
   and deployment conformance; retain SDK wire and caller-isolation regressions.
4. Integrate the verified safe OTel runtime into the final service bootstrap and
   operator configuration; retain prequeue privacy, bounded caller cleanup and
   no-exporter regression coverage. Add a runnable collector deployment example.
5. Freeze durable SQL/operation contracts, then implement PostgreSQL inbox,
   leases/fencing, persisted budgets/effect identity, checkpoints/outbox, async
   HTTP retrieval/cancellation, Redis recovery/output and webhook delivery.
6. Implement bounded child dispatch/join, per-child authorization, dependency and
   conditional holds, correction/reconciliation and retention/deletion operations.
   Real broker/database crash/redelivery tests are required.
7. Finish provider conformance, install/container/CI checks, full root regression,
   runnable examples/Compose, generated configuration docs and service skill.
   Independent end-to-end review must close findings before completion.

## Review boundaries

The [contract review](reviews/service-contract-readiness.md) supports the bounded
foundation and shared extraction; it is not full-service or human digest approval.
Current dependency research is recorded separately. Protocol simulations prove
wire behavior, not financial correctness or live-provider production readiness.
No extra model inference, cloud deployment, repository push or dataset mutation
has been performed as part of the service foundation.

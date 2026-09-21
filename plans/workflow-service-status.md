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

Compiler and native decision adapters bring the current full service suite to
172 passing tests, with strict mypy and ruff passing.
Compiler fixes cover exact-byte dependency revisions, retained schema resources,
strict authoring and distinct named tool policy. Closing regressions cover
fragment-target validation, bounded-literal error mapping and implicit review exits.
The native adapter also reparses existing Pydantic results to prevent mutated
instances from bypassing validation. These checks are not full replica/load, provider integration or
end-to-end identity-isolation evidence. The full service remains incomplete.

## Remaining implementation sequence

1. Complete deployment and execution contracts before
   execution implementation; retain the shared extraction regressions.
2. Deliver the embedded/CLI vertical slice: Markdown/YAML compiler, strict bindings,
   native/text/schema model adapter via PydanticAI, deterministic routing, bounded
   runtime, safe observations and minimal `examples/inbox` application.
3. Add authenticated HTTP and host-owned current MCP stdio/HTTP with declared
   catalogs, schema verification, OAuth/credential hooks, protected metadata and
   required-tool success semantics. Verify concurrent callers cannot share auth
   state; no blocking SDK work on the event loop.
4. Add standards-based OTel trace/metric export and MCP propagation, pre-export
   privacy filtering including third-party spans/events, bounded cleanup and
   no-exporter behavior. Finish safe logging and operator configuration.
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

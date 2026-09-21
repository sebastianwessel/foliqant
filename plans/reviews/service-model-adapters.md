# Model adapter review

Scope: deployment profiles, PydanticAI model execution, owned provider clients,
offline schema expansion and the embedded inbox example. This is a verified
milestone of the full service, not production or live-model qualification.

Independent reviewers reproduced and closed these issues:

1. SDK-synthesized reasoning usage erased the distinction between unknown and
   zero. Accounting now uses the normalized field's presence. Regression tests
   exercise omitted, explicit zero and positive Responses usage.
2. SDK timeouts were classified as dependency failures. Provider bindings now
   register typed timeout exceptions; wrapped SDK timeout causes become `timeout`
   without leaking private diagnostics or refunding attempted requests.
3. Forced strict schema conversion made authored nonempty dictionaries impossible
   to satisfy. Authored output requests preserve constraints through non-strict
   provider mode and retain original host validation. Unsupported Anthropic
   native mode fails before admission/accounting; explicit tool mode is tested.
4. SDK reference inlining dropped refs under some applicators and weakened sibling
   assertions. The host now fully inlines closed frozen resources with Draft-aware
   traversal, preserves literals and intersections, bounds expansion and rejects
   reachable recursion/dynamic references before I/O. Unused recursive definitions
   do not prevent an otherwise valid schema from running.

Provider tests use actual async SDKs with MockTransport. They verify emitted
settings, zero SDK retries, no discovery requests, safe timeout/connection errors,
partial startup cleanup and explicit API flavors. FunctionModel tests exercise
native decision semantics, evidence, text/schema outputs, isolation, cancellation,
admission, deadlines, budgets and invalid/refused/truncated responses. The inbox
example uses real compilation and routing with deterministic offline model doubles.

The independent closing passes found no further actionable issue in these
bounded areas. Coordinating verification: 359 service tests, strict typing,
Ruff/format, seven service schemas, unchanged native schema snapshots,
documentation and skill checks. No model endpoint was contacted.

Still required: live-provider conformance when authorized, Bedrock credential and
worker lifecycle, MCP/OAuth tools, privacy-filtered OTel, complete service CLI/HTTP,
persisted operations/budgets, PostgreSQL/Redis recovery, child dispatch/join and
replica/failure testing. No production throughput or reliability claim is made.

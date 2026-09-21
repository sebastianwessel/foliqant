# Embedded runtime review

Scope: the nondurable async runner, attempt accounting, immutable execution
values, public result mapping and offline schema adapter. This extends the
foundation review; it does not qualify the full service or any model deployment.

An independent reviewer reproduced four issues and then verified their fixes:

1. An unexpected input-validator exception escaped the safe boundary. It now
   becomes a fixed dependency error without the original private message.
2. An invalid token report could break final accounting and discard an attempt.
   Reports are revalidated before storage; invalid reports leave the reserved
   request counted with unavailable token measurements.
3. Direct embedded identity construction accepted invalid control characters.
   One canonical core constraint now validates trusted identity and supplied
   claims and supplies the public envelope schema's identity constraints.
4. Cache/reasoning subset counts could exceed known input/output totals. Canonical
   token validation now rejects these reports, including at the public boundary.

The independent closing pass ran 89 focused tests and strict typing for the five
reviewed modules and found no further actionable issue in that scope. Subsequent
contract regressions also cover strict wire enum conversion, malformed core
reports and omission versus explicit null in public responses.

Runner tests demonstrate overlapping isolated executions, cancellation cleanup
and capacity release, original deadline enforcement, failed attempt accounting,
review-before-next routing, selected/skipped branches, explicit finish nulls,
and projection failure preserving completed steps and the accepted payload.
Schema tests prohibit socket/file retrieval during validation and retain frozen
resources despite source changes. The runnable example uses real compilation,
schema validation and execution with deterministic handlers only.

Coordinating verification: all 263 service tests, strict typing for 35 files,
lint/format, six service schema drift checks, 27 unchanged native schemas,
documentation links and skill structure checks passed. The example's documented
command also passed with uv offline. The added CI job has not run remotely.

No inference or model-discovery calls were made. Tests do not establish provider
integration, replica throughput, persistent budgets, mutation reconciliation,
database/broker recovery, MCP/OAuth behavior or end-to-end telemetry privacy.
Those remain separate required implementation and acceptance work.

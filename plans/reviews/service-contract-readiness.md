# Service contract and extraction readiness review

Date: 2026-09-21. Scope: the shared native-contract extraction and first service
contract slice in [specification 11](../../specs/11-workflow-service.md), reviewed
using `spec-readiness-review`. This is a bounded design review, not implementation
acceptance, full-service approval, or human approval of a manifest digest.
The owner has delegated implementation and routine decisions; this review does
not request another user confirmation.

Original design-review specification SHA-256:
`fe1b8463795b9906848c2d73c71fdc540cb11d7372bd16043556c6d2546a2883`.
Bounded envelope re-review specification SHA-256:
`551cad9ecabd83f98a62bb53689912c42bc672e6dd43d5c2e1896f49119027d9`.

## Outcome

The shared-package extraction is ready to implement. The revised first-slice
contract design resolves the earlier substantial gaps. The two output-shape
ambiguities identified in the original review have now been closed:

1. Singular `question` is unwrapped and uses the step ID. Explicit `questions`
   requires at least two members and retains the wrapper and authored IDs.
2. A successful finish has explicit `result: null`.

These are delegated specification clarifications, not authorization blockers.
They do not prevent work on the independent shared native-package extraction.

## Bounded envelope implementation review

The initial 21 envelope tests passed. Independent probes verified that accepted
payload and metadata recursively reject mutation, do not alias caller-owned
containers, and remain unchanged after a thawed copy is modified. Trusted tenant
and principal IDs are independently optional; unestablished and mismatched body
claims fail. Invalid incoming trace strings are deliberately preserved for the
future W3C propagation adapter rather than treated as authentication.

Initial review found that whitespace-only identity values passed generated JSON
Schema but failed runtime validation, and C1 controls passed both. The owner
corrected the identity constraint; independent probes now confirm schema/runtime
agreement for those cases and preservation of ordinary Unicode IDs. Invalid null
defaults were also removed from protected-field schema annotations.

One ingress requirement remains under implementation: raw JSON must reject
duplicate keys, non-finite numbers and excessive total size/depth, and validation
failures must become fixed safe errors. Direct Pydantic validation currently
exposes rejected input in its exceptions; its default JSON parser accepts
duplicate keys. Internal model construction must not become the public ingress
API. Verify the owner's bounded decoder with nested duplicate keys, byte/depth
boundaries, malformed JSON and PII sentinel failures before accepting that API.

The new async scaling section supplies the necessary design constraints. These
pure bounded validation helpers may remain synchronous. This review does not
establish async execution, admission, cancellation, trace propagation or durable
recovery behavior, and does not accept the full service.

## Bounded safe logging implementation

The separately assigned logging slice now provides fixed event codes and a
formatter that never renders messages, exception content, logger names or
arbitrary extras. Only startup-allowlisted nonsecret labels, bounded finite
measurements, typed error codes and valid trace/span IDs may pass. Debug changes
severity filtering, not data permissions. Third-party records become generic
external events.

Explicit process configuration replaces existing sinks and queues only sanitized
JSON strings for a managed stderr writer. The bounded queue drops overflowing
events and counts drops; sink failures are also counted without recursively
logging exceptions. A blocked sink does not block producers or retain original
records. Bounded shutdown returns false when the writer cannot drain; bootstrap
must check that result and keep blocking joins off the async event-loop thread.
Bootstrap must configure after SDK setup and prevent later uncontrolled handlers.
This does not sandbox Python code that deliberately replaces process logging.

Verification at this slice: 23 logging tests, 66 total service tests, strict mypy
for all 14 current service source files, and service source/test ruff checks pass.
Tests include hostile objects, nested extras, secret-bearing errors, production
and debug levels, sink failures, overflow counts, raw-record lifetime, event-loop
heartbeat during a blocked write and bounded shutdown. No exporter or provider
integration is implemented or approved by this evidence.

## Passed first-slice design checks

- Tagged pointer/literal bindings eliminate interpretation of a literal object's
  `from` key. Optional pointers require an explicit default; mandatory absence
  does not become null.
- Closed control shapes distinguish intentionally open JSON leaves. Metadata
  identity fields are independently optional, validated against trusted context,
  and cannot become authority merely by appearing in input.
- Cancelled steps are represented explicitly. Answerability is checked before
  `next`; unresolved/partial results hold for review. Multi-question branching
  cannot silently select the first answer.
- Configured MCP catalogs support offline compilation. Runtime discovery must
  verify schema digests before use and again on reconnection/resume.
- Frozen core JSON values and explicit validator facts preserve the boundary
  between standard-library core and Pydantic contracts. Service/native schema
  generator ownership and drift checks are stated.
- First-slice logging and observation boundaries cover third-party span names,
  status descriptions and exception events, not only message-content attributes.

## Exact extraction acceptance

Move the canonical native decision models, semantic validator, category catalog
and scalar definitions into `packages/decision-contracts/`; keep generation-only
settings in model tooling. Update consumers rather than retaining two validators.
The service must not import `foliqant_model` or training/runtime dependencies.

Before accepting the extraction, require byte-for-byte native schema snapshots,
unchanged native serialized examples and canonical artifact digests, equivalent
positive and adversarial semantic-validation outcomes, existing model-tooling
regressions, and independently installed shared/service package checks. A schema
that is merely structurally similar is insufficient. No compatibility alias,
validator weakening or digest bypass is an acceptable substitute. The initial
design review did not run those implementation checks. Subsequent extraction
verification reported 934 offline model tests, unchanged native schema bytes,
strict model typing/lint and isolated package builds. Package typing markers are
included and checked from the installed service environment.

## Async foundation follow-up

Independent review reproduced and the implementation fixed three concurrency
problems: expired ready waiters entering work, noninteger limits defeating
admission bounds, and executor tasks being created before capacity reservation.
Regression tests now cover those exact cases. Started blocking SDK calls retain
capacity after their caller stops waiting; cancelled queued calls never start.
Tests demonstrate independent worker overlap, task-local context isolation,
bounded shutdown, and safe handling of abandoned exceptions. Deep defensive
copying now also applies when an embedded caller constructs AcceptedEnvelope
directly, not only through the ingress mapper.

The 119 foundation/binding/logging tests pass. All 934 offline model tests pass
with seven native integration tests deselected; strict model/shared typing and
lint pass. No live model calls were made. Compiler revision/schema/authoring
findings now have regression fixes. Closing review found and the compiler owner
fixed unchecked referenced annotation schemas and unsafe depth-error mapping;
output checks now include implicit review exits. The coordinating agent reran
all 172 service tests, strict typing, lint, formatting and four schema checks.
These checks do not approve full runtime integration.

## Deliberately not approved by this review

The full service remains incomplete. Durable SQL and operation schemas,
reconciliation/correction/deletion commands, transport binding/error mappings,
OAuth integration, provider conformance, child recovery and production operations
must receive their own concrete contracts and acceptance evidence in their slices.
The revised retry-budget, effect-identity, authorization, poison-message and
telemetry requirements are useful constraints, not evidence those paths work.
Registry/generation-map alignment is owned by the coordinating author and must
track the actual package extraction and new service representations.

## Verification and limits

`node /Users/sebastianwessel/.agents/skills/spec-readiness-review/scripts/check_specs.mjs specs`
passed. This is a structural smoke check, not semantic or production approval.
This reviewer changed no source code, runtime configuration, dependencies or
artifacts and invoked no model endpoints. The referenced skill's `checklist-secrets-privacy.md`
file is absent; privacy was assessed with the available security checklist and
the previously fetched official MCP/OTel SDK documentation.

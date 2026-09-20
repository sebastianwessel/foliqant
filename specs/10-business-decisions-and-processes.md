# Evidence-backed business decisions and process branching

Date: 2026-09-20. Status: target design recorded from the owner's accepted
direction. Additive category authoring and normalization are implemented;
the broader process design is not implemented, benchmarked, or formally
readiness-approved.

This extension covers banking, funds, insurance and public-sector correspondence
and documents. It does not replace the implemented
[native V1 data contract](09-native-decision-data.md), change existing artifacts,
or authorize changes to a running generator. Beyond the additive category
authoring schema, new decision wire schemas, training records and workflow
execution require a separately reviewed implementation slice.

## Authority and scope

REQ-BUSINESS-DECISIONS / CAP-BUSINESS-DECISIONS records typed observations,
evidence and thread interpretation. REQ-PROCESS-BRANCHING /
CAP-PROCESS-BRANCHING records one process with multiple bounded child tasks.
REQ-DOMAIN-DATA / CAP-DOMAIN-DATA records candidate-source assessment, not source
acquisition. Decision authority is DEC-BUSINESS-PROCESS-CONCEPT.

The model interprets supplied evidence against caller-defined questions and
catalogs. Deterministic code validates results, applies the configured process,
checks authorization and controls side effects. A statistically correct intent
does not authorize a transaction, claims settlement or administrative decision.
Current policy/product facts come from versioned supplied sources, not model memory.

Reuse existing `choice`, `multiselect`, `predicate`, `ordinal`, `request_units`,
answerability issues, concise explanations and allowed-source checks. Do not
introduce a second classification taxonomy or encode workflow node names in
training labels. Most catalog/process changes must remain configuration changes.
Ordinary structured-output inference remains the serving boundary; no custom
model head, consumer library, provider SDK or new runtime dependency is required
by this concept.

## Classification, alternatives and request instances

| Question | Meaning | Boundary |
| --- | --- | --- |
| `choice` | One supported option under explicit criteria | Exactly-one cardinality does not justify forcing a winner. Preserve a null answer and existing issues when unresolved. |
| `multiselect` | All independently applicable options within declared cardinality | Multiple confirmed labels are different from alternative guesses about one intent. A permitted empty set differs from missing evidence. |
| `request_units` | Every distinct requested item and its state/relations | Two statement requests for different periods remain two units even though the category is identical. |
| `predicate` / `ordinal` | Existing bounded proposition/rubric | Missing evidence is not false, and model scores do not supply business policy. |

Use existing option descriptions and question criteria for inclusion, exclusion,
scope and boundary examples. No new taxonomy DSL is needed. A single primary
queue may be chosen deterministically from several confirmed requests without
discarding secondary work. A model uncertainty assessment is distinct from a
claim that information is absent from the customer's input.

### Category catalogs and support decisions

Every category has a caller-owned machine `id` and a required nonblank
`description`. Describe what qualifies, what does not, and how adjacent categories
differ; the identifier alone is not its definition. New authored category keys
use ASCII lowercase snake_case: `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`.
Normalize recoverable formatting before inference, including capitalization,
spaces and hyphens: `Request Info` and `request-info` both become `request_info`.
Reject collisions after normalization instead of merging categories or choosing
a winner. Reject an empty or otherwise unrepresentable normalized key. Do not
translate category meaning or fuzzy-match names. External taxonomies need a
recorded, versioned, collision-checked mapping.
The initial normalizer accepts at most 128 printable ASCII characters, lowercases
letters, collapses punctuation/space runs to `_`, and strips outer separators.
The canonical result starts with a letter. Non-ASCII letters and controls require
an explicit caller mapping rather than lossy automatic deletion. JSON Schema
describes raw input; runtime validation owns transformation and collision checks.
Descriptions may be English or German; machine keys remain stable across languages.

Return the supplied key exactly and retain the existing answerability,
explanation and evidence. Applications obtain the category description from the
same caller-owned catalog; model-generated repetitions are not authoritative.
Validate output membership against the exact question catalog before routing.
The catalog may resolve formatting variants in returned identifiers through the
same deterministic normalization and exact membership check. This cannot recover
an unknown or semantically incorrect category. Persist canonical keys from the
start of a new task rather than changing immutable historical data.
Evidence for each selected label is still the future extension described below;
current V1 classification explanations are attached to the question result.

Keep support dimensions as separate questions: request kind (`incident`,
`information_request`, `confirmation`), topic/queue, and priority under a supplied
rubric. A confirmation can coexist with a new request in one message. Do not
infer that these label facets are independent tasks. A priority label is not a
deadline or authorization to act. Literal due-date extraction requires a source
span; resolving "tomorrow" requires trusted reference time and timezone.
Computing an SLA deadline is deterministic policy, not a model target or an
invented date. Missing date evidence stays missing.

The catalog-authoring validation is additive to V1 tooling. Frozen V1 artifacts,
generation schemas and recipe identities remain unchanged so their completion,
repair and extension can be verified. Strict catalog authoring must not be
misrepresented as a retrospective new restriction on every historical V1 ID.

Keep the existing English issue enums: `missing_information`,
`conflicting_information`, `multiple_valid_options`, `no_matching_option`.
`multiple_valid_options` applies when positively supported options exceed the
task's cardinality; it is not an error merely to have two valid multiselect
labels. Two possible referents for "cancel it" must not become two executable
requests. Preserve unresolved alternatives without marking them confirmed.

## Evidence and explanations

Bind supporting evidence to each selected label, request unit and extracted
value. Preserve result-level contrary evidence and missing facts where they
concern the overall answer. Avoid parallel answer/evidence collections that
can disagree or untyped JSON Pointer attachment schemes.

The semantic model output supplies a source ID and exact quotation. Host code
resolves an optional locator against an immutable source revision. The target
locator semantics are zero-based Unicode code-point offsets with half-open
`[start,end)` bounds. It must satisfy `source.text[start:end] == quote`.
Implementation must supply explicit coordinate conversion for UTF-16/UTF-8
consumers and tests covering German, emoji, combining marks and line endings.

Freeze text after declared ingestion transformations, before assigning source
revision/digest and offsets. Do not normalize or redact it afterward in place.
Redaction or re-extraction creates a new revision with a recorded mapping when
one exists. Evidence belongs to one message/document source, not a concatenated
thread whose offsets move when messages are added. PDF-page/table-cell rendering
is adapter-owned and must not pretend parsed-text offsets address original bytes.

When a quotation has multiple matches, require distinguishing context or an
explicit validated occurrence. Never silently take the first match. A resolver
may report an unresolved locator without fabricating one; a workflow requiring
precise location must reject automatic use. Multiple citations express separated
evidence spans. Literal occurrence is a deterministic integrity check, not proof
of relevance, entailment, completeness or truth.

Keep short evidence-backed justifications using the existing 400-character
summary cap and approximately 160-character target. Do not train invented hidden
reasoning as gold. Synthetic summaries retain synthetic/teacher provenance;
source labels and silver annotations do not become human review.

## Ordered thread snapshots and changing intent

Ingestion owns stable source identity, recorded ordering, observed actor/role,
reply/quote linkage when known, and source revision. Sender text or a claimed
timestamp is not authenticated identity or authority. Preserve unknown metadata
instead of guessing chronology or treating a display name as a trusted actor.

Each analysis binds to a complete supplied snapshot and declared decision-time
boundary. Missing attachments, extraction failures or known truncation are
explicit input conditions, not invisible omissions. Historical evaluation may
not include facts learned after that boundary. The model may interpret changes
within the permitted snapshot; it cannot fetch new sources or redefine scope.

Reuse `active`, `withdrawn`, `conditional`, `quoted` request states and existing
relations. A later withdrawal changes only the unambiguously referenced request;
quoted old instructions do not reactivate it. A new condition remains declarative
until deterministic policy resolves it. A correction that could refer to two
requests remains unresolved. "Latest message wins" is not a general policy.
Claims in correspondence about completed actions remain distinct from trusted
business-system completion records.

Example: message 1 requests cancellation of a transfer and a March statement;
message 2 says to keep the transfer but still send the statement. The cancellation
unit is withdrawn; the statement unit remains active. Evidence includes the
original request and the status-changing message. An unrelated later message
does not erase either result.

Model-produced unit IDs are references within one response, not durable action
or idempotency keys. Host code owns persistent request identity and reconciliation
across snapshots; an ambiguous match requires review. Existing schema prose
about caller-authored expected IDs concerns training fixtures, not a requirement
to know every request instance before runtime extraction.

## Bounded extraction

Add one proposed `extract` task after thread/evidence contracts are settled.
One existing question ID identifies one caller-defined field, its description,
allowed sources and cardinality. Several fields can share one analysis call;
do not create an additional arbitrary nested extraction-schema language.

Return literal values with per-value evidence, using the existing answerability
and issue vocabulary. Required-but-missing, competing values and explicit absence
are distinct. A permitted empty collection must satisfy the declared field
semantics; it must not hide a failed extraction or unreadable attachment.

Keep literal extraction separate from deterministic normalization and arithmetic.
Any normalized value records its named normalizer/version and supporting raw
value. Ambiguous locale/date/currency interpretation fails or requests context;
it must not silently choose. Deriving a ratio, interpreting legal applicability
or inferring an intent is not literal extraction.

## One parent process with bounded child tasks

The accepted use case is one process instance that can create multiple tasks or
subflows from multiple confirmed requests. It is not one independent top-level
process per label and does not let the model generate a workflow graph.

| Parent case | Confirmed units | Configured work |
| --- | --- | --- |
| Banking/funds correspondence | Redeem specified units; send a statement | One case, redemption-review task plus document-delivery task; execution prerequisites remain independent. |
| Insurance correspondence | Report a claim; update a contact address | One case with claim-intake and address-change tasks, each separately authorized. |
| Citizen correspondence | Change an address; request a certificate | One case with two configured administrative tasks; dependencies follow supplied process rules. |

Sequence: snapshot -> typed analysis -> deterministic validation -> configured
task plan -> persisted child identities -> bounded dispatch -> configured join.
Branch selection maps confirmed request instances to an allowlisted, versioned
task/subflow definition. The model cannot name arbitrary handlers, endpoints or
scripts. Unmatched units enter the configured review path; none disappear.
Several labels may be facets of one request rather than several obligations.
Task count follows the configured mapping of request instances, not label count:
one request may need several configured tasks, while several requests may share
an explicitly configured prerequisite. Default to holding the task plan when
analysis is incomplete or partially answerable. Dispatch of resolved units alone
requires explicit policy establishing their independence from unresolved work;
the parent still cannot report complete success while required work is unresolved.

Persist the inbound request before acknowledging durable intake; analysis may
follow asynchronously. Persist the complete accepted child set and dispatch
intents atomically before dispatching child work. Separate the revision-specific planning
record from a host-owned business obligation/action identity that is stable across
plan revisions and bound to the tenant/parent process. Retries and unchanged work
in a later revision reuse the same action identity; changing a plan or task code
version must not silently create permission to repeat an external action.
Bind the action identity to its approved input; changed inputs require explicit
reconciliation, not reuse with a different payload. Two same-category requests
may produce two children. Combining
them is allowed only by explicit configuration that preserves both obligations.

Configuration declares finite child-count/depth/concurrency bounds, dependency
edges and a join rule. No recursive subflow expansion or implicit unbounded loop
is permitted in the initial design. Exceeding a bound rejects/reviews the plan;
do not truncate it. Parallel execution requires explicitly independent work;
shared-entity conflicts, prerequisites and declared ordering require serialization.
Two mutually exclusive or unresolved conditional alternatives must not both run.

The initial proposed join waits for every required child in the effective,
accepted plan to succeed. A failure, pending review or unfinished required child
prevents a successful parent result. Optional
children, partial success or an alternative join require explicit future policy;
they are not inferred from whichever child finishes first. The parent retains all
child outcomes and an audit reason derived from executed rules, not model prose.

A failing child does not automatically roll back a successful sibling. Recovery
retries only permitted incomplete/retryable work, with bounded retries and
deduplication; external actions may require an idempotent adapter. Crash after
dispatch/before acknowledgment must be tested with outbox/redelivery semantics.
No exactly-once end-to-end or transaction-wide atomicity claim is made.

A later email creates a new analysis/plan revision. Reconciliation checks the
entire parent action history before dispatch: unchanged completed obligations
stay satisfied, never replayed. Unstarted withdrawn/superseded work leaves the
effective required set only through an auditable accepted revision; its old
planning record and cancellation/supersession outcome remain in history. It is
not falsely reported as executed successfully, nor left permanently blocking
the new join. If no required work remains, record closure without further action
rather than inventing successful child executions. Prevent a stale dispatcher
from starting retired work by checking the accepted plan/action state at claim
and dispatch boundaries. In-flight or uncertain external delivery is not safely
cancelled just because local state changed. In-flight/completed changes require the
configured cancellation, compensation or review path. Automatic compensation is
out of initial scope. Authorization and tenant isolation apply to every child.

## Confidence, speed and qualification

Keep input answerability, whole-answer correctness/completeness and action
eligibility separate. No model-written confidence percentage enters native gold.
A future runtime assessment may attach separately calibrated numeric estimates
only with a versioned validated profile; otherwise the estimate is unavailable.
A reliable score for one label does not establish that no other request was missed.

Profile binding covers model/quantization, prompt, task/catalog, preprocessing,
language and deployment distribution. Assess error among automated cases together
with automation coverage, sample counts and uncertainty. A confidence threshold
cannot override invalid evidence, missing prerequisites or failed authorization.

Start with one bounded structured analysis call plus deterministic checks; compare
additional assessment calls against their latency/error benefit before requiring
them. Evaluate p50/p95 latency and cost alongside omission and wrong-action rates.
No numerical service-level target or production accuracy is claimed by this spec.

## Data and acceptance

[Broader-domain dataset research](research/business-process-datasets.md) is a
candidate inventory, not an executable download manifest. Pin and review each
source's actual data terms, annotation origins and proposed mapping before import.
Code licenses do not license underlying correspondence or documents. Private
research does not override restrictions. Do not use held-out/test evidence as
training supervision. Keep original annotations and source rights through all
derived records; preserve research-only/commercial restrictions in model ancestry.

Training should include category-order permutations, new catalogs, boundary
examples, confirmed multiplicity, ambiguous alternatives, same-category request
instances, corrections, withdrawals, quotes and conditional relationships.
Whole threads, temporal prefixes, paraphrases, translations and counterfactuals
share a leakage family. English and German retain their prose and English enums;
neither translated benchmarks nor synthetic data establish deployment accuracy.

Automatic data generation remains diagnostic. Human-adjudicated representative
qualification examples are a separate evaluation activity, not a new manual step
required to operate the existing generation command.

| Acceptance ID | Required success, failure and recovery evidence |
| --- | --- |
| ACCEPT-BUSINESS-DECISIONS | Correct single vs multiple labels; two same-category units; ambiguous reference never becomes two actions; label/value evidence binding; repeated-quote rejection; Unicode offsets; missing/contradicting fields; later selective withdrawal; quoted-history exclusion; snapshot changes invalidate stale locators; no self-reported confidence or false completeness claim. |
| ACCEPT-PROCESS-BRANCHING | Two confirmed units create one parent and two configured children; both represented at join; failed child blocks success; alternatives never both dispatch; partial analysis holds by default and only an explicit independent-partial policy permits dispatch; bounds reject without truncation; shared-entity ordering; each child authorized; crash/redelivery does not duplicate a completed business action; a later correction suppresses unchanged successful work and retires only safely unstarted withdrawn work from the effective join; stale dispatch cannot revive retired work. |
| ACCEPT-DOMAIN-DATA | Primary-source evidence, language/task/annotation fit, access and data-license status; no acquisition by research; no code-license substitution, test leakage, invented gold rationale or native/model endpoint calls during offline preparation. |

Report exact label sets and per-label precision/recall, any-request omission,
unit/status/relationship correctness, extraction exactness, citation occurrence
and semantic support separately, and route correctness. Include insufficient-
context, language/domain, rare-class and full-thread slices. Default unit tests
can establish mechanics, not these model-quality acceptance claims.

## Implementation boundaries and readiness gaps

The broader authoring mandate covers this spec/concept and research. The owner
also authorized the additive category catalog, key normalizer, generated schema
and their tests/guides. No new endpoint, loader, workflow node, decision schema
version or data download is implemented.
`service/src/core` remains proposed execution ownership; adapters own transports,
document parsing, persistence and delivery. The core must not import model-training
libraries or provider/transport clients. Reuse language-neutral generated schemas
at the model/service boundary rather than sharing Python runtime classes with Go.
Existing curation and model lifecycle interfaces remain unchanged; category
authoring is an additional Python contract and exported schema.

Before implementation, freeze the next typed wire schema and generation map,
thread metadata trust rules, process configuration/child state contracts,
durable storage/acknowledgment contract, allowed reconciliation/cancellation
behavior, finite budgets and calibration targets. Those are explicit review
items, not permission for an implementation agent to invent defaults. No new UI,
hosted auth surface or cloud deployment is part of this authoring slice; future
execution still requires authenticated ingress, tenant isolation, bounded inputs,
redacted telemetry, retention/deletion rules and independently reviewed releases.

Adopt versioned new training records/contracts in a separate run. Never relabel
V1 records, migrate an active run in place, or invalidate its request cache by
changing prompts during generation. The next implementation order is evidence
and thread context, targeted evaluation, bounded extraction, then qualified
process branching and confidence assessments. The process contract should be
designed now so model outputs stay observations rather than executable plans.

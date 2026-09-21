# Native decision-data generation

Status: **implemented pipeline; bounded generated-data quality repair accepted**.
The final 35-job verification, independent accepted-output review, artifact
membership and lineage checks, runtime fingerprint, public verification and
immutable resume are recorded in
[`plans/reviews/generated-data-quality-repair-pilot.md`](../plans/reviews/generated-data-quality-repair-pilot.md)
and
[`plans/reviews/generated-data-quality-repair-review.md`](../plans/reviews/generated-data-quality-repair-review.md).
The earlier fixed-snapshot audit remains evidence about its named old run; those
rows remain diagnostic and are not promoted by this acceptance. The accepted
evidence establishes the corrected bounded pipeline and final research artifact,
not formal human digest approval, full-recipe coverage, model quality, training
readiness, native MLX training or production certification. This specification
turns the recommendations in
[input answerability and reliability](research/input-answerability-and-reliability.md)
into a bounded data-generation contract. It extends, and does not replace,
[automated data curation](08-automated-data-curation.md).
The scoped source-projection extension below is implemented and verified through
offline tests. Its planned live pilot has not run, so this status carries no
generated-data quality or model-validation acceptance claim.

The completed-run rejection investigation led to a new recipe with phase-specific
recovery, review-required semantic disagreement, explicit source category
definitions and relation-preserving WANLI tasks. The earlier acceptance applies
only to its named recipe/artifacts; it does not qualify this revision. Current
tests and the diagnostic pilot are recorded separately in
[the recovery verification report](../plans/reviews/curation-recovery-source-quality.md).

## Required outcome

One local command prepares a native Foliqant decision corpus without manual
labeling or training:

```text
./scripts/generate-data [--pilot] [--prepare-only] [--offline]
  [--workspace <path>] [--config <path>] [--progress auto|always|never]
```

Without `--pilot`, the wrapper selects the committed
`model/examples/native-full.yaml` recipe. `--pilot` selects the independently
identified `model/examples/native-pilot.yaml` recipe. `--pilot` and an explicit
`--config` are mutually exclusive; `--config` selects that recipe instead. The wrapper delegates to the
existing `foliqant-model curate` boundary; it must not implement a second
curation engine. Repeating the same command resumes the same immutable run.

The full recipe selects all five sources with caps of 2,000 records each,
`maxCandidates=10000`, `maxAttempts=2`, English and German, a 32,000-character input cap,
and endpoint limits of 8,192 output tokens and 300 seconds. Native full and pilot
recipes explicitly select standalone Splash on loopback port 8000, model
`incoai/Qwen3.8-27B-Splash`, `reasoningEffort=low`, and `temperature=0.1`.
Root environment overrides select the user's actual local server. Reasoning
remains enabled; no automatic effort escalation or model fallback is added.
It uses
`examplesPerScenario=4`, `sourceExamplesPerSource=500`, and
`minimumAcceptedPerCell=1`. The pilot uses its own name/run identity, source caps
of 100, `maxCandidates=16`, `maxAttempts=1`, four examples per scenario, eight
source examples per source, and no per-cell minimum; at least one accepted
candidate job is still required for successful generation. Both remain serial.
The 2,000-record cap keeps the resolved configuration within the existing 1 MiB
configuration limit; this change does not raise any parser or safety bound.
Historical prepare-only evidence for the pre-repair full recipe produced 9,600 auxiliary
source records, 4,200 native seed parents and 3,215 training jobs across 34
coverage cells, with at least 72 jobs per cell and a reported maximum of 11,550
endpoint calls. These are recipe-sizing observations, not quality or completion
claims, and the repaired recipe makes no promise to retain those counts.

The full run acquires and freezes the five pinned public sources, publishes the
raw normalized `source-corpus` as auxiliary research data, creates deterministic
native decision seeds, generates only training-partition variants through the
configured local endpoint, and publishes `native-decisions` plus a coverage
report. It does not train a model. `--prepare-only` performs acquisition,
conversion, compatible projection, native seed publication and partition planning without inference.
`--offline` forbids missing remote downloads but does not disable explicitly
configured local inference. Initial generation concurrency is one.

The native corpus is unreviewed research data. Only rows admitted by the
publication rules below may enter a training partition; all other prepared rows
remain diagnostic. Authored oracle targets, source annotations, a blind model check, or automatic acceptance
must never be described as human-gold evidence, calibrated answerability,
production fitness, or permission to automate a financial action.

### Scoped offline source-projection extension

The default native recipes retain the compatibility policy documented below.
An operator may separately derive a versioned projection plan from the already
frozen source snapshots and splits of an immutable run:

```text
./scripts/prepare-source-projections --from-run /absolute/path/to/run [--pilot]
  [--output /absolute/path/to/new-projection-plan]
foliqant-model prepare-source-projections --from-run /absolute/path/to/run [--pilot]
  [--output /absolute/path/to/new-projection-plan]
```

This command is offline by construction. It may run while generation is active
once the required source snapshots, families and splits are frozen. It makes zero model requests,
including endpoint model discovery, performs no source or dependency download,
and does not alter the source run. It fails closed unless every required source
snapshot, family assignment, split and integrity envelope can be reused exactly.
Its output is a new immutable plan directory outside the checkout. Explicit
selected, projected and excluded counts bind the plan to its inputs and rules.
`--pilot` selects exactly 32 training projection tasks: eight MultiDoGO tasks,
eight TAT-QA tasks and 16 typed-decisions tasks, with four typed records from
each workflow. A plan without `--pilot` is the separate full selection from the
same parent inputs.

The plan may be applied only through the paired extension flags:

```text
./scripts/generate-data
  --extend-projections-from /absolute/path/to/completed-parent-run
  --projection-plan /absolute/path/to/projection-plan
  --progress always
```

`foliqant-model curate` accepts the same paired options. Both are required
together and are mutually exclusive with `--continue-from` and `--repair-from`.
The parent must be complete. `--prepare-only` is permitted and creates/verifies
the child plan and artifacts without model discovery or inference. A full
extension creates only the new blind-verification jobs after the parent has
finished. It preserves every prior outcome byte-for-byte, publishes through a
new immutable child run, and never advances or rewrites the parent. Repeating
the exact extension command resumes that child.

The first bounded execution uses the 32-task pilot plan with the normal
extension command above after parent completion. The pilot plan itself may be
prepared while that parent is still generating. A later full plan is prepared
separately from that same parent. The full extension makes no promise to reuse
pilot model calls or outcomes. Model validation of both modes is deferred; no
projection-extension pilot has run or been accepted yet.

Only an explicit prepared plan enables the additional mappings. An ordinary
`generate-data`, pilot, continuation or repair retains the legacy projection
set and does not silently acquire new projection behavior. Plan preparation
does not train, translate, augment or call a model.

The implementation entrypoint is
`run_decision_curation(config: CurationConfig, workspace: Path | None, *,
prepare_only: bool = False, offline: bool = False) -> CurateResult`.
`build_decision_jobs` deterministically balances scenario/language training
cells. The bilingual 16-job pilot uses a stable sorted round-robin priority that covers
all five question types in each language plus partial-answerability, conditional-relation and
whole-answer-adequacy cases before remaining jobs. Native work reuses
`CandidateJob` and `CandidateOutcome`; a native job
has `purpose="decision-training"` and `operation="native-decision"`. No new CLI
command or result union is introduced. Public native wire roots are the strict
Pydantic `DecisionInput` and `DecisionOutput`, with generated JSON Schemas.

## Configuration and run identity

`CurationConfig` adds optional `decisionData: DecisionDataSettings`. Its
presence selects native decision generation; omission selects the existing
curation behavior. Existing immutable artifacts are not modified. Because the
resolved configuration schema changes, new runs may receive a new identity;
this extension makes no backward-identity claim.

`DecisionDataSettings` is closed and has exactly these bounded fields. The
quality-repair recipe replaces the misleading pre-repair
`familiesPerScenario` field with `examplesPerScenario`; the old field is
rejected rather than aliased because recipe and run identity must change.

| Field | Type and default | Meaning |
|---|---|---|
| `examplesPerScenario` | integer `4..10000`, default `4` | Upper bound on examples selected from the finite genuine-case catalog for each configured native scenario. It is not a requested-output guarantee or independent-family claim. |
| `sourceExamplesPerSource` | integer `0..100000`, default `100` | Maximum compatible native seeds projected from each pinned auxiliary source. Zero disables projection without disabling acquisition or reporting. |
| `minimumAcceptedPerCell` | integer `0..1000`, default `1` | Required accepted candidate jobs for every eligible planned `(scenario, language)` training cell: generated derivatives for authored rewrite cells and verified source parents for projection cells. |

Existing endpoint configuration and generation bounds (`maxCandidates`,
`maxAttempts`, languages, input character limit, timeout, token and byte limits)
are reused. Full and pilot recipe bytes, resolved configuration, source catalog,
source assets, authored seed recipe, scenario/template version,
generation/check schemas and prompt
recipe digest determine distinct run and job identities. A changed model
identity, recipe, source conversion, question contract or prompt cannot reuse a
persisted outcome. Immutable request caches, serial execution, locks, private
files, safe failures and resume rules remain those of specification 08.

The wrapper forwards `--progress auto|always|never` to the curation CLI. Progress
is a stderr-only operator view with phase, run path, completed/total candidates,
accepted, quarantined, reused outcomes, request-cache entries present when
generation starts, current-candidate elapsed time and heartbeats; it does not
estimate completion time. The final JSON remains the sole stdout output. `auto`
is the default and renders only when stderr is a TTY. This display setting does
not change run identity.

The authored recipe supports English and German through finite, explicit prose
catalogs over the same typed scenario logic. Every source, question, criterion,
option description, reference explanation, missing fact and request description
must be localized; a missing catalog entry fails closed. Machine field names,
IDs, plain-English enum values, numerical answers and relationships remain unchanged. Quoted subjects
and citations must reference the localized source exactly. Localized citations
may use the full localized source when the original substring has no explicit
translation, subject to the same evidence validation as other references.

English/German siblings share one semantic family and therefore one partition;
translation must never create train/held-out leakage. Both language catalogs and
explicit output-language instructions participate in recipe identity. Generation
preserves each parent record's language; German reference summaries, missing
facts and request descriptions remain German in published response records.
English source projections remain English, rather than being relabeled as
German. Language tags are declared provenance, not automatic language detection.

The full preset includes both languages. The bounded pilot includes eight
priority scenarios per language (16 jobs), covering all five response types.
Separate real German acceptance must check source and response language as well
as schema, evidence, semantics, coverage and immutable resume. Lexical guards
recognize supported English/German negation, numeric comparison, date, unit and
quote forms conservatively; they are not a universal multilingual entailment
system. Do not promote unverified languages by adding a language code alone.

The first Ctrl+C requests a graceful pause: generation persists the current
candidate before starting no further candidate, while preparation stops at the
next safe phase boundary. The command releases its lock and returns the existing
`INTERRUPTED` error with exit code 130, without inventing a paused-success schema.
A second Ctrl+C stops immediately, so the current request can repeat on resume
and server-side work may continue. Resume is the existing operation: rerun the
exact same recipe, effective environment and workspace, with no `--resume` flag.
Existing candidate outcomes and request-cache formats do not change.
The shared runner's bounded local pause/resume verification is recorded in
[`plans/reviews/curation-progress-pause.md`](../plans/reviews/curation-progress-pause.md).

## Canonical native example

The published boundary remains the existing strict `DataRecord`. A native
record's user message contains one canonical native task object and its final
assistant message contains one canonical native result object. Normalization,
source rights, `familyId`, generation lineage, origin and `reviewed=false` use
the existing contracts. Native task/result contracts are closed; unknown keys
and explicit nulls are rejected unless a field below explicitly permits null.

### State and source boundary

`state.sources` is a nonempty ordered list of objects with exactly:

- `id`: caller/source-stable nonempty ID, unique within the state;
- `kind`: one of `message`, `document`, `table`, `policy`, or `metadata`;
- `text`: nonempty supplied content.

Every question declares `allowedSourceIds`, a unique nonempty subset of the
state source IDs. Caller order is retained and need not be sorted. Evidence
outside that set cannot support its answer.
Source text is untrusted content, never an instruction that changes the
question, criteria, output contract, or generation policy. A source said to be
missing is represented by an issue, never fabricated text.

Authored state may contain `kind=metadata` only when the metadata is relevant to
at least one question or immutable provenance. Metadata that is not relevant to
an answer is never added merely to make records look different, is never added
to `allowedSourceIds`, and does not establish a distinct content family. Every
metadata source is copied byte-for-byte through generation. If an answer-bearing
date, number, currency, unit or identifier appears in another source kind, a
rewrite may change formatting but must preserve the normalized value and its
role. For example, `2026-04-28` and `April 28, 2026` are the same date; their
different numeric tokenization is not a changed fact. The guard still rejects a
changed value, unit, currency, identifier, negation or comparison.

Unit preservation canonicalizes English `per month`/`monthly` and German
`pro Monat`/`je Monat`/`monatlich` to the same `month-rate` token. Bare `month`,
`months` and German `Monat` inflections remain separate duration tokens. Thus `EUR 20 per month` may become `a monthly charge of EUR 20` when the
same amount/currency association is preserved, while `in one month` becoming
`monthly`, or `monthly` becoming `daily` or `annual`, fails. This bounded lexical
alias prevents a false unit-change rejection; it does not by itself establish
that two source sentences are semantically equivalent.

The preservation checks are intentionally conservative lexical guards, not a
general entailment system. A fact-preserving paraphrase can therefore be
quarantined when its surface markers differ. That rejection alone does not prove
that the model was wrong or that a fact changed. Review the immutable source,
candidate and rejection evidence; do not edit cached records or bypass the
validator. A newly supported equivalence requires a versioned guard rule,
focused regressions and a new recipe identity.

The generic lexical guards recognize English `cannot`, `can not`, `unable`, and negative
auxiliary contractions with straight or curly apostrophes as negation markers.
They do not match substrings of unrelated words. Marker counts are only a
rejection signal: equal counts do not prove that negation still applies to the
same claim. Percentage symbols are recognized both adjacent to a number and
separated by whitespace, and remain equivalent to `percent`/`prozent` markers;
removing a percentage marker or changing it to basis points is rejected.
Numeric-bound inclusive comparisons recognize `or higher`, `or above`, and
`or more` as `at least`/`>=`, and `or lower`, `or below`, `or less`, and `or fewer`
as `at most`/`<=`. Numeric-bound strict comparisons recognize `exceed`/`exceeds`
and `above` as `greater than`/`>`, and `below` as `less than`/`<`. Incidental
wording without a numeric operand does not match. Strict/inclusive boundaries,
comparison direction, and negation remain distinct. This marker equivalence
does not infer or check which entity is being compared.
German numeric-bound strict comparison aliases include `über`/`unter`,
`mehr als`/`weniger als` and supported `überschreiten`/`unterschreiten` forms;
inclusive suffixes include `oder mehr`/`oder weniger` and corresponding
higher/lower forms. German negation and Tag/Monat/Jahr/Basispunkt inflections
retain marker counts. `pro Monat`/`je Monat` and `monatlich` share a rate marker,
not a duration marker. Exact valid `DD.MM.YYYY` dates map to the same date as
written month and ISO forms. Decimal separator changes remain rejected without
locale inference. Quoted content inside `„…“` and `»…«` is preserved exactly;
quote glyph choice is not an anchor fact. Guard identity is versioned (v10).

Before blind solving, at least one selected source must have a different
sequence of Unicode word tokens, compared case-insensitively. Exact copies and
whitespace-, punctuation-, or casing-only edits fail with
`rewrite-no-wording-change`. This is a bounded no-op check, not proof of useful
diversity: changed tokens, word reordering or filler do not establish a new fact
or independent semantic family. Frozen-source and fact-preservation checks run
first and remain mandatory, including during persisted candidate revalidation.

Metadata kind alone never determines relevance. Metadata named by any
question's `allowedSourceIds` remains fully answer-bearing for task identity and
diversity checks, including its exact source ID, kind and text. Preparation may
ignore a state source for diversity equivalence only when no question can use
it: its ID is absent from the union of all `allowedSourceIds`. This rule applies
to every source kind and makes no claim about the meaning of allowed prose.

Authored examples must keep financial dates, periods, currencies, units,
denominators, jurisdiction and applicability dates explicit when they affect an
answer. “The document states X” and “X applies in this jurisdiction and period”
remain different questions.

### Typed questions

New caller-authored category catalogs use the additive `CategoryCatalog`
contract in `foliqant_model.curation.category_catalog` and its generated
`category-catalog.schema.json`: IDs deterministically normalized to unique
lowercase snake_case keys, collisions rejected, and nonblank
descriptions. `decision_options()` converts these definitions to the existing
option representation without changing text. This authoring boundary preserves
V1 message schemas, historical source IDs and frozen recipe identities; it does
not retroactively rename existing category answers. Detailed catalog and support
task semantics are recorded in
[the business decision concept](10-business-decisions-and-processes.md#category-catalogs-and-support-decisions).

Each question has a caller-stable `id`, `type`, nonempty `prompt`, a nonempty
unique list of `criteria` strings, and `allowedSourceIds`. Type-specific fields
and typed answer objects are:

| Type | Question contract | Answer contract |
|---|---|---|
| `choice` | At least two stable option IDs with caller definitions; cardinality is exactly one. The caller explicitly includes no-match/unknown options when permitted. | `ChoiceAnswer {optionId}` using one supplied option ID. No nearest-option fallback. |
| `multiselect` | Stable option IDs and definitions plus `minSelections` and `maxSelections`. Options are independently applicable; their scores are not normalized into one categorical distribution. | `MultiselectAnswer {optionIds}` with unique supplied IDs satisfying cardinality. |
| `predicate` | A caller-defined proposition and criteria for supported true, supported false and unknown. Presence predicates must say whether they ask about an item in the world or whether allowed evidence explicitly contains or denies the item. | `PredicateAnswer {value}` where value is `true`, `false`, or `unknown`; absent evidence never silently becomes false. An explicit allowed-source statement that the asked-for item is absent supports `false` and is answerable. `unknown` applies only when neither the proposition nor its negation is established. |
| `ordinal` | Stable ordered levels with an ID and description for every level and facts required to distinguish adjacent levels. | `OrdinalAnswer {levelId}` using one supplied level ID; an unresolved result has a null answer plus issues. |
| `request_units` | A catalog, `allowNoMatch`, decomposition criteria and allowed relationships. | `RequestUnitsAnswer {units, relations}` preserving every distinct unit, including repeated categories, relationships and status. |

A request unit has exactly a stable `id`, status (`active`, `withdrawn`,
`conditional`, or `quoted`), nullable `categoryId`, required nullable `subject`,
nonempty description and supporting evidence. A non-null `subject` is a
nonempty, case-sensitive literal substring of at least one of the unit's
evidence quotes. Authored examples explicitly delimit the exact target anchor in
the source and criteria; that complete literal is the canonical subject and is
compared exactly. For other inputs it is the shortest complete span that distinguishes that unit
from another possible unit: an explicit account, transaction, period, date,
amount with currency, or other request-specific reference. Thus an explicit
`EUR 125` charge supplies subject `EUR 125`; generic nouns such as `fee`,
`statement`, or `account` alone do not. An action name or document/product type
alone remains generic even when qualified by its purpose or subtype. For example,
`warranty certificate` is a document type, whereas an explicit serial number or
coverage period can identify its target. Quoting a generic object or an entire
request does not make it an identifying reference. Do not infer a missing
identifier or require one for an otherwise clear request. This is a semantic
instruction, not a dataset-specific noun blacklist in the validator.
Use null only when the allowed unit
evidence contains no such discriminating span. Answer-neutral case metadata
cannot supply a subject, and a generated rewrite must retain the exact subject
span. A null
category is permitted only under the question's no-match and answerability
semantics. Any returned request-unit answer containing one or more null
`categoryId` values includes `no_matching_option` exactly once in the result's
exhaustive issue list. This also applies to a supported conditional branch whose
requested action is outside the supplied catalog. Relations are a closed discriminated union:
`conditional_on` links a request to a predicate question and required boolean
value; `requires` links a request to its prerequisite; `precedes` preserves
order; and `mutually_exclusive` lists two or more alternatives. Mutually
exclusive branches cannot become simultaneous actions. `precedes` is emitted
when the source explicitly states order. `requires` is emitted only when the
source or caller-authored criteria state that one request cannot be completed
without the other; words such as `first` and `then` establish order alone.
Relations cannot self-reference, form a directed cycle, give the same
request/predicate pair conflicting required values, or make mutually exclusive
requests prerequisites of each other. Two statement requests for
different periods remain two units even when their category is identical. One
request with two unresolved referents remains one ambiguous unit, not two
confirmed actions.

Request extraction is declarative. A request stated conditionally keeps
`status=conditional` even when a separate predicate result resolves its gate to
true or false; `conditional_on` retains the source-stated gate. Extraction does
not execute, activate or deactivate either branch. A later workflow may evaluate
the predicate when deciding what to do, without rewriting the extracted unit's
status.

A later, unambiguous withdrawal or cancellation of a previously stated request
changes that same unit to `withdrawn`; it does not create a second request unit
and does not remove unrelated units. A request to cancel an account, product or
transaction is instead its own `active` unit when cancellation is the requested
business action. A quoted earlier request remains a `quoted` unit unless the
current speaker adopts it. When the alleged withdrawal cannot be linked to one
unit, preserve the supported units and report the unresolved reference rather
than guessing which unit was withdrawn.

Question IDs, option IDs, level IDs, criteria, allowed source IDs, request-unit
IDs and expected relationships are caller/code-authored before model inference.
Untrusted state prose cannot add or redefine them.

### Whole-answer adequacy tasks

An adequacy predicate is permitted only when its state supplies three explicit
objects: the original task contract, the original state, and the proposed
answer. The original task contract enumerates every required output and its
answerability behavior; it is not a reference to an unspecified legal or data
contract. The adequacy criteria define all four checks:

1. every substantive proposed assertion or action is supported by the original
   state under the original task contract;
2. every required active, conditional, withdrawn, quoted, or unknown item is
   represented as that contract requires;
3. cited evidence supports the assertion for which it is used; and
4. the proposed shape and unknown/abstention behavior follow the original task
   contract.

The proposed answer is the object under review and cannot serve as independent
evidence that its own assertions are true. A result is `true` only when all four
checks pass, `false` when a supplied object proves at least one check fails, and
`unknown` only when a fact required to perform the checks is absent. An authored
"correct unknown" example must still represent every other request or required
output; identifying one missing fact does not excuse an omitted request.

### Result, answerability, issues and evidence

`DecisionOutput` is exactly `{schemaVersion, results}`. It contains one result
for every question ID and no unknown result. Each result is exactly
`{questionId, type, answer, answerability, explanation}`. `answerability` is
exactly `{status, issues}`. `explanation` is exactly
`{summary, evidence, contraryEvidence, missingFacts}`; each evidence item is a
`{sourceId, quote}` citation. The fixed system contract and generated output
schema must communicate this complete graph at inference. A terse instruction
without the schema or this exact graph is insufficient.

Status is one of `answerable`, `partially_answerable`, `not_answerable`, or
`undetermined` and is always relative to that question's criteria,
cardinality, allowed sources and time boundary. Issue codes are closed to
exactly `missing_information`, `conflicting_information`,
`multiple_valid_options`, and `no_matching_option`. Their meanings are:

- `missing_information`: a required source, referent, fact, prerequisite,
  cardinality-resolving detail, or applicability fact is absent. A pronoun or
  other unresolved reference uses this code even when several referents are
  imaginable.
- `conflicting_information`: two or more allowed facts are incompatible under
  the question criteria and no caller-authored precedence rule resolves them.
- `multiple_valid_options`: two or more supplied options are each positively
  supported and the question's maximum cardinality cannot represent them all.
  Possibility created only by missing information is not this issue.
- `no_matching_option`: the allowed facts establish a determinate category or
  request, but the supplied option/catalog space has no representation for it.
  It is not used when the underlying fact or referent is unknown. A returned
  request unit with `categoryId=null` always establishes this issue at the
  result level.

The issue list is exhaustive for every independently supported obstacle, not a
primary-cause list. Codes are unique and order has no meaning. For example,
conflicting allowed facts and a separate missing applicability date require
both `conflicting_information` and `missing_information`. A malformed question
is a contract-validation failure, not an issue value. Multiple issues may
coexist and do not mechanically imply one status. Several clear requests are
answerable through `multiselect` or `request_units`; multiplicity alone is not
an issue. A supported no-match can be answerable when the question supplies an
explicit no-match answer or permits a null request category, and `unknown` can
be the correct predicate answer. Endpoint
failure, malformed model output and checker failure are technical candidate
failures, never semantic answerability labels.

For `choice`, `multiselect`, `ordinal`, and `request_units`, `answer` is null
for `not_answerable` and `undetermined`, and non-null for `answerable` and
`partially_answerable`. Only `multiselect` and `request_units` permit
`partially_answerable`. A partial collection contains only supported selections
or request units and reports the gap through issues and missing facts; it never
invents a placeholder selection or unit. A partial collection must contain at
least one supported item and at least one explanation evidence citation. An
empty collection can still be fully answerable when the question permits it
and the evidence supports finding no matching items. A predicate answer is always present:
`answerable`
requires `true` or `false`, while `not_answerable` and `undetermined` require
`unknown`. An answerable result contains at least one evidence citation.

Each citation contains an allowed `sourceId` and an exact nonempty quote from
that source. The explanation names the applied criterion, connects it to cited
evidence, and states contrary evidence or missing facts concisely. Exact quote
presence is necessary but not proof of support. Every summary assertion must be
traceable to a question criterion and either an evidence/contrary-evidence
citation or a listed missing fact; it may not introduce a new requirement such
as an account reference, fulfillment content, or legal contract unless that
requirement appears in the task. Authored or source annotations define the
target. The checker independently solves the task as a rejection signal; it
does not define or replace that target, and model agreement is not proof that
an explanation is entailed.

`explanation.summary` is a nonempty string with a 400-character maximum. It is
one grounded concise reason, aiming for 160 characters or fewer; a second
sentence is reserved for a decisive limitation. The 400-character bound applies
only to `summary`, not to citation quotes, `missingFacts`, or the explanation
object as a whole. Generation never truncates a summary: an oversized response
is invalid and follows the ordinary retained-rejection and repair path.
Solver guidance requests paraphrased summaries and reserves verbatim quotations
for citation fields. Every string must use correct JSON escaping for quotation
marks, backslashes and control characters; decoded citation text must still
match its source exactly. This is versioned prompt guidance, not permission to
rewrite malformed JSON or alter evidence after generation.

Because the current explanation fields contain free prose rather than
machine-checkable claim-to-citation links, automatic publication does not trust
solver-written summaries, request descriptions, evidence lists or missing-fact
prose. For authored rewrite candidates, those fields are deterministically
derived from the code-authored oracle and remapped by source ID to exact text in
the rewritten state. A remapped whole-source citation is permitted only when
that source is at most 4,096 characters; the bound is part of recipe identity.
Remapping fails closed if an oracle evidence source, exact quote, or required
subject span cannot be mapped deterministically after rewriting. These checks
prove exact source/quote/subject correspondence only; intended criterion and
semantic validity comes from the corrected authored reference and its focused
regressions, not an algorithmic entailment claim. The
accepted solver still must match the complete frozen semantic signature, but
its free prose is diagnostic only. Source projections keep their source-derived
target unchanged; blind agreement verifies consistency but cannot promote them
to human-reviewed or gold data.

Native targets contain **no generated numeric confidence, probability,
answerability score, adequacy score, or calibration claim**. Source probability
distributions, model log probabilities and checker self-reports may remain
private provenance/diagnostics, but are never copied into native targets.

## Oracle-first authored examples and content families

The authored generator first constructs a deterministic oracle object containing
source facts, typed questions, exact answers, question-relative answerability,
issues, citations and request relationships. It then assigns the stable family
and partition. A model may realize only the state prose from that oracle. It
cannot choose facts, questions, allowed sources, expected answers,
answerability, issues, dates, amounts, units, jurisdictions, conditions or
relationships.

Every authored example retained under the `examplesPerScenario` cap changes at least one
answer-bearing fact, evidence arrangement, document structure, or expected
semantic result. A paraphrase, translation, answer-neutral date/account suffix,
or numeric substitution that leaves the task and evidence relationship
unchanged is not an independent example. Exact duplicate tasks are rejected
during preparation. Wording variation belongs in generated derivatives of the
same parent/content family.

Preparation uses two bounded identities rather than a generic semantic or NLP
equivalence claim. The exact task identity is the complete canonical task. The
answer-bearing task identity preserves schema version and every question in
order, then preserves in original order every state source whose ID occurs in
at least one question's `allowedSourceIds`; only sources unavailable to every
question are omitted. Included source text is not normalized, summarized or
classified, and included metadata is never dropped. Equal answer-bearing task
identities with different oracle semantic signatures are a contract conflict
and fail closed.

An exact duplicate or a task that differs only through sources unavailable to
every question cannot increase example, unique-task, template-variant or
content-family diversity. Duplicate authored cases are construction failures.
For compatible source projections, deterministic preparation resolves every
answer-bearing task-identity group before applying a source cap or pilot quota.
If new members of a group have conflicting oracle semantic signatures, every
new member is excluded. If an alias crosses frozen splits or frozen source
families, every new member is likewise excluded. When all members have the same
target, frozen family and split, the lexicographically smallest source record ID
is the deterministic representative; the other new members are reported as
exact-duplicate or unavailable-source-only exclusions. A match or conflict with
an existing baseline native task excludes the new projection members and never
removes or rewrites the baseline task. The normalized raw rows remain unchanged
in `source-corpus`; this exclusion never rewrites their frozen source family.
Paraphrases, translations,
value siblings or other variations inside an allowed source are connected only
through explicit template/group/family keys supplied before partitioning. The
implementation does not guess that two allowed texts are semantically equal.

Every authored template has an explicit stable template-variant ID. Its content
family key is the semantic counterfactual group plus that template-variant ID.
The initial catalog supplies exactly four genuine cases per scenario with
different propositions, evidence structures, expected results, or business
states. `examplesPerScenario` selects at most that available count: requesting
100 still yields four, without filler, superficial rendering changes or numeric
repetition. Increasing the cap does not synthesize more seed cases.
All repetitions, answer-bearing value variants, paraphrases, translations and
counterfactual members derived from that key are connected before partitioning,
even when they have different record or family IDs. A new template-variant ID
requires a materially different business state or evidence structure; an index
or filler metadata cannot create one. Distinct authored record identity likewise
requires a real proposition, evidence, expected-result, counterfactual or
business-state change; alternate wrappers and renderings share the same semantic
identity and content family. The preparation report states the requested cap,
available genuine cases and actual selected examples for every scenario, plus
the exact unique task count, template-variant count, connected
content-family count, and duplicate exclusions separately, so row volume is not
reported as independent diversity.

Content-family identity is independent of the run seed and global recipe
version: changing shuffle/partition seed or repairing a prompt cannot make the
same underlying template appear unrelated. Record identity includes the
complete canonical task and oracle identity so distinct answer-bearing examples
cannot collide after stronger grouping. Run/job identity remains versioned by
the recipe and configuration. Tests vary the run seed and recipe version while
proving stable content-family grouping and changed run/job identity.

Ordinal examples provide necessary and sufficient, mutually distinguishable
criteria for every adjacent level, including how missing facts are handled.
Labels such as "routine stated need" without an objective deadline, impact or
other discriminating rule are prohibited.

The scenario catalog covers at least:

- one clear supported request;
- different-category and same-category multiple requests;
- conditional, alternative, ordered and dependent requests;
- an ambiguous referent;
- missing attachment/source and missing customer-only facts;
- withdrawal of one request without removing unrelated requests;
- conflicting information with and without an explicit precedence rule;
- changed deadlines and dated applicability;
- catalog no-match and predicate unknown;
- incomplete financial ratios, currency/units/periods and jurisdiction;
- irrelevant evidence and prompt-injection text;
- a correct-looking result that omits one active request.

Counterfactual members that add, remove, resolve, withdraw or contradict facts
share the content-family grouping. Deterministic validation checks IDs,
cardinality, quotes, dates, numbers, units, relationships, exact JSON and oracle
invariants before any model call and again before publication. This includes the
presence-predicate rule, exhaustive issue list, adequacy contract, request
subject/withdrawal rules, relation graph constraints and explanation grounding
roles specified above.

Every prepared native `DecisionSeed` carries required `rewriteSourceIds` in
addition to its parent, scenario and mode. The list is unique, every ID names a
source in the parent task, and every named source is non-metadata. Rewrite mode
requires a nonempty list; annotate mode requires an empty list. The rewrite
request may change text only for those declared sources. All other source
objects and text, the source order, and the complete question contract remain
byte-for-byte fixed from the parent. Candidate reconstruction and cached-outcome
validation enforce the same boundary before publication.

The authored recipe declares every non-metadata source mutable for ordinary
authored scenarios. Whole-answer adequacy scenarios declare only
`original-state` mutable: `task-contract` and `proposed-answer` are the fixed
object and rubric being assessed and therefore remain byte-for-byte unchanged.
Projected annotate seeds declare no mutable sources. `rewriteSourceIds` and
this scenario policy are bound into recipe, job and cache identity, so the
narrower boundary always creates a fresh run rather than reusing an older
outcome.

## Auxiliary-source projection

The five pinned sources remain a separate raw `source-corpus`; they are not
renamed as native human-gold data. A projection may create a native seed only
when the source annotation maps to the native question and answer semantics
without model inference, guessed evidence, fabricated answerability or changed
cardinality. The projection retains the original source declaration, rights,
family and frozen split. It adds the deterministic provenance tag
`original-source-record:<id>` to identify the source row without misusing model
generation lineage.

The default compatibility policy remains deliberately narrow:

- BANKING77: one `choice` question over the complete intent catalog, with
  versioned editorial definitions and contrastive boundaries. Definitions are
  Foliqant task guidance, not additional source gold. Overlap without an explicit
  distinction must remain ambiguity, not an invented tie-break. Preserve the
  original source label as an unreviewed reference; unknown catalog labels fail
  closed rather than falling back to raw names as definitions;
- WANLI: one `choice` question classifying the relation between the two supplied
  texts as `entailment`, `contradiction` or `neutral`, with explicit definitions.
  Neutral is a relation category, not fabricated native predicate answerability
  or a missing-fact annotation. Preserve both original texts, including questions,
  and the source relation label. This replaces the former truth-predicate
  projection for new recipes only; old artifacts and schemas remain readable;
- typed-decisions, MultiDoGO and TAT-QA: not projected by the default recipes.
  Their records remain in `source-corpus`, and exclusions are counted. The
  explicit offline plan below is the only path that enables their bounded
  mappings; default generation still promises no partial mapping.

The explicit offline source-projection plan adds only these mappings:

- **MultiDoGO finance:** one `multiselect` intent question over the fixed
  18-intent catalog. Only the source-declared intent set becomes a native
  target. Token-aligned slot labels and the raw redacted turn remain attached to
  the raw source record and provenance; slot labels are never converted into a
  native answer or discarded to make the record appear simpler.
- **typed-decisions:** project all five questions in each eligible record.
  Source `choice`, `noul`, and `score` questions map respectively to native
  `choice`, `predicate`, and `ordinal` questions. The deterministic target uses
  the source's explicit `label`; it never takes an argmax over the teacher
  distribution. The complete raw distributions remain in `source-corpus` and
  provenance, but no probability or teacher-agreement value becomes native
  confidence. The source label is only a proposed answer: an independently
  blind solve must verify both the answer and answerability before publication.
- **TAT-QA:** at most one deterministic comparison predicate per source context.
  Eligibility requires a unique table row and adjacent, unique, explicit
  year-column headers in the range 1900 through 2099. Both displayed cells must
  parse as strict signed decimals with matching unit markers: the same currency
  on both values, or percentage markers on both values.
  The predicate compares the displayed values directly. It does not implement
  full financial question answering, infer a derivation, or consult the
  original TAT-QA gold answer.

All three mappings preserve the source family and exact frozen split. They
produce English native tasks from the existing English source text only; they
do not create translations. Every ineligible row receives a stable exclusion
reason and is included in selected/projected/excluded accounting. All raw source
records and annotations remain in `source-corpus` even when no native task is
projected.

Per-source preparation reports include `eligibleMultiIntentRecords` and
`selectedMultiIntentRecords`: rows with a multiselect reference answer selecting
more than one option. They expose limited multi-intent availability separately
from the total number of multiselect questions.

Before caps and pilot quotas, projection preparation groups new candidates by
answer-bearing task identity. It excludes all new members of a group when their
targets conflict, when the alias crosses frozen splits, or when it crosses
frozen families. Same-family, same-split and same-target duplicates retain one
deterministic representative by source record ID. Existing baseline tasks are
comparison anchors only and remain unchanged. Under the currently frozen
inputs, the group-wide conflicting-target rule excludes 24 MultiDoGO rows; this
is an offline preparation count, not a quality result.

Projected native records use `origin=synthetic` or `origin=teacher`, never
`human`, because projection creates a novel native task/result even when its
source annotation came from people. `reviewed` remains false. Projection does
not add `GenerationProvenance`; that field remains reserved for actual model
generation. The original source declaration, rights and family/split
provenance remain attached.

An inherited source label is a proposed unreviewed reference, not unquestioned
truth for the new native task. A projected training seed receives one blind
solve verification job. A disagreement is quarantined; an unattempted or
quarantined projected training seed remains only in `native-seeds`. On exact
semantic agreement, the accepted annotate outcome is exactly the existing seed
parent with its source-derived target; it is not a prose variant, gets no
`GenerationProvenance`, and is published once. Its explanation is the corrected
reference explanation, never unchecked solver rationale. Coverage reports this
as an accepted projection verification separately from an accepted generated
derivative. Neither agreement nor publication changes `reviewed=false` or
supports a gold-label or accuracy claim.

Every source reports selected, projected and excluded counts by stable reason,
including unsupported type, absent required evidence, invalid cardinality,
unsafe alignment, duplicate/family exclusion and configured cap. Zero compatible
examples is visible and does not trigger a synthetic substitute.

## Partitioning and generation isolation

Source families are frozen before projection or generation. Authored native
content families are connected and frozen independently before generation. A
projected record inherits the source family and split; an authored or generated
derivative inherits its native family and split. The content-family grouping
defined above is applied before split assignment. No template variant,
counterfactual sibling, value substitution, paraphrase, translation or
generated derivative in one connected component can span published splits.
Held-out authored content uses template variants absent from training; a
same-template record with different filler or wording is not held-out evidence.

Only native `train` families may create generation jobs. Generation messages,
few-shot examples, retrieval and checker state must never contain source,
calibration or test records. Held-out native seed parents are copied unchanged
into the diagnostic portions of `native-decisions`; they are never submitted to
the generator or used as examples. Raw auxiliary calibration/test content is
likewise unavailable to training generation. This prohibition includes resume,
retry and cache construction.

Coverage cells are formed only from eligible training seeds after content-family
assignment. A scenario/language combination whose entire content family is
held out is reported as held out and creates no required training cell or
coverage shortage. Pilot priority is applied to the available training cells;
it never moves held-out content into training to satisfy the configured pilot target.

The generator receives the parent task without its result and may vary only the
permitted state prose while preserving facts, sources and task. The independent
checker receives the candidate state and complete task contract but not the
oracle/parent target, scenario category, hidden label or generator output flags.
It solves the whole task itself. Automatic acceptance requires schema validity,
deterministic oracle checks, citation validity and equality of the frozen
semantic signature: answers, answerability statuses, exhaustive issue codes and relations.
Multiselect option IDs are compared as an order-independent unique set. For
request units the signature compares each unit's ID, status, category ID and
exact subject plus canonical relation sets. `mutually_exclusive.requestIds` is
order-independent; all other relation direction is significant. The signature
does not treat generated unit descriptions or explanation prose as trusted
semantics; published prose is canonicalized from the corrected reference as
specified above. Citations and non-null subject substrings still pass
mechanical source/quote validation before signature comparison.
The blind checker is a rejection signal only; it cannot change the oracle,
repair a candidate, or promote its review status. Model-reported support or
confidence is not truth. Connection and integrity failures stop the run;
bounded semantic failures are quarantined with safe reasons.

For model requests, project the canonical output schema onto only result types
present in the caller's task and remove unreachable definitions. Preserve schema
declaration order, all surviving constraints, IDs and value domains. The public
V1 output schema and full post-response validation remain unchanged. Bind the
projection rule version into the recipe and the actual projected schema into
request/cache identity. Verify all nonempty combinations of the five result
types; an all-types task must retain the full schema. This reduces request
complexity without treating smaller schemas as evidence of model correctness.

When `maxAttempts` permits another inline attempt, only a recoverable rejected
job continues. Feedback contains the previous bounded final assistant output
from the failing phase and actionable contract guidance, without the oracle,
target answer, hidden scenario label or exception details. A failed rewrite
repairs rewriting; a valid rewrite followed by an invalid solver response
retains the candidate state and repairs only the solver response. Recovery must
reconstruct the same validated candidate from verified call caches after
interruption or external repair. Every new solver response passes the complete
validation sequence; no response is edited directly or promoted automatically.
The immutable outcome retains attempt traces and call provenance. Accepted
records are not called again.

A schema-valid `solver-semantic-mismatch` is terminal for automatic recovery,
both inline and through native `--repair-from`: preserve its quarantine for
reference review rather than repeatedly cueing the model to change its label.
No disagreement is evidence that the source reference or the model is correct.
Ordinary structural validation remains strict, including the prohibition on an
answerable predicate with value unknown. Feedback makes that constraint explicit.
Calendar-year-end phrasing equivalences are permitted by the lexical guard;
actual durations, rates, year values and calendar boundaries remain protected.

The separate `--repair-from <run>` operation follows the immutable child-run
contract in specification 08. It must additionally verify the native seed and
family snapshots, preserve accepted training lineage, and permit a fully
processed coverage-failed parent. It does not relax coverage or promote
quarantined rows. The new child retains all statuses in its outcome ledger;
only accepted rows enter the published training lineage.

These changes require a fresh generation-recipe identity. An older completed
run stays intact and readable; changed source tasks/prompts must not be passed
off as a same-recipe repair or continuation. New runs may reuse verified source
downloads, but must not transplant old outcomes into changed tasks. A bounded
sequential pilot precedes a new full run. Native integration and human quality
qualification remain separate from offline recovery tests.

## Publication, coverage and completion

Publication reuses the immutable dataset artifact, full `DataRecord` files,
leakage indexes, `FrozenFamilyAssignment`, `GenerationProvenance`, source-rights
union and verified manifests from specifications 03 and 08.

The prepare-only run publishes `source-corpus` and `native-seeds`. A successful
generation run publishes:

1. `source-corpus`: normalized raw auxiliary records from all acquired sources;
2. `native-decisions`: every unchanged held-out native seed, including source
   projections, for diagnostic use,
   every accepted authored rewrite derivative plus only its validated training
   seed parent required for lineage, and every projected training seed whose
   blind verification was accepted exactly once;
3. a private coverage report bound to the run and dataset identities.

Unattempted and quarantined training parents are excluded from
`native-decisions`; they remain in `native-seeds` with their outcomes. A parent
is admitted only after the corrected authored/source reference passes the
deterministic oracle checks and its candidate job is accepted. Keeping the
accepted authored parent is mandatory because its generated derivative's
lineage references that record ID. A projected annotate acceptance publishes
the parent itself and creates no generated duplicate. Generated rewrite
variants use `origin=teacher`, `reviewed=false`,
the observed local model metadata and parent record IDs. Authored seeds use
`origin=synthetic`, `reviewed=false`. Projected seeds also use
`origin=synthetic` or `origin=teacher`, `reviewed=false`, while retaining the
original source record ID in their provenance tag. Source and generator terms
are carried conservatively into derived source declarations; unknown
teacher/output terms remain restrictions rather than inferred permission.

`native-seeds`, held-out rows, and any artifacts retained from a failed coverage
gate are diagnostic inputs, not completed generation results. If zero candidate
jobs are accepted, no `native-decisions` artifact is published and the existing
nonzero `OUTPUT_INVALID` path points to the coverage report. With at least one
accepted job but a later coverage shortage, the bounded eligible partial corpus
may be retained as diagnostic while the command still fails.

Coverage reports planned, accepted and quarantined jobs; known source totals,
projection totals and exclusions; primitive question types; answerability
statuses; issue codes; scenarios; languages; partitions; and safe rejection
reasons. Accepted jobs are split into `verifiedProjection` and
`generatedDerivative` counts; row counts separately report lineage parents,
held-out diagnostic seeds and published records. The existing
`CurateResult.generatedAccepted` value remains the existing total accepted
candidate-job count and is not a count of newly generated rows. Coverage
reports every eligible configured training `(scenario, language)` cell,
including cells that the candidate budget left unplanned and cells with no
accepted candidate, and compares accepted candidate jobs with
`minimumAcceptedPerCell`. Held-out cells are reported as excluded from
generation and never satisfy training coverage. A
completed generation attempt with any required cell
shortage raises the existing `OUTPUT_INVALID` error (exit code 4) with an
`ErrorLocation` pointing to the private coverage report. It returns no
`CurateResult(status="completed")`, retains all valid resumable work and partial
artifacts as diagnostic, and publishes no misleading complete status. The operator increases a
budget, repairs the endpoint/model/recipe, or explicitly changes the
configuration; no manual record review is required to unblock the pipeline.
`prepare-only` reports planned cells and does not fail for zero accepted model
outputs because generation was not requested. The pilot's
`minimumAcceptedPerCell=0` disables the per-cell gate, but a requested pilot
generation still needs at least one accepted candidate job for completed
success; zero accepted candidates is an incomplete diagnostic run.

## Security, privacy, rights and operations

All downloaded data, native rows, oracle objects, prompts, model responses,
checks, caches and reports stay in the private external workspace. Console JSON
contains paths, identities, counts, statuses and safe errors only. Public/DNS
endpoint restrictions, no ambient proxy, no cloud fallback, bounded response
handling, cancellation limitations and cache integrity follow specification 08.

Raw source text and model text are untrusted. No generated output can change
source rights, family assignments, question contracts, coverage requirements or
run configuration. Corrupt or changed cached state fails closed. Publication is
atomic and immutable. New recipes create new runs. Data and generated artifacts
remain excluded from Git.

## Acceptance requirements

Implementation verification covers:

- strict generated schemas and round-trip contract tests for every native
  question/result variant, required nullable request subjects, literal-subject
  evidence validation and invalid cross-variant combination;
- oracle invariant and counterfactual tests covering every required scenario,
  including explicit-absence predicates returning answerable false, missing
  referents using `missing_information`, all applicable issue codes, objective
  ordinal rubrics and explicit whole-answer task contracts;
- request regressions for exact amount/reference subjects, order without
  inferred dependency, explicit prerequisite, alternative-condition graphs,
  cycles/contradictions, withdrawal of one unit and cancellation as a distinct
  business request;
- proof that generator and checker messages contain no target answer or selected
  hidden scenario label;
- tests that no source/calibration/test record reaches any generation request;
- stable content-family, split and parent lineage tests across template variants,
  values, counterfactuals, paraphrases, projection, translation, retry and
  resume, plus a rejection proving a same-template train/held-out split cannot
  be published;
- diversity accounting that rejects exact duplicates, resolves whole projection
  identity groups before caps, excludes every new conflicting-target,
  cross-split or cross-family member, deterministically retains one same-target
  same-family representative, preserves baseline tasks unchanged,
  preserves allowed metadata as answer-bearing,
  and reports examples, unique tasks, template variants, connected content
  families and both exclusion counts without equating those counts;
- source projection fixtures with explicit selected/projected/excluded counts;
- offline projection-plan fixtures proving zero endpoint discovery/inference and
  zero downloads, exact frozen source/family/split reuse, stable exclusions,
  MultiDoGO's 18-intent multiselect, all five typed-decisions mappings with
  explicit labels rather than argmax, and TAT-QA's unique-row/adjacent-year/
  signed-decimal/unit rules without consulting its original gold answer;
- extension CLI tests proving the two plan flags are required together,
  conflict with continuation/repair, require a completed parent, allow
  prepare-only without discovery, preserve every old outcome byte-for-byte and
  schedule only new train verification jobs in an immutable child;
- publication tests proving unattempted/quarantined training parents remain only
  in `native-seeds`, accepted annotate jobs publish the exact parent once with
  no generation provenance, accepted rewrites publish only their required
  parent plus derivative, all held-out native seeds remain diagnostic, and zero
  acceptance publishes no `native-decisions` artifact;
- deterministic target-canonicalization tests proving solver-written rationale
  cannot enter published records, rewritten citations and exact subjects remap
  or fail closed, and unsupported requirements cannot appear in summaries;
- seed mutability tests proving rewrite IDs are unique existing non-metadata
  sources, annotate IDs are empty, ordinary authored cases expose every
  non-metadata source, and adequacy rewrites expose only `original-state` while
  preserving `task-contract` and `proposed-answer` byte-for-byte;
- conditional extraction tests proving predicate truth does not execute a
  branch or change `status=conditional`, and every returned null category adds
  `no_matching_option` to the exhaustive issue list;
- date/value preservation tests accepting equivalent ISO and written-date forms
  plus `per month`/`monthly` rate phrasing while rejecting duration/rate swaps,
  changed dates, numbers, currency, units and identifiers;
- prepare-only and offline-cache compatibility tests;
- interrupted serial generation followed by immutable resume;
- coverage-shortage nonzero behavior without loss of accepted work;
- verified `source-corpus` and `native-decisions` artifacts through the existing
  prepare/train/evaluate data boundary; and
- one real bounded local full/pilot generation acceptance using the exact
  endpoint/model and recipe. Test doubles cover failure boundaries only.

Training, assessor fitting, probability calibration, policy selection, runtime
decision APIs, workflow automation, human-review tooling and production
qualification are outside this data-only change. Frontend, hosted API, webhook,
queue, PURISTA, Harness and Voyage work remain not applicable.

## Explicit deterministic migration and question variants

The approved migration path creates a new dataset from a verified completed
native run without inference or downloads. It is separate from continuation and
repair: prompt/transport revisions alone do not invalidate existing records.
Retain compatible authored records with their exact historical generation
provenance; reproject changed imported tasks from their pinned original annotations
and current task definitions. Reprojected rows are source-annotation references,
not claims of fresh blind model verification. Never convert reference disagreement
into historical acceptance. Preserve the original run and all rejection evidence.

Include the existing deterministic MultiDoGO, typed-decisions and TAT-QA projections
after the same whole-group deduplication, contradiction, family and split checks.
These rows can enter the explicitly diagnostic migrated dataset with their actual
source-annotation or deterministic-computation status. This explicit offline import
does not change the blind-verification requirement of ordinary curation extensions.
Keep all five typed-decisions questions and original distributions in provenance;
derive no model-confidence values. TAT-QA comparisons use the displayed numeric
cells, never the original QA answer. Complete raw annotations stay linked by source
record ID and immutable source snapshot digest.

Derived question variants reuse the identical state, source rights, family and
split. A complete, answerable multiselect reference can yield an exact-one choice
question: one selected option yields that option; several yield a null answer and
`multiple_valid_options`. Do not invent a primary intent, merge unrelated messages,
or treat missing annotations as negative facts. Preserve German prose and stable
English enums. New variants have distinct IDs and explicit deterministic parent
links, not fabricated model-generation metadata.

Every migrated row has a typed provenance entry linking original dataset IDs,
source record IDs, annotation snapshot digests, source-rights IDs, parent records,
transformation rule and evidence status. Export ordinary conversational JSONL and
the full records through the existing artifact preparation/verification boundary.
Preserve source restrictions and frozen family assignments. Count old published
rows, retained/reprojected rows, added sources, variants, independent families,
split/language totals, exclusions, review items and remaining inference tasks.

`migrate-decisions --from-run RUN [--output DIR]` and `scripts/migrate-data` perform
that offline operation with immutable resumable publication. A frozen pending queue
contains only unresolved training tasks. Unchanged semantic disagreements go to
review; changed question semantics can justify a fresh bounded verification task.
`rerun-migrated-decisions --from-migration DIR [--output DIR] [--limit N]
[--job-id RECORD_ID] [--progress auto|always|never]` and `scripts/rerun-migrated-data`
operate only on that queue. Selection and recipe are hash-bound, completed outcomes
and requests resume without new calls, and fatal transport errors preserve progress.
Completed rejected jobs leave the pending queue for review; they are not repeatedly
sampled until agreement. Successful rows are published with current verification
and explicit ancestry in an immutable child containing the unchanged migrated rows.
Neither command implicitly uploads, trains, changes source labels, or reassigns
held-out families. Tests cover offline network prohibition, unchanged parent bytes,
tamper rejection, exact labels/evidence, variants, rights, queue isolation and resume.

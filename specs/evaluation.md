# Evaluation contract

## Execution and datasets

`WorkflowApplication.run_step(workflow, flow_id, step_id, envelope)` executes exactly one
configured step using the same runtime, executor, budgets, caller context and
adapter validation as full execution. Payload contains the explicitly resolved
input/source/argument keys for that step. It does not execute upstream steps,
follow routes or apply the pipeline's input/output projection. It returns only
the selected flow/step record and its successful/review result as payload; failed runs
retain their accepted resolved-input payload. Both retain the original plan
revision. Unknown step names and missing/extra inputs fail before model I/O.

`run_flow(workflow, flow_id, envelope)` executes one flow with its already-resolved
input and validates the flow input schema. It applies that flow's output projection,
but no upstream input bindings, workflow projections or boundary transitions.
Both isolation APIs retain original workflow identity and revision, caller/tool
policy and finite limits; they never silently run dependencies. Callable flows
and their steps may be selected directly. Isolating a `flow_collection` step
supplies its already-resolved `items` input; the step intentionally invokes its
allowlisted callable flows under the same run limits.

Each executed step returns measured monotonic `elapsed_seconds` and its own
attempt/token `usage`; skipped or unmeasured steps have no measurements. Failed
requests remain charged. Missing token observations remain unknown. Cancellation
propagates rather than producing fabricated completed measurements.

The optional `foliqant.evaluation` module evaluates explicit ground-truth cases
against an async pipeline or isolated-step callable. A case holds an input and
named expectations at RFC 6901 result paths. Exact equality is type-sensitive;
set comparison is explicit for unordered labels, not an implicit coercion.
Missing output, skipped steps and execution failures remain visible in coverage
and denominators. Custom scorers must be explicit host functions. No evaluator
calls an LLM judge, discovers endpoints, alters prompts or accepts its own output
as ground truth.

The module ships in the standard wheel and needs no evaluation extra or new
dependencies. Its Python entry points are `evaluate` and `compare_variants`, with
`EvaluationSuite`, `EvaluationCase`, `Expectation`, `EvaluationVariant`,
`RegisteredScorer` and `MetricSpec`. Immutable dataclasses remain the scoring
representation. The optional JSON boundary converts into those same values;
there is no second scheduler or evaluation framework.
Source-span comparison uses independently authored `input_path`, `required`, and
`allowed` gold. Ranges use Unicode code-point offsets with an exclusive end;
required is nonempty and nested inside allowed, both within the input string.
Candidates must match a verbatim occurrence containing required and contained
within allowed. No normalization, semantic judge or prediction-derived gold is
implied. Invalid ranges and nonstring/missing source pointers fail offline.

Set comparison ignores top-level array ordering and duplicates while retaining
JSON type distinctions. Custom scorers are registered async functions with an
explicit revision. `evaluate` defaults to one worker and a 300-second per-case
timeout including scoring; results retain suite order. `compare_variants` runs
variants sequentially. Hosts own ground truth and holdout selection. The library
neither certifies holdout separation nor optimizes prompts.

`repeat` defaults to 1 and requires a positive integer, excluding booleans.
Each source case runs that many times in case-major order. Original case IDs
remain unchanged; `repetition` identifies attempts from 1 through `repeat`.
`case_count`/`source_case_count` describe distinct authored cases and
`attempt_count` describes executions. Assertion, outcome, and classification
counts cover attempts; equal repetitions preserve equal weighting per source.
Repeated outcomes never become independent gold. Work is scheduled lazily with
the same bounded concurrency and cancellation ownership as ordinary evaluation.

Evaluation defaults to `evaluation/dataset.json` beside the `config/` directory.
An explicit `evaluation.dataset` overrides it with a literal path relative to the
settings file (absolute paths also work). Preparation, application startup, `validate` and `doctor` never stat or
open that dataset. The field is excluded from the runtime configuration digest;
changing private gold locations does not revise execution. Gold need not be
packaged or deployed. The reference is not an environment-expansion field.

The strict JSON dataset has `name`, `revision` and named `suites`.
Each suite declares a configured `workflow`, optional isolated `flow` and `step`,
optional `metrics`, and `cases`. `step` requires `flow`. Without either target,
run the whole workflow; with only flow, run that flow; with both, run one step. Cases may be a nonempty inline array or a path to a JSON
case array, relative to the main dataset file (absolute paths also work). Mixed
inline/separate step and pipeline files are supported through the same loader;
nested references are not. Every case contains `id`, an envelope `input`, and
named `expectations` (`path`, explicit `expected`, optional `comparison`
`exact|set|source_span`). Step inputs are already-resolved inputs, not upstream pipeline
inputs. File configuration never imports scorer functions or executable code.
The generated `evaluation-dataset.schema.json` describes this boundary. Loading
rejects duplicate keys, duplicate IDs/names, nonfinite values, invalid pointers,
unknown target workflows/flows/steps, invalid gold catalogs and over-limit files.
Static checks cover known result roots/flow/step references, not arbitrary dynamic
payload-field existence or the business correctness of ground truth.

`foliqant evaluate --check` loads and validates only. Ordinary `evaluate` opens
the configured application and runs suites sequentially with default one worker
and a 300-second case deadline, still subject to normal runtime limits.
`--max-concurrency` and `--timeout` change evaluator bounds only. Explicit
`--replay REPORT` scores saved complete results without constructing clients,
resolving credentials or calling models/tools. Replay requires matching dataset
identity, suite/case identity and order, input values, configuration/workflow
revisions and requested workflow/flow/step target, including all-error runs;
revised gold is permitted. Replay cannot measure a changed prompt. Its suite wall
time describes rescoring; saved case latency, step usage and measurements describe
source execution. Repetitions are inferred from the saved artifact unless an
explicit matching count is supplied. Each saved attempt is rescored exactly once.
Missing saved outcomes are rejected; recorded execution errors remain
errors. No replayed failure triggers inference.

The CLI writes a full report atomically to a new owner-readable file;
it never overwrites. Default destination is a unique
`.foliqant/evaluations/report-TIMESTAMP.json` under the config directory;
`--output` selects another new file. Stdout contains counts/status/path only.
Reports contain private input, gold, complete public execution results and
per-check actual/expected/presence/reason. Public reasons and evidence-strength
assessments are retained; internal provider reasoning is not collected.
Authored synthetic example gold is committed under each example's `evaluation/`
directory. Real customer gold, private corpora and generated results
remain ignored and must not be uploaded as ordinary CI artifacts. Each dataset
file is limited to 64 MiB; report publication/replay share a 256 MiB bound.
Cancellation joins owned tasks and returns no fabricated complete report. Exit
codes are 0 for passing checks, 1 for disagreement, 2 for invalid config/data,
3 for missing dependencies, 4 for runtime failures (even if expected by gold),
130 for interrupt. The repository `scripts/evaluate` forwards to this command.

`evaluate --compare CANDIDATE --baseline BASELINE` and Python `compare_reports`
compare private saved reports offline without loading configuration or clients.
Require matching inputs, gold, scorers, metric catalogs, targets and complete
attempt identities; configuration/variant revisions may differ. Recompute
aggregates from validated observations. Retain mixed per-case changes and
operational failures. Completion and intended review have equal operational
rank; authored assertions determine correctness. Replay reports cannot establish
latency improvements. Comparison output is descriptive, not significance or
automatic acceptance policy.

`group_report` accepts a detailed Python report and an explicit input pointer.
Groups use scalar, type-sensitive keys, separating missing from null; retain
first-observed order and complete source repetitions. Reuse existing metrics
and measured latency/usage; never infer groups, rerun scorers or call endpoints.
Saved artifact loading is not part of this grouping API.

Metrics use explicit label catalogs and gold expectations at their declared
result pointer. Classification catalogs contain strings and may explicitly
include JSON null. If null is declared, null gold is valid and an actual null is
an observed classification outcome in matrix/per-label counts; otherwise actual
null is an abstention and null gold is invalid. Missing/skipped/error observations
never become null. Multilabel catalogs remain string-only. Classification matrices
have expected rows and predicted columns in catalog order. Multilabel reports
include per-label TP/FP/FN/TN and exact-set accuracy. Reports distinguish support (matching gold), excluded cases
(no matching gold), valid observed outputs, null abstention, missing, skipped,
errors and invalid predictions. Accuracy is correct/support, coverage is
observed/support; zero support gives null. Confusion and per-label counts cover
only valid observations and are read alongside coverage. Wrong labels/types do
not become valid predictions. No acceptance thresholds are inferred. An exact
array assertion still preserves ordering, independently of set-based metrics.

Each label also reports observed-only precision, recall, and F1. Precision and
recall are null on a zero denominator; F1 uses `2TP/(2TP+FP+FN)` and is null when
that denominator is zero. `micro` pools label counts. `macro` averages the whole
declared catalog for each measure, yielding null if any label has an undefined
value for that measure. Coverage and unobserved outcomes remain separate rather
than inventing label predictions. With repetition, `support`/`excluded` count
attempts and `source_support`/`source_excluded` identify authored cases.

Reports identify flow and local step separately; aggregate step measurements by
`(flow, step)`, never a colliding local name. Expectations use
`/flows/<flow>/steps/<step>/result` or a public flow projection. Flow-only checks
are attributed to the flow, not invented step records. Workflow, flow and step
usage are separate views of the same calls, not additive across levels.

Collection execution records carry `kind: flow_collection`. Evaluation traverses
only marked step ledgers (`result.items`, or `partial_result.items` after failure),
never an ordinary business object that resembles a ledger. Child invocations stay
in array order and retain repeated calls to the same flow, failures and skipped
items. Projections of a ledger do not create duplicate observations. Each per-case
flow, step and scoped check report carries its exact `invocation_path` JSON pointer;
nested checks identify the innermost recorded flow and step. For example,
`/flows/main/steps/collect/result/items/0/steps/work/result` checks the first child
invocation's `work` result. The analogous failed-step ledger uses `partial_result`.

Flow and step summary `observed_invocations` counts execution records, including repeated
child invocations within one case. Every measured child observation contributes
to its flow/step latency and usage summary; skipped/unmeasured records remain
unavailable. A case with no matching record also retains an unavailable observation.
Case/root usage is read once from the execution result, never summed across
inclusive parent and child levels. Missing assertions in failed child records are
errors; missing assertions in skipped child records are skipped. Metrics preserve
failed-case error denominators, while explicit skipped records remain skipped.
Replay preserves the complete nested records, markers, measurements and paths.
Generated collection containers do not consume a child business value's depth
allowance. Every ordinary step result keeps the existing 64-level business bound;
flow/root projections and the complete snapshot receive only the extra structural
depth established by actual marked invocation records. Unmarked operation results are never
granted that collection allowance. The evaluator rejects more than sixteen
collection levels and caps generated documents at the shared execution bound.
Offline target checking follows declared collection allowlists and rejects
impossible item indices or child step names; dynamic business fields and which
allowlisted flow an item will actually select remain runtime observations.

Step summaries also count `model_selected_invocations` and `fallback_selected_invocations`.
`fallback_rate` is fallback selections divided by all observed step records,
including skipped/error records, or null with no records. Repeated attempts
count separately. These are policy-use measurements, not accuracy. Replay and
grouping preserve the selection origin from the validated public result.

Report and step summaries include measured latency count, unavailable count,
minimum, median, p95, and maximum in seconds. The median averages middle values
for an even sample; p95 uses nearest rank `ceil(0.95*n)`. No measured samples
means null statistics. Suite latency uses measured execution durations, including
errors; replay wall time remains replay measurement, not source model latency.
Usage summaries preserve observed/unknown counts and known totals separately:
the complete total is null when any observation is unknown, and even known-total
is null when there were no known observations. No unknown count becomes zero.

Every runnable example includes executable evaluation with explicit ground
truth. Model examples default to clearly labeled scripted wiring checks and
require `--live` for local-model measurements. Step suites use `run_step`, flow
suites use `run_flow`, and full suites use `run`. The HTTP wrapper reuses the same workflow suite through its
ASGI boundary; it does not duplicate business logic. Failed assertions result in
a nonzero evaluation command status. Example evaluators save a new private
report by default and print counts and its path; core Python evaluation remains
in memory unless the host explicitly writes it. No example's expected values are generated
from its observed response. Synthetic checks do not establish population accuracy.
Support fixtures cover each declared queue label and review, including English
and German inputs with unchanged English category keys. Committed synthetic JSON
gold is authored independently of scripted responses and available immediately
after cloning. Example reports stay private; gold is never required at startup.

Reports identify the suite and its content fingerprint, the variant/configuration
revision supplied by the caller and observed workflow revisions. They report
check/case pass rates, execution failures and review rates, measured latency and
available token usage. Compare prompt variants on the same suite, sequentially
by default; opt-in concurrency is bounded. Cancellation propagates and joins
evaluator-owned cooperative tasks; it does not prove remote or blocking work
stopped. Python reports omit business values unless `include_details=True`;
the explicit evaluation CLI enables details for its private report. Ordinary
application execution never persists evaluation data or results.

Prompt optimization means comparing explicitly authored variants using these
metrics, then validating a selected variant on a separate untouched holdout.
A small synthetic example is a wiring test, not a financial accuracy estimate.
No confidence/calibration, statistical significance or production reliability
claim follows from a pass rate alone.

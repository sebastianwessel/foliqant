# Multi-request planning and flow collections

Status: active. Business interpretation belongs to authored questions and trusted
application code. Core provides bounded execution, not a business scheduler.

## Separate interpretation, planning and disposition

Use `choice` when exactly one category is required, `multiselect` for labels, and
`request_units` for distinct requested actions. Two labels do not necessarily mean
two requests; two requests can share one category. Preserve native assessments,
including partial answers, withdrawals, quoted history, conditions and relations.
A collection-level reason or evidence strength does not certify each unit.

An explicitly registered async handler maps assessments into a plan. Application
policy chooses independent requests, merges duplicates, selects allowed flows and
passes only required inputs. Dependencies use an authored coordinating flow or
review; no automatic dependency inference or generic graph scheduler is supplied.
A separate handler determines business disposition from the complete child ledger.
No built-in business statuses, global readiness threshold or automatic fallback exist.

## Authoring and validation

A `flow_collection` step declares `items` (a literal or pointer binding), a nonempty
unique `flows` allowlist, and `max_items` (default 32, integer 1–1024). Each item has
exactly `id`, `flow` and object `input`. IDs are unique within the collection;
flow targets must be allowlisted. All items, limits and target input schemas are
validated before any child operation. Empty collections complete successfully.

Callable workflow entries declare `callable: true` and an optional definition;
conventional resolution remains `<id>/flow.yaml`. They have sequential steps,
input schema and output binding, but no routing, unresolved target or root input
bindings. Routed transitions cannot target callable flows; collections cannot call
routed flows. Static reachability and cycle checks include collection edges.
Callable invocations may nest at most sixteen collection levels; compilation
rejects deeper call chains, and runtime applies the same defensive bound. Routed
transitions do not consume nesting depth. This fixed bound preserves the complete
64-level business-value allowance inside bounded generated execution ledgers.
Callables are not available through root `/flows` bindings. Repeated calls retain
separate records under the collection; they never overwrite by target flow name.

## Execution and results

Execute planned items sequentially in supplied order. Children share the root
execution ID, admission slot, absolute deadline and visited-step budget. Normal
per-step model/tool budgets and provider admission still apply. Child contexts
use their declared flow and step IDs; caller metadata is inherited, while payload
is exactly the planned input. No hidden prior history is forwarded.

Collection step records carry `kind: flow_collection`; ordinary business results
are never inferred to be execution ledgers merely from their shape. A completed
collection has `result.items`: ordered child records with `id`, `flow`,
status, steps, projected result when present, error, usage and elapsed time. Child
`needs_review` does not prevent later independent items; the collection becomes
`needs_review` and uses the enclosing routed flow's explicit unresolved policy.
First technical failure stops execution: failed collection `partial_result.items`
retains completed/review/failed children and later `skipped` children. It has no
success `result`. No failure is silently converted into a business disposition.
Caller cancellation propagates and joins owned work without launching later items.

Root usage counts child attempts once through the collection subtotal. Durations
are nested measurements, not additive totals. Telemetry uses only configured flow
and step names, never business item IDs. Private execution/evaluation results may
contain item IDs and original assessments. Evaluations retain every invocation,
including repeats of one callable flow, failures and unstarted steps.

Public `run_flow` remains an isolated invocation API for direct use and evaluation;
calling it repeatedly inside a handler is not parent-owned composition. Collections
do not provide durability, external writes, task queues, parallel fan-out or recovery
across process loss. The embedding application owns those requirements.

## Acceptance

Offline tests establish complete prevalidation, duplicate/unknown target rejection,
empty collections, repeated targets, nested acyclic calls, shared budgets/deadlines,
review continuation, first-failure retention, cancellation and usage accounting.
Examples demonstrate explicit planning and disposition, independent gold for full
pipeline and isolated flow/step scopes, and a real bounded model-to-MCP tool loop
using scripted model responses. These prove wiring, not live model accuracy.

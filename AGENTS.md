# Foliqant contributor guide

Read [specs/README.md](specs/README.md), then the relevant active specification
and implementation. [docs/index.md](docs/index.md) is the user guide;
[.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md) contains contributor checks.

## Authority and scope

1. The current user request defines the authorized change. Preserve unrelated edits.
2. Active specs define intended behavior. Public Python types, validators, CLI
   help and tests establish what exists today. Resolve disagreement explicitly;
   do not silently implement a research proposal or redefine a contract in prose.
3. Research, old reviews and plans are evidence, not an implementation backlog.
   Historical action records alone do not authorize a new run. Explicit user
   authorization remains valid within its scope until revoked.

The root uv project builds the in-memory `foliqant` library in `src/foliqant/`:
compile configured workflows, run bounded steps, return a result, evaluate explicit
ground truth. `model/` is the separate training/curation uv project. It reuses
`foliqant.decisions`; the library never imports model tooling. Runnable hosts and
business workflows belong in `examples/`. No persistence, queue workers, incoming
transport package, application authentication, PURISTA, Harness or Voyage is in scope.

## Decisions and changes

Use existing public APIs and defaults. Mechanical fixes, documentation alignment
and private reversible implementation choices within the request do not need an
extra approval step. If a missing decision changes public behavior, data meaning,
security boundaries or scope, report the exact gap and ask one focused question;
continue independent authorized work. Do not invent APIs, compatibility shims,
fallbacks, future infrastructure, guarantees or approval ceremonies.

Update contract types, generated schemas, tests, examples, public guides and the
relevant skill together when a public surface changes. Runtime fields are
snake_case; existing native decision/model wire fields retain their camelCase
names. Do not rename immutable artifacts or change old recipe identities.

Reuse the same compiler for inline and file-based steps/schemas. Reuse the marked
deployment-field environment resolver; never expand prompts or input data. Every
runnable example needs explicit golden pipeline/step evaluations, with offline
wiring checks distinguished from opt-in live model measurements.

## Execution and data

Default verification is offline. Live inference, native training, downloads and
publishing follow existing user authorization within its scope; old plans alone
are not authorization. Never
contact a model endpoint just to check docs or change shared environments during
an active run. Real lifecycle acceptance needs real execution evidence; unit
doubles and small synthetic examples cannot establish model quality.

Keep customer data, golden corpora, generated datasets, weights, adapters,
checkpoints, predictions, secrets and logs outside Git. Tests create minimal
temporary records in code. Preserve source rights, split isolation, lineage and
artifact immutability. Research/noncommercial data is usable only within its
terms; private hosting is not permission. Do not accept gated terms, upload data
or use paid services unless authorized.
The default model workspace is the ignored `.foliqant/` directory in this
checkout; installed commands use their current directory. Use the shared
workspace selector/Git policy. Relocation must not rewrite immutable records.

## Verification

Run the affected checks in [.agent/IMPLEMENTATION.md](.agent/IMPLEMENTATION.md).
Run docs and tracked-data audits for guidance/skill changes and a strict MkDocs
build for published documentation. Report exact outcomes and skipped live checks;
do not convert previous test counts into current evidence.

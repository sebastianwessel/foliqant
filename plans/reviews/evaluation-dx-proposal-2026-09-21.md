# Evaluation DX research and decision record

Date: 2026-09-21. Research baseline: `6bf0297`.
Status: superseded proposal; retained as research and decision rationale.
Behavior authority: [approved Python package specification](../../specs/11-python-package.md).
Usage: [testing and evaluation guide](../../docs/guides/testing-and-evaluation.md).
This record is not a backlog or authorization for additional execution.

## Approved implementation

Keep the existing bounded async evaluator and application `run`/`run_step` APIs.
The standard wheel gains no evaluation dependency or second scheduler. Optional
`evaluation.dataset` in application configuration points to a strict JSON manifest;
startup/validate/doctor never read or stat private gold. Evaluation metadata does
not revise the runtime configuration digest.

Suites select a workflow and optional isolated step. Cases may be inline or in
separate JSON arrays, resolved relative to the main dataset. Both forms use the
same validation and immutable Python evaluation values. Authors supply gold;
labels, workflow routing and business policy are never inferred from predictions.

`foliqant evaluate` executes explicitly; `--check` validates offline and `--replay`
scores saved results without inference. Replay matches targets, identities,
inputs and execution revisions, including failed invocations; revised gold is
allowed. New private artifacts retain full public results for diagnosis/replay.
Stdout stays content-free. Files are never overwritten; datasets/case files are
bounded at 64 MiB, report writing and replay at 256 MiB.

Exact/set assertions remain distinct from classification/multilabel aggregates.
Metrics use explicit catalogs, support/coverage, unavailable-output counts and
confusion/per-label counts. They introduce no numeric gate or implicit judge.
Examples demonstrate wiring with synthetic gold; they do not qualify a model.
Skills help with mechanics while preserving user authority over business gold.

## Research and library choice

Primary sources consulted on 2026-09-21:

- [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/) and
  [serialization](https://pydantic.dev/docs/ai/evals/how-to/dataset-serialization/):
  typed cases, reusable evaluators, file schemas and local execution.
- [Pydantic report evaluators](https://pydantic.dev/docs/ai/evals/evaluators/report-evaluators/):
  dataset analyses are separate from individual case assertions.
- [Inspect rescoring](https://inspect.aisi.org.uk/scoring-workflow.html):
  reuse recorded responses when scoring changes.
- [MLflow scorers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/):
  explicit context and structured feedback.
- [LangSmith evaluator types](https://docs.langchain.com/langsmith/evaluation-types)
  and [local execution](https://docs.langchain.com/langsmith/local):
  case, summary and pairwise evaluation need not require hosted execution.
- [Scikit-learn metrics](https://scikit-learn.org/stable/modules/model_evaluation.html):
  explicit label universes, denominators and multilabel averaging semantics.

Pydantic Evals was the strongest external candidate. Its
[version-matched metadata](https://raw.githubusercontent.com/pydantic/pydantic-ai/v2.46.0/pydantic_evals/pyproject.toml)
includes `pydantic-ai-slim`, already used here. Dependency size was not decisive;
its [execution model](https://raw.githubusercontent.com/pydantic/pydantic-ai/v2.46.0/pydantic_evals/pydantic_evals/dataset.py)
would duplicate scheduling, deadlines, accounting and report semantics. Retaining
one engine avoids two behavior contracts. Public scorer adapters remain possible.

## Deferred ideas, not scheduled work

Slices, metric thresholds, numeric custom scores, macro/micro-F1 summaries,
paired uncertainty estimates, repeat orchestration, model judges, automatic
prompt optimization and hosted dashboards were not adopted. JSONL and a separate
evaluation YAML/CLI hierarchy from the original proposal were also not adopted.
Future scope requires a concrete user need; this research does not mandate it.

## Implementation verification

Completed against the updated working tree on 2026-09-21:

- Runtime suite: 681 passed. Configuration tests additionally rerun after the
  final status adjustment: 30 passed.
- Model-tooling regression suite: 939 passed, 7 deselected; no live inference.
- Strict mypy (92 source/example files), Ruff checks/formatting, 13 generated
  library schemas and specification integrity checks passed.
- Documentation audit: 41 guide/skill references and 57 CLI examples; strict
  MkDocs and skill validation passed. The local docs audit used the current
  source through `PYTHONPATH=src:model/src` because the model environment retains
  a previously installed runtime wheel.
- Model-free inline/split-file execution, mismatch controls and saved-result
  replay passed. Private files were created only in temporary directories.
- Independent review caught and verified fixes for all-error workflow identity,
  Unicode report serialization and consistent report size bounds.

These checks establish implementation/wiring behavior, not model quality. No
model requests, training, dataset downloads or external publication occurred.

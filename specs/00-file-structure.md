# File and ownership structure

`pyproject.toml` and `uv.lock`: single Python distribution for model tooling. `model/src/foliqant_model/`: a contracts/ package with explicit public re-exports, CLI, data, artifacts, MLX backend, training, evaluation, calibration and export modules. Keep backend imports lazy. `model/tests/`: unit/contract/CLI tests and a marked real integration lifecycle. `model/examples/`: YAML recipes and optional diagnostic-data generation code, never dataset files. Downloaded/prepared data and model weights live in the external local setup workspace; project-local overrides must be ignored. `model/base/`, `model/customization/`, `model/evaluation/`, `model/export/`: task-oriented recipes and pointers, not parallel copies of code. `contracts/model/`: generated schemas. `scripts/`: schema/docs/skill verification commands. `skills/foliqant-model/`: installable agent skill. `docs/`: user guides. `specs/`: implementation requirements and research. `plans/`: readiness/review evidence and implementation tracking. `artifacts/`, `runs/`, `.venv/`: ignored local outputs. Existing `service/`, `workflows/`, `config/service.example.yaml` are outside this goal and must remain clearly separate proposals.

No package imports the workflow service or PURISTA. Data and artifact modules are MLX-independent. MLX backend depends on typed contracts and subprocess utilities, not CLI parsing. CLI composes modules. Evaluation and export use backend interfaces and verified artifact identities. Canonical closed contract types are reused, and schema generation is one-way from their source.

`model/src/foliqant_model/curation/` owns pinned source conversion, family planning, local endpoint generation, candidate checking, and resumable dataset publication. Its JSON source catalog is packaged configuration; downloaded records, model responses, outcomes and corpora remain outside Git.

Native decision contracts, deterministic oracle scenarios, source projections
and coverage accounting remain under the curation package and reuse its runner,
endpoint and storage boundaries. `model/examples/native-full.yaml` and
`model/examples/native-pilot.yaml` are versioned recipes; `scripts/generate-data`
is a thin command wrapper. Generated native data and reports remain outside Git.

Within that package, `decision_seeds.py` owns seed assembly, stable semantic
family/record identity and source projection.
`decision_adequacy_cases.py` owns the four whole-answer-adequacy scenarios, and
`decision_research_cases.py` owns the six research-derived scenarios. These two
case catalogs are private logical helpers: they import the native decision
contracts only, expose no CLI or package public API, add no dependency, and do
not call endpoint, storage, runner or publication code. Remaining authored
scenario cases stay in `decision_seeds.py` until another coherent catalog
boundary is specified.

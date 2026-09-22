# Documentation architecture

Public docs follow the reader's path from a working installation to a configured,
tested process. Verify examples against public contracts and runnable examples;
do not infer new capabilities from the desired documentation structure.

Top-level navigation is Overview, Guide, Tutorials, Evaluation, and Reference.
Guide contains Getting started (including Claude/Codex skill setup), Configure,
and Step types. Getting started is a topic within Guide, not a separate tab.

## Reading order and page ownership

| Section and page | Purpose and section sequence |
| --- | --- |
| Overview (`docs/index.md`) | Purpose → AI plus deterministic control → workflow/flow/step model → learning path |
| Getting started (`getting-started/runtime.md`) | Install → scaffold → configure endpoint → validate → run → embed → configuration next step |
| Concepts (`concepts/runtime.md`) | Boundaries → execution lifecycle → selected context → review versus failure |
| Configuration overview (`configuration/index.md`) | Ownership diagram → expected folder tree → discovery/names/path rules → smallest complete configuration → validate → next topics |
| Workflows (`configuration/workflows.md`) | Input schema → defaults/start → flow instances → output → transition/review rules → validation |
| Flows (`configuration/flows.md`) | Resolved input → ordered steps → output projection → definition reuse/callable distinction → worked example |
| Context (`configuration/context.md`) | Binding scopes → literals/pointers/defaults → earlier outputs → prompt placeholders → schema paths → prompt trust |
| Models (`configuration/models.md`) | Install adapter → profile/provider choice → environment → workflow default and step overrides → output/capability settings → limits/retries |
| MCP (`configuration/mcp.md`) | Read-only runtime boundary → HTTP/stdio profile → declared catalog → direct step versus loop → host credentials/authorization → validation/troubleshooting |
| Step overview (`steps/index.md`) | Choose a step type → common configuration → file formats → execution/review semantics → per-type links |
| Decision (`steps/decision.md`) | Sources → single/multiple typed questions → catalogs/criteria → result/selection/fallback → evaluation pointers |
| LLM (`steps/llm.md`) | Text → schema extraction → prompts/context → output modes/validation → focused example |
| Handler (`steps/handler.md`) | Trusted async function → input/output schemas → registration → step config → result/failure → testing |
| MCP step (`steps/mcp.md`) | Declared server/tool → arguments → output/schema → authorization/failure → focused example |
| Agent loop (`steps/agent-loops.md`) | Bounded LLM step, not a separate workflow type → model/tool prerequisites → allowlists → tool/result loop → budgets/failure → tests |
| Collection (`steps/flow-collection.md`) | Host-owned plan → callable flows → items/allowlist/limits → sequential execution → ledgers/disposition → evaluation |
| Evaluation overview (`evaluation/index.md`) | Gold versus wiring tests → workflow/flow/step scopes → offline versus live → learning path |
| Ground truth (`evaluation/ground-truth.md`) | Reviewed criteria → representative/ambiguous/negative cases → manifests/case files → holdouts/families/languages → expectations → privacy |
| Task scoring (`evaluation/task-types.md`) | Choice/ordinal/predicate → multilabel → extraction/spans → request units → tool/handler/collection and routing expectations → metric limits |
| Running (`evaluation/running.md`) | Discovery/config path → offline check → scoped/live execution → concurrency/repeat/timeouts → Python embedding → replay/compare → CI |
| Reports (`evaluation/results.md`) | Full observations → confusion and label metrics → review/error/missing denominators → latency/usage → comparison/privacy |
| Unit tests (`evaluation/unit-testing.md`) | Public lifecycle with local fakes → handler/model/tool isolation → assertions → what mocks do not prove |
| Tutorials (`tutorials/`) | Six focused runnable use cases; link to configuration and evaluation details rather than duplicating reference tables |
| Reference (`reference/`) | Exact input/output/evidence/error contracts and deployment/CLI lookup |
| AI-assisted setup (`skills/foliqant.md`) | Install skill → provide business rules and examples → review configuration → validate and evaluate |

## Authoring and verification

Each guide explains what to edit, the exact path, relevant required/default fields,
how values connect to other files, expected behavior, and the next useful page.
Use runnable repository examples as evidence; mark fragments explicitly. Keep
YAML block style, use tables for choices and fields, and add Mermaid diagrams
where they explain ownership, data flow, or lifecycle. Do not claim that structural
validation proves model correctness or security.

The old broad workflow/evaluation guides are replaced by focused sections, not
retained as duplicate manuals. Update all local links and navigation together.
Run contract/example checks offline, all repository checks, strict MkDocs, and
inspect the rendered navigation before publishing. No model calls are needed.

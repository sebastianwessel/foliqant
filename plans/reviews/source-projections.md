# Offline source-projection verification

Date: 2026-09-20. Status: offline implementation verified; real-model pilot deferred.

## Isolation and baseline

The pre-existing implementation was committed on `main` as `301516d` after
offline checks and staged-data auditing. New work lives on
`feat/source-projections` in the independent `foliqant-source-projections`
worktree with its own virtual environment. Nothing was pushed or merged back
into the active generator's checkout.

No additional model, model-discovery, cloud, training, or download requests were
made during this implementation. Endpoint tests use an isolated fake HTTP
server. Native model integration tests remain excluded by explicit instruction.

## Actual frozen-source preparation

Parent: `native-financial-decisions-en-de-v1-continue-3beaee64b8c2` under the
external Foliqant workspace's `curation/` directory. Both public preparation
commands completed while its generation continued. SHA-256 checks of its five
source snapshots, configuration, source plan, native seeds, native family map,
and job plan matched before and after preparation. Mutable progress and outcome
files were neither changed nor used by preparation.

The final full plan is under `projections/af0f28946a6ca6be37655b953942c06652d30f7fb8433321ca437287555bf609/`.
The final pilot plan is under `projections/c1eff3616e9c730a2ad9e689dc6a7ebf6ce28abfc6517fa20d90bbd7fd8f561a/`.
Both contain `projection-plan.json` and `projection-report.json`, outside Git.
Both passed deterministic reload and reconstruction against the parent inputs.

| Source | Raw rows audited | Full selected tasks | Training tasks | Selected families |
| --- | ---: | ---: | ---: | ---: |
| MultiDoGO finance | 2,000 | 500 | 284 | 324 |
| typed-decisions | 1,600 | 500 | 303 | 500 |
| TAT-QA | 2,000 | 53 | 36 | 53 |
| Total new projections | 5,600 | 1,053 | 623 | 877 |

BANKING77 and WANLI contribute another 4,000 inspected snapshot records, but
their existing projections are unchanged. Their `unsupported-source` exclusions
mean they are not additional projections, not that their original tasks were
removed. All 1,600 typed-decisions cases and their 8,000 questions were audited.

The strict TAT-QA implementation finds 53 eligible independent contexts, 36
training contexts, rather than the preliminary estimate of 52/35. It selects
adjacent uniquely labelled year columns, including nonconsecutive years, and
skips duplicate row labels. Original QA answers do not construct targets.

The full plan preserves 95 calibration, 221 test, 623 training and 114 validation
tasks. All new imported tasks remain English. Existing authored German records
and English enum values are unchanged. New training jobs require later blind
verification; these counts establish neither correctness nor model acceptance.

MultiDoGO has only three eligible multi-intent rows. The full plan retains all
three; the training-only pilot retains the two training rows. All 24 rows in
12 conflicting task groups are excluded before selection. Overall new-source
deduplication excludes 273 repeated tasks. No conflicting member is selected
arbitrarily and no new alias may cross a frozen split or family.

The pilot has exactly 32 training tasks from 32 families: eight MultiDoGO, eight
TAT-QA and 16 typed-decisions, four from each workflow. Its model execution and
review of accepted/rejected responses remain deferred until generation ends.

## Regression evidence

- Offline suite: 759 passed; seven native integration tests deselected.
- Strict mypy: 61 source files passed.
- Ruff lint and format checks: passed, including scripts.
- Generated schemas: all 25 match and pass Draft 2020-12 schema validation.
- Documentation checks: 24 guides/skill references and 33 CLI examples passed.
- Repository model skill: skill-creator validation passed.
- Staged tracked-data audit, credential/path scan and whitespace check passed;
  no `.env`, datasets, weights, generated responses or environments are staged.
- Extension tests preserve old outcomes byte-for-byte, carry quarantines,
  reject incomplete/active parents and wrong plan ancestry, enforce the total
  candidate budget, resume an interrupted child, and repair only quarantines.
- Offline tests fail on any attempted discovery, generation or source download.
- Projection tests cover full multi-intent retention, explicit teacher labels
  including ties, all five questions, strict signed numeric comparisons, exact
  evidence, exclusions, rights and split preservation.

Two independent bounded code reviews found and resolved parent-plan binding
and inherited-plan verification issues; final review found no remaining high
or critical correctness findings. This is code verification, not a data-quality
or financial-domain accuracy claim.

## Endpoint cleanup

The runner discovers its model once per invocation through `/v1/models`.
Generation workers receive the verified identity instead of rediscovering it.
Provider-specific `/api/v1/models` probing and its unused parser are removed.
A fake-server regression records exactly one `/v1/chat/completions` request and
zero discovery requests for a worker supplied with the run identity. Mismatched
configured/requested/observed/response model IDs still fail, and size/schema/
deadline checks remain in place. Request payloads and cache identities are
unchanged. The saved Splash identity matches the new compatibility-only
identity reconstruction without contacting the server.

No throughput claim is made: benchmarking the live backend would violate the
no-additional-model-work constraint. The active generator continues running
the committed baseline until the updated code is deliberately adopted later.

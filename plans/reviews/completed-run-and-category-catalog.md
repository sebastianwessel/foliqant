# Completed bilingual run and category authoring

Date: 2026-09-20. Scope: completed-run verification, local integration of the
offline source-projection work, additive category authoring, and updated
business-process/dataset guidance. No model calls or training were launched.

## Completed artifact

Parent: `native-financial-decisions-en-de-v1-continue-3beaee64b8c2` in the external
Foliqant workspace. All 865 jobs finished: 654 accepted and 211 quarantined.
The native-decisions artifact
`844c86503e824db6c90442f54ec7a9759c9ae358bbc0e393f95b9837a9dc587d`
passed CLI integrity verification. It contains 1,245 records: 846 train,
108 validation, 111 calibration and 180 test; 1,028 English and 217 German.
Accepted jobs are not identical to published rows or a measure of accuracy.

All 1,264 native seeds and 1,245 published decisions load under current contracts.
Main's effective configuration matches the parent's stored configuration.
The generation recipe digest and the V1 input/output schemas are unchanged.
The repair preflight retains all 654 accepted jobs and selects exactly 211
quarantines. A temporary child-copy check preserved 654 accepted outcome files
and 952 cache files byte-for-byte; no actual model repair pass was performed.

Repair snapshots now acquire the existing run lock and check completion and
coverage against the outcome ledger. An active or inconsistent parent is
rejected; finished native runs failing only coverage remain repairable.
No parent data, configuration, outcomes or published artifacts were rewritten.

## Category boundary

`CategoryCatalog` normalizes new printable ASCII identifiers to lowercase
snake_case and detects collisions after normalization. Descriptions are required,
nonblank, and retain their original English/German text. The helper converts
to existing `DecisionOption` values. `resolve_id` normalizes formatting and
requires exact membership; it does not guess categories. Legacy identifiers
such as `label-000` stay valid in frozen V1 artifacts.

The new catalog schema describes raw authoring input; JSON Schema does not
normalize data or check normalization collisions. Tests cover the runtime
transformation, collision rejection, blank descriptions, multilingual prose,
schema portability and unknown output keys. The guide's catalog example runs
without inference and constructs a valid three-option question.

## Verification

- Offline suite: 778 passed; seven native integration tests deselected.
- Strict mypy: 62 source files passed.
- Ruff lint and formatting: passed across 120 files.
- Generated schemas: 26 files match; schema validation passed.
- Documentation: 24 guides/skill references and 33 CLI examples passed.
- Model skill validation and canonical spec checks passed.
- Initial full-suite failures were a stale schema-count assertion and the host
  system Git/Xcode setup. The assertion now checks the registry's exact file set;
  verification used the existing bundled Git executable. The complete rerun passed.

## Next sequence

Run repair from the original checkout, which owns the matching `.env`, using
the completed continuation as `--repair-from`. After repair completes, prepare
new pilot/full projection plans against its child path. Plans prepared from the
earlier continuation are not portable to the repair child. Review the sequential
32-task projection pilot before a full extension.

NLU++ is the recommended next acquisition candidate; MAILEx follows for thread
events after its external data files are pinned. Tobi-Bueck support tickets are
a supplementary synthetic research candidate with inconsistent priority/type
annotations and no date/confirmation gold. No candidate was added to the current
acquisition manifest. Per-label evidence, extraction, thread reconciliation and
workflow execution remain target features, not delivered service behavior.

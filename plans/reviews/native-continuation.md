# Native continuation after generator updates

Date: 2026-09-20. Explicit `--continue-from` implemented and verified.

The parent `native-financial-decisions-en-de-v1-38d8294835d2` has 178 completed
outcomes (162 accepted, 16 quarantined), with 687 of 865 candidates unfinished.
The public `generate-data --continue-from <parent> --prepare-only --offline`
command created `native-financial-decisions-en-de-v1-continue-3beaee64b8c2`.
All 178 carried outcome files are byte-identical to their parents. All 588
original outcome/call files still match the hashes recorded before timeout
diagnosis. The continuation copied 409 call records associated with completed
outcomes; the unfinished candidate's partial call is not claimed as completed.

A real orchestration verification discovered the configured model, revalidated
the carried data and reached `completed=178 accepted=162 quarantined=16
reused=178 cached=409`. A control checkpoint then stopped before any unfinished
candidate, so no new model inference or full generation was started. Private
evidence is under `~/.local/share/foliqant/checks/continuation-2026-09-20/`.

The immutable child records the parent snapshot and current recipe in
`continuation.json`. Carried jobs keep original IDs and generation provenance;
unfinished jobs use current recipe IDs. Current semantic, source-preservation
and partition checks still apply. Same-command resume uses the child. Completed
children remain eligible for a separate rejection-only repair pass.

Tests cover partial-run continuation across recipe changes, no regeneration of
completed outcomes, preserved quarantines and provenance, unchanged parent bytes,
child resume/publication, subsequent rejection repair, corrupt parent rejection,
configuration mismatch, mutually exclusive options and CLI forwarding.

Verification: 715 offline tests passed; 7 native training integrations deselected.
Strict mypy, Ruff lint/format, 23 schemas, docs, skill validation and whitespace
checks passed. No native training or population-quality claim is made.

# Curation progress and pause verification

Date: 2026-09-19. Scope: terminal progress and safe pause/resume for the existing
curation runners. No data contract, generation recipe identity, or model-quality
claim changes.

The CLI adds `--progress auto|always|never` on stderr. Runtime controls are not
part of the dataset configuration or immutable run identity. The first SIGINT
requests a pause at a safe boundary; a second interrupts immediately. Completed
requests and outcomes continue to use the existing verified caches and lock.

## Real local check

A separate two-candidate recipe used the configured local
`qwen3.8-27b-splash` endpoint with serial generation. The recipe and logs remain
outside Git. No full generation or training was started.

1. Sent SIGINT during the first candidate's model execution.
2. Observed elapsed-time heartbeats and `pause=requested` on stderr.
3. The two calls for that candidate finished; one outcome was persisted. The
   process exited 130 with typed `INTERRUPTED`, empty stdout, and no completed
   report. The second candidate had not started.
4. Reran the exact same wrapper command. It reacquired the run lock, displayed
   `reused=1`, and processed only the second candidate. All three previously
   saved request/outcome files retained their modification times.
5. The second candidate needed one new model call: its rewrite changed a number
   and was correctly quarantined before a solve call. This is a validation
   outcome, not a resume failure. Final counts were one accepted and one
   quarantined candidate.
6. Verified the resulting dataset artifact:
   `c1b222b9f6b8c5ea9a0fd14dcb69784b398643f68b06c379e4b63c5b7f8271d7`.

The terminal-equivalent process-group check exposed duplicate SIGINT delivery
through a long-lived `uv run` parent. The wrapper now resolves the synchronized
Python interpreter, then directly execs it. Repeating the check with SIGINT sent
to the whole foreground group produced one graceful pause, exit 130, empty
stdout, and no forced-interruption notice. A cached preparation run was used;
this check made no generation request.

The pre-existing eight-candidate pilot also resumed under `--progress never`:
its run identity and all 24 cached call/outcome modification times were
unchanged, and no Foliqant progress messages appeared on stderr. Changing the
progress setting therefore did not restart or regenerate the dataset.

The external run is `native-progress-pause-check-v1-224129b0c08a`.
Immediate interruption may leave an unfinished request to repeat, and terminating
the client does not guarantee cancellation at the remote model server. Normal
resume skips completed work; changing the effective recipe or model configuration
deliberately produces a different run.

## Final checks

The full offline suite passed: 357 tests, with seven unrelated native lifecycle
integration tests deselected. Ruff lint/format, strict mypy (50 source files),
23 generated schema checks, documentation checks, skill validation, tracked-data
audit and `git diff --check` passed. Focused tests include handler restoration,
first/second interrupts, unsafe-stop notices, TTY redraw, wrapper process-group
delivery, and reuse of persisted work. The real checks above supplement these
tests; no checkpoint was trained.

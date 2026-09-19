# Verify and recover artifacts

A completed artifact is a directory with a `manifest.json` and the files listed
in that manifest. Its identity binds the metadata and file checksums. Do not edit
completed artifacts; produce a new output directory for a new run.

```sh
uv run --no-sync foliqant-model verify /absolute/path/to/artifact
```

A successful result includes `valid: true`. Verification checks recorded
integrity and lineage. It does not certify legal clearance, model quality or
compatibility with every inference server.

## Recognize a failed command

Success writes one JSON result to standard output. Failure writes its final JSON
error to standard error and leaves standard output empty.

| Exit code | Meaning |
|---|---|
| `2` | Invalid arguments, configuration or input |
| `3` | Missing dependency or unsupported environment |
| `4` | Execution, network, timeout or file IO failure |
| `5` | Integrity, lineage or leakage failure |
| `130` | User interruption |

The setup wrapper also runs uv, whose installation failures use uv's own output
and exit codes before the model CLI starts. Offline setup requires cached Python
build dependencies as well as cached model/data files.

## Recover safely

- **Existing output:** choose a new directory. Artifact commands do not offer a
  force-overwrite switch.
- **Altered file:** retain the failed artifact for investigation. Restore trusted
  bytes or rebuild in a new directory; do not change the manifest to make it pass.
- **Interrupted run:** inspect the sibling `.<output>.run-<runId>` workspace.
  Partial files are not completed artifacts.
- **Existing lock:** inspect `.<output>.lock`, its run ID and workspace. Check that
  the original process has stopped before manually removing the stale lock. A
  PID alone is insufficient because operating systems reuse PIDs.
- **Interrupted setup:** verified downloads remain reusable. Rerun setup. A
  conflicted or incomplete child artifact needs inspection before retrying;
  setup never treats a directory's existence as success.
- **Interrupted curation:** rerun `curate` with the same configuration and
  workspace. Verified downloads, the frozen source plan and completed candidate
  outcomes are reused. A changed configuration creates another run; changed
  model metadata is refused for an existing generation run.
- **Curation lock:** inspect the run's `.lock` metadata and process before
  removing it. A timed-out endpoint request does not prove that the model server
  stopped its work.

Private outputs use file permissions `0600` and directories `0700` on POSIX.
Keep backups and logs private too: full records, predictions and backend logs can
contain source material. Foliqant does not upload your data automatically.

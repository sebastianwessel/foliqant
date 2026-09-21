# Execution evidence

These records preserve dated implementation, experiments and reviews.
[The active specs](../specs/README.md) define current scope; old plans are not a
backlog and old execution records do not grant new authorization.

- [Python package status](python-package-status.md): current in-memory library and evaluation.
- [Model lifecycle status](implementation-status.md): separate model toolchain.
- [Curation status](curation-status.md): dataset run evidence and its limitations.
- `reviews/`: historical snapshots; test counts and active-run notes are valid
  only for their recorded revision/date.

In particular, `service-*` reviews document intermediate designs superseded by
the in-memory package. PostgreSQL, durable workers, deployment and inbound
transport findings there do not imply supported or pending package features.
Keep historical evidence intact when changing current guidance; source history
remains available in Git.

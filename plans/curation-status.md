# Automated curation implementation

Tooling implementation and acceptance are complete. Canonical contract:
[automated curation](../specs/08-automated-data-curation.md). Detailed evidence:
[curation acceptance](reviews/curation-execution.md) and
[measurements](reviews/curation-measurements.json).

Delivered: five pinned importers; safe family splitting and provenance; local
JSON/schema generation; authored scenarios; independent checker pass; quarantine;
immutable caches and resumable publication; curate CLI; one-command wrapper and
recipe; documentation, skills, generated schemas and packaging.

Verified: 4,998 source records; real local pilot 1 accepted / 7 quarantined; exact
cache/artifact reuse; three verified published datasets; two-step native training
compatibility; 257 offline tests and all static/content/package checks.

Model qualification remains separate: the small generator has low acceptance
yield, while the Qwen corpus pilot exceeded its configured request deadline.
Prompt transport compatibility was demonstrated, but Qwen corpus generation and
German quality are not claimed as successful. No unsafe samples were promoted
and no manual data work is required. No commit or push was performed.

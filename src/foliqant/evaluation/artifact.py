"""Explicit private evaluation artifact output; runtime execution never writes reports."""

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .contracts import EvaluationReport

MAX_REPORT_BYTES = 256 * 1024 * 1024


def write_report(
    path: Path,
    reports: Sequence[EvaluationReport],
    *,
    mode: str,
    dataset_name: str,
    dataset_revision: str,
) -> None:
    """Atomically publish a new owner-readable JSON report without overwriting.

    Detailed reports contain sensitive inputs, gold and full returned results.
    Keep them outside Git and ordinary CI logs. No directories are uploaded.
    """
    document = {
        "version": 1,
        "mode": mode,
        "dataset": {"name": dataset_name, "revision": dataset_revision},
        "reports": [report.to_dict() for report in reports],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".evaluation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, indent=2)
            size = 0
            for chunk in encoder.iterencode(document):
                size += len(chunk)  # Escaped ASCII is one byte per character.
                if size + 1 > MAX_REPORT_BYTES:
                    raise ValueError("evaluation report exceeds size limit")
                stream.write(chunk)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive publication, including dangling symlinks.
    finally:
        os.unlink(temporary)

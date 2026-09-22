"""Explicit private evaluation artifact output; runtime execution never writes reports."""

import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import EvaluationReport

MAX_REPORT_BYTES = 256 * 1024 * 1024
REPORT_MODES = frozenset(
    {"execution", "replay", "live_model", "offline_wiring", "offline_asgi", "local_stdio"}
)


def report_document(raw: object) -> dict[str, Any]:
    """Validate the closed report envelope shared by replay and comparison."""
    if not isinstance(raw, dict) or set(raw) != {"mode", "dataset", "reports"}:
        raise ValueError("invalid evaluation report envelope")
    if raw["mode"] not in REPORT_MODES:
        raise ValueError("invalid evaluation report mode")
    return raw


def default_artifact_path(directory: Path, *, prefix: str = "report") -> Path:
    """Return a collision-resistant path for a private evaluation artifact."""
    return directory / f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"


def write_private_json(path: Path, document: Any) -> None:
    """Atomically publish bounded JSON to a new owner-readable file.

    The destination is never overwritten, including when it is a dangling
    symlink. Temporary files are removed after every success or failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".evaluation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, indent=2)
            size = 0
            for chunk in encoder.iterencode(document):
                size += len(chunk)  # Escaped ASCII is one byte per character.
                if size + 1 > MAX_REPORT_BYTES:
                    raise ValueError("evaluation artifact exceeds size limit")
                stream.write(chunk)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive publication, including dangling symlinks.
    finally:
        os.unlink(temporary)


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
    if mode not in REPORT_MODES:
        raise ValueError("unknown evaluation report mode")
    document = {
        "mode": mode,
        "dataset": {"name": dataset_name, "revision": dataset_revision},
        "reports": [report.to_dict() for report in reports],
    }
    write_private_json(path, document)

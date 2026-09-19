"""Adversarial filesystem boundaries for evaluation inputs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_evaluation_reader_rejects_file_swapped_to_fifo_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "split.jsonl"
    source.write_bytes(b"{}\n")
    package_root = Path(__file__).parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(package_root), environment.get("PYTHONPATH", "")]
    )
    script = """
import os
import sys
from pathlib import Path
from foliqant_model import evaluation
from foliqant_model.errors import ModelError

target = Path(sys.argv[1])
original_open = os.open
swapped = False

def racing_open(path, flags, *args, **kwargs):
    global swapped
    if not swapped and Path(path) == target:
        swapped = True
        target.unlink()
        os.mkfifo(target, mode=0o600)
    return original_open(path, flags, *args, **kwargs)

evaluation.os.open = racing_open
try:
    evaluation._read_regular(target, label="dataset split", max_bytes=1024)
except ModelError as error:
    raise SystemExit(0 if error.code == "INTEGRITY_FAILED" else 2)
raise SystemExit(3)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr

#!/usr/bin/env python3
"""Reject model weights and dataset files accidentally added to Git's index."""

import argparse
import subprocess
from pathlib import Path

FORBIDDEN_SUFFIXES = {
    ".safetensors",
    ".gguf",
    ".pt",
    ".pth",
    ".ckpt",
    ".bin",
    ".onnx",
    ".h5",
    ".jsonl",
    ".ndjson",
    ".csv",
    ".tsv",
    ".parquet",
    ".arrow",
    ".feather",
    ".npy",
    ".npz",
}
FORBIDDEN_ROOTS = {"data", "artifacts", "models", "checkpoints", "outputs", "runs", ".foliqant"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git", default="git", help="Git executable to inspect the index")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [args.git, "-C", str(root), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        print("Cannot inspect Git's index. Supply a working Git executable with --git.")
        return 2
    paths = [
        Path(name.decode("utf-8", errors="surrogateescape"))
        for name in result.stdout.split(b"\0")
        if name
    ]
    forbidden = sorted(
        str(path)
        for path in paths
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.parts[0] in FORBIDDEN_ROOTS
    )
    if forbidden:
        print("Dataset/model files must not be tracked:")
        for path in forbidden:
            print(f"  {path}")
        return 1
    print("Tracked-data audit passed: no dataset files, model weights or generated output roots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

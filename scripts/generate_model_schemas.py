#!/usr/bin/env python3
"""Generate or check deterministic model contract JSON Schemas."""

from __future__ import annotations

import argparse
from pathlib import Path

from foliqant_model.schemas import SCHEMAS, schema_bytes


def generate(output: Path, *, allow_existing: bool = False) -> int:
    if output.exists() and any(output.iterdir()) and not allow_existing:
        print(f"output directory must be new or empty: {output}")
        return 1
    output.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMAS.items():
        (output / filename).write_bytes(schema_bytes(filename, model))
    return 0


def check(output: Path) -> int:
    expected_names = set(SCHEMAS)
    actual_names = (
        {path.name for path in output.glob("*.schema.json")} if output.exists() else set()
    )
    drift: list[str] = []
    for filename, model in SCHEMAS.items():
        path = output / filename
        expected = schema_bytes(filename, model)
        if not path.exists() or path.read_bytes() != expected:
            drift.append(filename)
    drift.extend(sorted(actual_names - expected_names))
    if drift:
        print("schema drift: " + ", ".join(sorted(set(drift))))
        return 1
    print(f"model schemas match ({len(SCHEMAS)} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, metavar="DIR")
    mode.add_argument("--check", type=Path, metavar="DIR")
    mode.add_argument(
        "--maintenance-output",
        type=Path,
        metavar="DIR",
        help="replace generated files in the maintained repository schema directory",
    )
    args = parser.parse_args()
    if args.output is not None:
        return generate(args.output)
    if args.maintenance_output is not None:
        return generate(args.maintenance_output, allow_existing=True)
    return check(args.check)


if __name__ == "__main__":
    raise SystemExit(main())

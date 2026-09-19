#!/usr/bin/env python3
"""Check local guide/skill links and references to implemented CLI commands."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

from foliqant_model.cli import _parser
from foliqant_model.errors import ModelError


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = _parser()
    commands = {
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }
    failures: list[str] = []
    documented: set[str] = set()
    parsed_examples = 0
    documents = sorted(
        [
            root / "README.md",
            *root.joinpath("docs").rglob("*.md"),
            *root.joinpath("skills").rglob("*.md"),
            *root.joinpath("model").glob("*/README.md"),
        ]
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        for destination in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = destination.strip().split(" ", 1)[0].strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            if not (path.parent / unquote(url.path)).exists():
                failures.append(f"{path.relative_to(root)}: missing link {target}")
        for command in re.findall(r"foliqant-model ([a-z][a-z-]*)", text):
            documented.add(command)
            if command not in commands:
                failures.append(f"{path.relative_to(root)}: unavailable command {command}")
        for block in re.findall(r"```(?:sh|bash|shell)\n(.*?)```", text, flags=re.DOTALL):
            joined = block.replace("\\\n", " ")
            for line in joined.splitlines():
                command_line = re.fullmatch(
                    r"\s*(?:uv run --no-sync )?foliqant-model ([^\n]+)", line
                )
                if command_line is None:
                    continue
                try:
                    _parser().parse_args(shlex.split(command_line.group(1)))
                    parsed_examples += 1
                except (ModelError, ValueError):
                    failures.append(f"{path.relative_to(root)}: example arguments do not match CLI")
        if re.search(r"/Users/[^/]+/", text):
            failures.append(f"{path.relative_to(root)}: author-specific filesystem path")
    for missing in sorted(commands - documented):
        failures.append(f"Missing lifecycle command documentation: {missing}")
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(
        f"Documentation checks passed: {len(documents)} guides/skill references, "
        f"{parsed_examples} CLI examples"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

from foliqant.cli import _parser as runtime_parser


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    model_parser = _parser()
    model_commands = {
        name
        for action in model_parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }
    package_parser = runtime_parser()
    package_commands = {
        name
        for action in package_parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }
    failures: list[str] = []
    documented_model: set[str] = set()
    documented_package: set[str] = set()
    parsed_examples = 0
    documents = sorted(
        [
            root / "README.md",
            root / "schemas" / "README.md",
            root / "model" / "README.md",
            *root.joinpath("docs").rglob("*.md"),
            *root.joinpath("skills").rglob("*.md"),
            *root.joinpath("model").glob("*/README.md"),
            *root.joinpath("examples").rglob("README.md"),
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
            documented_model.add(command)
            if command not in model_commands:
                failures.append(f"{path.relative_to(root)}: unavailable command {command}")
        for command in re.findall(
            r"(?m)^\s*(?:uv run (?:(?:--no-sync|--locked) )*)?"
            r"foliqant ([a-z][a-z-]*)",
            text,
        ):
            documented_package.add(command)
            if command not in package_commands:
                failures.append(f"{path.relative_to(root)}: unavailable command {command}")
        for block in re.findall(r"```(?:sh|bash|shell)\n(.*?)```", text, flags=re.DOTALL):
            joined = block.replace("\\\n", " ")
            for line in joined.splitlines():
                model_line = re.fullmatch(
                    r"\s*(?:uv run (?:(?:--project model|--no-sync|--locked) )*)?"
                    r"foliqant-model ([^\n]+)",
                    line,
                )
                package_line = re.fullmatch(
                    r"\s*(?:uv run (?:(?:--no-sync|--locked) )*)?foliqant ([^\n]+)", line
                )
                for parser, match in (
                    (_parser(), model_line),
                    (runtime_parser(), package_line),
                ):
                    if match is None:
                        continue
                    arguments = shlex.split(match.group(1))
                    if arguments in (["--help"], ["-h"]):
                        parsed_examples += 1
                        continue
                    try:
                        parser.parse_args(arguments)
                        parsed_examples += 1
                    except (ModelError, SystemExit, ValueError):
                        failures.append(
                            f"{path.relative_to(root)}: example arguments do not match CLI"
                        )
        if re.search(r"/Users/[^/]+/", text):
            failures.append(f"{path.relative_to(root)}: author-specific filesystem path")
    for missing in sorted(model_commands - documented_model):
        failures.append(f"Missing lifecycle command documentation: {missing}")
    for missing in sorted(package_commands - documented_package):
        failures.append(f"Missing runtime command documentation: {missing}")
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

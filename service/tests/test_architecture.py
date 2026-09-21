"""Keep engine mechanics free of concrete I/O, validation and training packages."""

import ast
import sys
from pathlib import Path


def test_core_imports_only_stdlib_and_own_core_or_ports() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "foliqant" / "core"
    for file in root.rglob("*.py"):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.level == 1, f"unexpected relative boundary import in {file.name}"
                    continue
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] in sys.stdlib_module_names or module.startswith(
                    ("foliqant.core.", "foliqant.ports.")
                ), f"forbidden core dependency {module} in {file.name}"

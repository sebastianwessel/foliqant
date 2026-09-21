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


def test_package_does_not_own_application_servers_authentication_or_persistence() -> None:
    """Host applications own intake, identity verification, queues and databases."""
    root = Path(__file__).resolve().parents[1] / "src" / "foliqant"
    forbidden = (
        "foliqant.adapters.auth",
        "foliqant.adapters.storage",
        "foliqant.adapters.transports",
        "foliqant.contracts.auth",
        "foliqant.contracts.http",
        "foliqant.core.storage",
        "foliqant.ports.auth",
        "foliqant.ports.storage",
        "foliqant.workers",
        "foliqant.durable",
        "foliqant.durable_bootstrap",
        "psycopg",
        "psycopg_pool",
        "redis",
        "sqlite3",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "fastapi",
        "jwt",
    )

    def allowed(module: str) -> bool:
        return not any(module == name or module.startswith(name + ".") for name in forbidden)

    for file in root.rglob("*.py"):
        module = "foliqant." + ".".join(file.relative_to(root).with_suffix("").parts)
        assert allowed(module), f"host-owned module must not ship: {module}"
        package = module.split(".")[:-1]
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join(
                        package[: len(package) - node.level + 1] + ([base] if base else [])
                    )
                modules = [base, *(f"{base}.{name.name}" for name in node.names)]
            elif isinstance(node, ast.Import):
                modules = [name.name for name in node.names]
            else:
                continue
            for imported in modules:
                assert allowed(imported), f"host-owned dependency {imported} in {file.name}"

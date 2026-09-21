"""Shared published contracts must remain visible to downstream type checkers."""

from importlib.resources import files


def test_shared_contract_distribution_is_typed() -> None:
    assert files("foliqant_decisions").joinpath("py.typed").is_file()


def test_service_distribution_is_typed() -> None:
    assert files("foliqant").joinpath("py.typed").is_file()


def test_minimal_install_has_no_server_storage_or_dev_dependencies(tmp_path):
    """Check the production install independently of the development environment."""
    import json
    import os
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the offline installation check")
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(tmp_path / "environment"))
    subprocess.run(
        [
            uv,
            "sync",
            "--project",
            str(root / "service"),
            "--locked",
            "--offline",
            "--no-dev",
        ],
        env=environment,
        check=True,
        capture_output=True,
        timeout=60,
    )
    binary = tmp_path / "environment/bin"
    probe = subprocess.run(
        [
            str(binary / "python"),
            "-c",
            "from foliqant.bootstrap import open_application; "
            "import importlib.metadata as m, json; "
            "print(json.dumps(sorted(d.metadata['Name'] for d in m.distributions())))",
        ],
        env=environment,
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert not {
        "pytest",
        "mypy",
        "ruff",
        "mlx",
        "torch",
        "starlette",
        "uvicorn",
        "PyJWT",
        "psycopg",
        "psycopg-pool",
        "redis",
    } & set(json.loads(probe.stdout))
    bundle = tmp_path / "bundle"
    for arguments in (
        ["init", str(bundle)],
        ["validate", "--config", str(bundle / "foliqant.yaml")],
        [
            "run",
            "--config",
            str(bundle / "foliqant.yaml"),
            "--workflow",
            "demo",
            "--input",
            str(bundle / "envelope.json"),
        ],
    ):
        result = subprocess.run(
            [str(binary / "foliqant"), *arguments],
            env=environment,
            check=True,
            capture_output=True,
            timeout=30,
        )
        assert isinstance(json.loads(result.stdout), dict)

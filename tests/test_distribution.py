"""The published distribution contains the typed runtime and decision contracts."""

from importlib.resources import files


def test_distribution_is_typed() -> None:
    assert files("foliqant").joinpath("py.typed").is_file()
    assert files("foliqant.decisions").joinpath("py.typed").is_file()


def test_decision_import_does_not_load_runtime_dependencies() -> None:
    import subprocess
    import sys

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import foliqant.decisions; "
            "assert 'foliqant.bootstrap' not in sys.modules; "
            "assert 'pydantic_ai' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


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
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(tmp_path / "environment"))
    subprocess.run(
        [
            uv,
            "sync",
            "--project",
            str(root),
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


def test_wheel_contains_and_runs_the_public_package(tmp_path):
    """Install the wheel in an isolated environment and run its public API."""
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from zipfile import ZipFile

    import pytest

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the offline wheel installation check")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(root),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    wheel = next(tmp_path.glob("foliqant-*.whl"))
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
    assert "foliqant/py.typed" in members
    assert "foliqant/decisions/py.typed" in members
    assert "foliqant/core/runner.py" in members
    assert "foliqant/evaluation/runner.py" in members
    assert not any(name.startswith(("foliqant_model/", "foliqant_decisions/")) for name in members)

    environment_path = tmp_path / "wheel-environment"
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment_path)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    binary = environment_path / "bin"
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(binary / "python"),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    environment = dict(os.environ, PYTHONNOUSERSITE="1")
    environment.pop("PYTHONPATH", None)
    probe = subprocess.run(
        [
            str(binary / "python"),
            "-I",
            "-c",
            "import foliqant.decisions, foliqant.evaluation; "
            "from foliqant import Envelope, load_environment, open_application; "
            "from pathlib import Path; "
            "assert load_environment(Path('foliqant.yaml'), {'EXAMPLE': 'value'}) "
            "== {'EXAMPLE': 'value'}",
        ],
        cwd=outside,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr

    bundle = outside / "bundle"
    for arguments in (
        ["init", str(bundle)],
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
            cwd=outside,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert isinstance(json.loads(result.stdout), dict)

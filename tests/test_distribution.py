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
        ["validate", "--config", str(bundle / "config/settings.yaml")],
        ["explain", "--config", str(bundle / "config/settings.yaml")],
    ):
        result = subprocess.run(
            [str(binary / "foliqant"), *arguments],
            env=environment,
            check=True,
            capture_output=True,
            timeout=30,
        )
        assert isinstance(json.loads(result.stdout), dict)
    _assert_installed_core_run(binary, bundle, environment)


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
    from foliqant.contracts.schemas import decision_schemas, runtime_schemas

    expected_resources = set(runtime_schemas()) | set(decision_schemas())
    assert {
        member.removeprefix("foliqant/schemas/")
        for member in members
        if member.startswith("foliqant/schemas/")
    } == expected_resources

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
            "import foliqant.contracts.execution as execution; "
            "from foliqant import ("
            "Envelope, ExecutionInfo, ExecutionResult, ModelUsage, Usage, "
            "load_environment, open_application); "
            "assert ExecutionInfo is execution.ExecutionInfo; "
            "assert ExecutionResult is execution.ExecutionResult; "
            "assert ModelUsage is execution.ModelUsage; "
            "assert Usage is execution.Usage; "
            "from pathlib import Path; "
            "assert load_environment(Path('settings.yaml'), {'EXAMPLE': 'value'}) "
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
        ["explain", "--config", str(bundle / "config/settings.yaml")],
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
    _assert_installed_core_run(binary, bundle, environment)


def _assert_installed_core_run(binary, bundle, environment):
    """Run installed core with host code, requiring no optional model SDK or network."""
    import subprocess

    config = bundle / "handler.yaml"
    config.write_text(
        "workflows: {demo: handler}\n"
        "handlers:\n  echo:\n    input_schema: {type: object}\n"
        "    output_schema: {type: string}\n    effect: read\n"
    )
    flow = bundle / "handler"
    flow.mkdir()
    (flow / "workflow.yaml").write_text(
        "name: demo\nstart: main\n"
        "output: {pointer: /flows/main/result}\nflows:\n  main:\n"
        "    input: {value: {pointer: /payload/value}}\n"
        "    transition: {outcome: completed}\n    definition:\n"
        "      output: {pointer: /steps/echo/result}\n      steps:\n"
        "        - id: echo\n          definition:\n"
        "            type: handler\n            handler: echo\n"
        "            input: {value: {pointer: /payload/value}}\n"
    )
    code = """
import asyncio, sys
from pathlib import Path
from foliqant import Envelope, open_application, prepare_application
from foliqant.adapters.handlers import HandlerRegistration
from foliqant.core.execution import StepOutcome
from importlib.resources import files
import json
schema = json.loads(files("foliqant").joinpath("schemas", "deployment.schema.json").read_text())
assert schema["additionalProperties"] is False
assert "workflows" in schema["properties"]

async def echo(inputs, context):
    assert context.flow_id == 'main'
    return StepOutcome(inputs['value'])

async def main():
    prepared = prepare_application(Path(sys.argv[1]), handlers={
        'echo': HandlerRegistration(echo)
    })
    async with open_application(prepared, environment={}) as app:
        result = await app.run('demo', Envelope(payload={'value': 'installed'}))
        assert result.execution.status == 'completed'
        assert result.payload == 'installed'
        assert result.flows['main'].steps['echo'].result == 'installed'
        assert result.execution.usage.model_requests == 0
asyncio.run(main())
"""
    result = subprocess.run(
        [str(binary / "python"), "-I", "-c", code, str(config)],
        cwd=bundle,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _cli(
    *arguments: str, stdin: str | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Exercise the real module entry point without changing process environment."""
    return subprocess.run(
        [sys.executable, "-m", "foliqant", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        cwd=cwd,
    )


def test_help_lists_only_implemented_commands() -> None:
    process = _cli("--help")
    assert process.returncode == 0
    assert process.stderr == ""
    for command in ("init", "validate", "explain", "doctor", "run"):
        assert command in process.stdout
    for unavailable in (" serve ", " migrate ", " worker ", " test "):
        assert unavailable not in process.stdout


def test_invalid_arguments_are_redacted_json() -> None:
    secret = "private-secret-command"
    process = _cli(secret)
    assert process.returncode == 2
    assert process.stdout == ""
    assert secret not in process.stderr
    assert json.loads(process.stderr) == {
        "error": {
            "code": "invalid_arguments",
            "message": "Invalid arguments; use --help for supported options.",
            "retryable": False,
        }
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ("validate",),
        ("doctor",),
        ("explain",),
        ("run", "--workflow", "demo", "--input", "envelope.json"),
    ],
)
def test_config_defaults_to_current_directory_without_parent_discovery(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    process = _cli(*arguments, cwd=destination)
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    nested = destination / "nested"
    nested.mkdir()
    missing = _cli(*arguments, cwd=nested)
    assert missing.returncode == 2
    assert json.loads(missing.stderr)["error"]["code"] == "invalid_configuration"


def test_init_is_atomic_non_overwriting_and_scaffolds_model_free_workflow(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    created = _cli("init", str(destination))
    assert created.returncode == 0
    assert created.stderr == ""
    assert json.loads(created.stdout) == {"command": "init", "status": "created"}
    assert (destination / "foliqant.yaml").read_text(encoding="utf-8") == (
        "version: 1\nworkflows:\n  demo: workflows/demo\n"
    )
    assert (destination / "workflows/demo/workflow.yaml").read_text(encoding="utf-8") == (
        "version: 1\nname: demo\nstart: done\nsteps:\n"
        "  done:\n    type: finish\n    outcome: completed\n"
    )
    assert not (destination / "workflows/demo/steps").exists()

    sentinel = destination / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    rejected = _cli("init", str(destination))
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr)["error"]["code"] == "conflict"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_scaffold_validate_explain_doctor_and_run_offline(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "foliqant.yaml"

    validated = _cli("validate", "--config", str(config))
    assert validated.returncode == 0
    assert validated.stderr == ""
    validation = json.loads(validated.stdout)
    assert validation["command"] == "validate"
    assert validation["status"] == "valid"
    assert validation["workflows"] == ["demo"]
    assert len(validation["configuration_digest"]) == 64

    explained = _cli("explain", "--config", str(config), "--workflow", "demo")
    assert explained.returncode == 0
    report = json.loads(explained.stdout)
    assert report["workflows"] == [
        {
            "name": "demo",
            "revision": report["workflows"][0]["revision"],
            "start": "done",
            "steps": [{"name": "done", "type": "finish"}],
        }
    ]

    diagnosed = _cli("doctor", "--config", str(config))
    assert diagnosed.returncode == 0
    diagnosis = json.loads(diagnosed.stdout)
    assert diagnosis["command"] == "doctor"
    assert diagnosis["status"] == "ok"
    assert set(diagnosis["optional_dependencies"]) == {
        "anthropic",
        "mcp",
        "openai",
        "telemetry",
    }

    executed = _cli(
        "run",
        "--config",
        str(config),
        "--workflow",
        "demo",
        "--input",
        str(destination / "envelope.json"),
        "--tenant-id",
        "tenant-a",
        "--principal-id",
        "principal-a",
    )
    assert executed.returncode == 0
    result = json.loads(executed.stdout)
    assert result["execution"]["workflow"] == "demo"
    assert result["execution"]["status"] == "completed"
    assert result["metadata"] == {"tenant_id": "tenant-a", "principal_id": "principal-a"}
    done = result["decisions"]["done"]
    assert done["status"] == "completed"
    assert done["result"] is None
    assert done["elapsed_seconds"] >= 0
    assert done["usage"] == {
        "model_requests": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def test_stdin_is_bounded_and_body_is_not_echoed_on_rejection(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    secret = "customer-secret-body"
    process = _cli(
        "run",
        "--config",
        str(destination / "foliqant.yaml"),
        "--workflow",
        "demo",
        "--input",
        "-",
        "--tenant-id",
        "trusted-tenant",
        stdin=json.dumps({"payload": secret, "metadata": {"tenant_id": "claimed"}}),
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert secret not in process.stderr
    assert "claimed" not in process.stderr
    assert json.loads(process.stderr)["error"]["code"] == "forbidden"


def test_run_without_identity_flags_preserves_envelope_identity_context(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    envelope = destination / "tenant-envelope.json"
    envelope.write_text(
        json.dumps({"payload": {}, "metadata": {"tenant_id": "tenant-context"}}),
        encoding="utf-8",
    )
    process = _cli(
        "run",
        "--config",
        str(destination / "foliqant.yaml"),
        "--workflow",
        "demo",
        "--input",
        str(envelope),
    )
    assert process.returncode == 0 and process.stderr == ""
    assert json.loads(process.stdout)["metadata"] == {"tenant_id": "tenant-context"}


def test_explain_includes_branch_edges_and_alias_without_prompt_content(tmp_path):
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "foliqant.yaml"
    config.write_text(
        "version: 1\nworkflows: {demo: workflows/demo}\nmodels:\n"
        "  local:\n    provider: openai_compatible\n    model: synthetic\n"
        "    base_url: https://model.example/v1\n    output_mode: native\n"
    )
    bundle = destination / "workflows/demo"
    (bundle / "workflow.yaml").write_text("version: 1\nname: demo\nstart: choose\n")
    (bundle / "steps").mkdir()
    (bundle / "steps/done.yaml").write_text("type: finish\noutcome: completed\n")
    (bundle / "steps/choose.yaml").write_text(
        "type: decision\nmodel: local\ninstructions: private-prompt\n"
        "sources: {document: {literal: private-content}}\n"
        "question: {type: predicate, criteria: [private-question]}\n"
        "on_answer: {'true': done, 'false': done}\n"
    )
    process = _cli("explain", "--config", str(config))
    assert process.returncode == 0, process.stderr
    assert "private-" not in process.stdout
    step = next(
        item
        for item in json.loads(process.stdout)["workflows"][0]["steps"]
        if item["name"] == "choose"
    )
    assert step["model"] == "local"
    assert step["on_answer"] == {"true": "done", "false": "done"}


def test_failed_execution_is_a_safe_cli_error_not_success_output(tmp_path, monkeypatch, capsys):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from foliqant import bootstrap, cli
    from foliqant.core.errors import ErrorCode, ServiceError

    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0

    class Application:
        async def run(self, *args, **kwargs):
            return SimpleNamespace(
                execution=SimpleNamespace(
                    status="failed", error=ServiceError(ErrorCode.TIMEOUT, retryable=True)
                )
            )

    @asynccontextmanager
    async def application(*args, **kwargs):
        yield Application()

    monkeypatch.setattr(bootstrap, "open_application", application)
    code = cli.main(
        [
            "run",
            "--config",
            str(destination / "foliqant.yaml"),
            "--workflow",
            "demo",
            "--input",
            str(destination / "envelope.json"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 4 and captured.out == ""
    error = json.loads(captured.err.splitlines()[-1])["error"]
    assert error["code"] == "timeout" and error["retryable"] is True


def test_nonregular_input_is_rejected_without_waiting_for_writer(tmp_path):
    import os

    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    fifo = tmp_path / "input.pipe"
    os.mkfifo(fifo)
    process = _cli(
        "run",
        "--config",
        str(destination / "foliqant.yaml"),
        "--workflow",
        "demo",
        "--input",
        str(fifo),
    )
    assert process.returncode == 2 and process.stdout == ""
    assert json.loads(process.stderr)["error"]["code"] == "invalid_input"

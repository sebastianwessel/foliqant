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


def test_init_is_atomic_non_overwriting_and_scaffolds_explicit_local_model_workflow(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "project"
    created = _cli("init", str(destination))
    assert created.returncode == 0
    assert created.stderr == ""
    assert json.loads(created.stdout) == {"command": "init", "status": "created"}
    from foliqant.bootstrap import prepare_application

    prepared = prepare_application(destination / "config/settings.yaml")
    flow = prepared.plans["demo"].flow("summarize")
    assert flow.steps[0].type == "llm"
    assert flow.transition.outcome == "completed"
    assert (destination / "config/demo/summarize/summarize.step.md").is_file()
    assert (destination / "config/.env.example").is_file()
    assert not (destination / "workflows").exists()

    sentinel = destination / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    rejected = _cli("init", str(destination))
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr)["error"]["code"] == "conflict"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_scaffold_validate_explain_doctor_offline(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "config/settings.yaml"

    validated = _cli("validate", "--config", str(config))
    assert validated.returncode == 0
    assert validated.stderr == ""
    validation = json.loads(validated.stdout)
    assert validation["command"] == "validate"
    assert validation["status"] == "valid"
    assert validation["workflows"] == ["demo"]
    assert len(validation["configuration_digest"]) == 64
    assert validation["diagnostics"] == []

    explained = _cli("explain", "--config", str(config), "--workflow", "demo")
    assert explained.returncode == 0
    report = json.loads(explained.stdout)
    plan = report["workflows"][0]
    assert plan["name"] == "demo" and plan["start"] == {"flow": "summarize"}
    assert len(plan["revision"]) == 64
    assert plan["flows"][0]["transition"] == {"outcome": "completed"}
    step = plan["flows"][0]["steps"][0]
    assert step["id"] == "summarize" and step["type"] == "llm"
    assert step["model"] == "local"

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


def test_scaffold_run_uses_real_composition_with_offline_model(tmp_path, offline_cli):
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    code, output, error = offline_cli(
        "run",
        "--config",
        str(destination / "config/settings.yaml"),
        "--workflow",
        "demo",
        "--input",
        str(destination / "envelope.json"),
        "--tenant-id",
        "tenant-a",
        "--principal-id",
        "principal-a",
    )
    assert code == 0 and error == ""
    result = json.loads(output)
    assert result["execution"]["workflow"] == "demo"
    assert result["execution"]["status"] == "completed"
    assert result["metadata"] == {"tenant_id": "tenant-a", "principal_id": "principal-a"}
    summary = result["flows"]["summarize"]["steps"]["summarize"]
    assert summary["status"] == "completed"
    assert summary["result"] == "Account statement requested."
    assert summary["elapsed_seconds"] >= 0
    assert summary["usage"]["model_requests"] == 1
    assert result["payload"] == "Account statement requested."


def test_stdin_is_bounded_and_body_is_not_echoed_on_rejection(tmp_path: Path, offline_cli) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    secret = "customer-secret-body"
    code, output, error = offline_cli(
        "run",
        "--config",
        str(destination / "config/settings.yaml"),
        "--workflow",
        "demo",
        "--input",
        "-",
        "--tenant-id",
        "trusted-tenant",
        stdin=json.dumps({"payload": {"message": secret}, "metadata": {"tenant_id": "claimed"}}),
    )
    assert code == 2 and output == ""
    assert secret not in error and "claimed" not in error
    assert json.loads(error)["error"]["code"] == "forbidden"


def test_run_without_identity_flags_preserves_envelope_identity_context(
    tmp_path: Path, offline_cli
) -> None:
    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    envelope = destination / "tenant-envelope.json"
    envelope.write_text(
        json.dumps(
            {
                "payload": {"message": "Statement please"},
                "metadata": {"tenant_id": "tenant-context"},
            }
        ),
        encoding="utf-8",
    )
    code, output, error = offline_cli(
        "run",
        "--config",
        str(destination / "config/settings.yaml"),
        "--workflow",
        "demo",
        "--input",
        str(envelope),
    )
    assert code == 0 and error == ""
    assert json.loads(output)["metadata"] == {"tenant_id": "tenant-context"}


def test_explain_includes_flow_edges_and_alias_without_prompt_content(tmp_path):
    import yaml

    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "config/settings.yaml"
    workflow = destination / "config/demo/workflow.yaml"
    workflow.write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "start": "choose",
                "flows": {
                    "choose": {
                        "input": {},
                        "definition": {
                            "steps": [
                                {
                                    "id": "choose",
                                    "definition": {
                                        "type": "decision",
                                        "model": "local",
                                        "instructions": "private-prompt",
                                        "sources": {"document": {"literal": "private-content"}},
                                        "question": {
                                            "type": "predicate",
                                            "criteria": ["private-question"],
                                        },
                                    },
                                }
                            ]
                        },
                        "transition": {
                            "binding": {"literal": "yes"},
                            "cases": {"yes": {"outcome": "completed"}},
                            "default": {"outcome": "needs_review"},
                        },
                    }
                },
            }
        )
    )
    process = _cli("explain", "--config", str(config))
    assert process.returncode == 0, process.stderr
    assert "private-" not in process.stdout
    flow = json.loads(process.stdout)["workflows"][0]["flows"][0]
    assert flow["steps"][0]["model"] == "local"
    assert flow["transition"] == {
        "binding": {"literal": True},
        "cases": {"yes": {"outcome": "completed"}},
        "default": {"outcome": "needs_review"},
    }


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
            str(destination / "config/settings.yaml"),
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
        str(destination / "config/settings.yaml"),
        "--workflow",
        "demo",
        "--input",
        str(fifo),
    )
    assert process.returncode == 2 and process.stdout == ""
    assert json.loads(process.stderr)["error"]["code"] == "invalid_input"


@pytest.fixture
def offline_cli(monkeypatch, capsys):
    import io
    from contextlib import asynccontextmanager

    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from foliqant import bootstrap, cli
    from foliqant.adapters.models import ModelBinding
    from foliqant.core.admission import CapacityLimiter

    real_open = bootstrap.open_application

    async def model(messages, info):
        return ModelResponse(parts=[TextPart("Account statement requested.")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        yield {
            "local": ModelBinding(
                FunctionModel(model), {}, CapacityLimiter(concurrency=1, queue_limit=0), "native"
            )
        }

    def open_offline(prepared, **kwargs):
        kwargs["environment"] = {
            "MODEL_ID": "synthetic",
            "MODEL_BASE_URL": "http://127.0.0.1:1/v1",
        }
        kwargs["plugins"] = bootstrap.RuntimePlugins(model_factory=factory)
        return real_open(prepared, **kwargs)

    monkeypatch.setattr(bootstrap, "open_application", open_offline)

    def run(*args, stdin=None):
        if stdin is not None:
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin.encode())))
        code = cli.main(list(args))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run

import json
import subprocess
import sys
from pathlib import Path


def _cli(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Exercise the real module entry point without changing process environment."""
    return subprocess.run(
        [sys.executable, "-m", "foliqant", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_help_lists_only_implemented_commands() -> None:
    process = _cli("--help")
    assert process.returncode == 0
    assert process.stderr == ""
    for command in ("init", "validate", "explain", "doctor", "run", "serve"):
        assert command in process.stdout
    assert " test " not in process.stdout


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


def test_init_is_atomic_non_overwriting_and_scaffolds_model_free_workflow(tmp_path: Path) -> None:
    destination = tmp_path / "project"
    created = _cli("init", str(destination))
    assert created.returncode == 0
    assert created.stderr == ""
    assert json.loads(created.stdout) == {"command": "init", "status": "created"}
    assert (destination / "foliqant.yaml").read_text(encoding="utf-8") == (
        "version: 1\nmode: development\nworkflows:\n  demo: workflows/demo\nmodels: {}\nmcp: {}\n"
    )
    assert (destination / "workflows/demo/workflow.yaml").read_text(encoding="utf-8") == (
        "version: 1\nname: demo\nstart: done\n"
    )
    assert (destination / "workflows/demo/steps/done.yaml").read_text(encoding="utf-8") == (
        "type: finish\noutcome: completed\n"
    )

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
        "azure",
        "bedrock",
        "http",
        "mcp",
        "openai",
        "postgres",
        "redis",
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
    assert result["decisions"] == {"done": {"status": "completed", "result": None}}


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
        stdin=json.dumps({"payload": secret, "metadata": {"tenant_id": "claimed"}}),
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert secret not in process.stderr
    assert "claimed" not in process.stderr
    assert json.loads(process.stderr)["error"]["code"] == "forbidden"


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


def test_serve_authenticates_on_real_loopback_and_stops_cleanly(tmp_path):
    import http.client
    import os
    import socket
    import time

    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = destination / "foliqant.yaml"
    config.write_text(
        "version: 1\nworkflows: {demo: workflows/demo}\nhttp:\n"
        f"  host: 127.0.0.1\n  port: {port}\n"
        "  auth:\n    type: bearer\n    bindings:\n      operator:\n"
        "        token_env: CLI_TEST_TOKEN\n        workflows: [demo]\n"
    )
    token = "synthetic-only-loopback-token"
    environment = dict(os.environ, CLI_TEST_TOKEN=token)
    server = subprocess.Popen(
        [sys.executable, "-m", "foliqant", "serve", "--config", str(config)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def request(method, route, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request(
                method,
                route,
                body='{"payload": {}}' if method == "POST" else None,
                headers=headers or {},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                assert request("GET", "/health") == (200, {"status": "ok"})
                break
            except OSError:
                assert server.poll() is None, "server exited during startup"
                if time.monotonic() > deadline:
                    raise AssertionError("server startup timeout") from None
                time.sleep(0.02)
        route = "/workflows/demo/runs"
        assert request("POST", route, {"Content-Type": "application/json"})[0] == 401
        status, result = request(
            "POST", route, {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        )
        assert status == 200 and result["execution"]["status"] == "completed"
        server.terminate()
        stdout, stderr = server.communicate(timeout=10)
        assert token.encode() not in stdout + stderr
        assert server.returncode in {0, -15}
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate(timeout=5)


def test_serve_bind_failure_returns_fixed_cli_error(tmp_path):
    import socket

    destination = tmp_path / "project"
    assert _cli("init", str(destination)).returncode == 0
    config = destination / "foliqant.yaml"
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        config.write_text(
            "version: 1\nmode: development\nworkflows: {demo: workflows/demo}\n"
            f"http:\n  port: {port}\n  auth: {{type: development}}\n"
        )
        process = _cli("serve", "--config", str(config))
    assert process.returncode == 4
    assert process.stdout == ""
    error = json.loads(process.stderr.splitlines()[-1])
    assert error["error"]["code"] == "dependency_failure"
    assert str(port) not in process.stderr
    assert "address already in use" not in process.stderr.lower()


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

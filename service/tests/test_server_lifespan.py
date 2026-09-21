"""Real Uvicorn lifecycle checks keep owned resources inside ASGI shutdown."""

from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_CHILD = r"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.trace import NoOpTracerProvider

import foliqant.adapters.auth as auth_package
import foliqant.adapters.telemetry.logging as logging_module
import foliqant.adapters.telemetry.runtime as telemetry_module
from foliqant.adapters.auth import JwtAuthenticator as RealJwtAuthenticator
from foliqant.bootstrap import RuntimePlugins, prepare_application, serve_application
from foliqant.core.errors import ServiceError

config_path = Path(sys.argv[1])
events_path = Path(sys.argv[2])
mode = sys.argv[3]


def record(event: str) -> None:
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class RecordingJwtAuthenticator(RealJwtAuthenticator):
    async def aclose(self) -> None:
        await super().aclose()
        record("jwt_close")


class RecordingTelemetry:
    startup_failures = ()

    def __init__(self) -> None:
        self.tracer_provider = NoOpTracerProvider()
        self.meter_provider = NoOpMeterProvider()

    @classmethod
    def build(cls, config, *, labels, environment):
        del config, labels, environment
        record("telemetry_open")
        return cls()

    def install_global(self) -> None:
        record("telemetry_global")

    async def aclose(self) -> bool:
        record("telemetry_close")
        return True


class RecordingLogging:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> bool:
        if not self.closed:
            self.closed = True
            record("logging_close")
        return True


def configure_logging(*, debug, labels):
    del debug, labels
    return RecordingLogging()


@asynccontextmanager
async def model_factory(profiles, *, environment):
    del environment
    record("model_open")
    try:
        # The finish-only workflow never reads these synthetic bindings.
        yield {name: object() for name in profiles.models}
    finally:
        record("model_close")


auth_package.JwtAuthenticator = RecordingJwtAuthenticator
logging_module.configure_logging = configure_logging
telemetry_module.TelemetryRuntime = RecordingTelemetry
prepared = prepare_application(config_path)

try:
    asyncio.run(
        serve_application(
            prepared,
            environment={},
            plugins=RuntimePlugins(model_factory=model_factory),
        )
    )
except ServiceError as error:
    record("safe:" + error.code.value)
    if mode == "signal":
        raise
"""


def _configuration(tmp_path: Path, port: int) -> Path:
    workflow = tmp_path / "workflows" / "demo"
    (workflow / "steps").mkdir(parents=True)
    (workflow / "workflow.yaml").write_text(
        "version: 1\nname: demo\nstart: done\n", encoding="utf-8"
    )
    (workflow / "steps" / "done.yaml").write_text(
        "type: finish\noutcome: completed\n", encoding="utf-8"
    )
    config = tmp_path / "foliqant.yaml"
    config.write_text(
        "version: 1\n"
        "workflows: {demo: workflows/demo}\n"
        "models:\n"
        "  local:\n"
        "    provider: openai_compatible\n"
        "    model: synthetic\n"
        "    base_url: https://model.example/v1\n"
        "    output_mode: native\n"
        "telemetry:\n"
        "  service_name: lifecycle_test\n"
        "http:\n"
        "  host: 127.0.0.1\n"
        f"  port: {port}\n"
        "  auth:\n"
        "    type: jwt\n"
        "    issuer: https://issuer.example/\n"
        "    audience: lifecycle-test\n"
        "    algorithms: [RS256]\n"
        "    jwks_url: https://issuer.example/jwks\n"
        "    workflows: [demo]\n",
        encoding="utf-8",
    )
    return config


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_child(config: Path, events: Path, mode: str) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    service_source = str(Path(__file__).resolve().parents[1] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        service_source if not existing else os.pathsep.join((service_source, existing))
    )
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(config), str(events), mode],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _events(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _wait_until_ready(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.communicate(timeout=1)[1]
            pytest.fail(f"server exited before readiness: {stderr}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
            connection.request("GET", "/ready")
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status == 200:
                return
        except OSError:
            pass
        time.sleep(0.02)
    pytest.fail("server did not become ready")


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX SIGTERM replay")
def test_sigterm_runs_asgi_shutdown_before_uvicorn_replays_signal(tmp_path: Path) -> None:
    port = _available_port()
    events_path = tmp_path / "events.log"
    process = _start_child(_configuration(tmp_path, port), events_path, "signal")
    try:
        _wait_until_ready(process, port)
        assert _events(events_path) == ["telemetry_open", "telemetry_global", "model_open"]
        process.send_signal(signal.SIGTERM)
        return_code = process.wait(timeout=10)
        assert return_code == -signal.SIGTERM
        events = _events(events_path)
        assert events == [
            "telemetry_open",
            "telemetry_global",
            "model_open",
            "model_close",
            "telemetry_close",
            "jwt_close",
            "logging_close",
        ]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX loopback listener")
def test_bind_failure_is_safe_and_closes_started_lifespan_resources(tmp_path: Path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = int(occupied.getsockname()[1])
        events_path = tmp_path / "events.log"
        process = _start_child(_configuration(tmp_path, port), events_path, "bind-failure")
        try:
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert _events(events_path) == [
        "telemetry_open",
        "telemetry_global",
        "model_open",
        "model_close",
        "telemetry_close",
        "jwt_close",
        "logging_close",
        "safe:dependency_failure",
    ]


def test_cli_missing_bearer_token_is_a_failure_not_stopped_success(tmp_path: Path) -> None:
    port = _available_port()
    config = _configuration(tmp_path, port)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text[: text.index("  auth:\n")]
        + "  auth:\n"
        + "    type: bearer\n"
        + "    bindings:\n"
        + "      operator:\n"
        + "        token_env: LIFECYCLE_TEST_TOKEN\n"
        + "        workflows: [demo]\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("LIFECYCLE_TEST_TOKEN", None)
    service_source = str(Path(__file__).resolve().parents[1] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        service_source if not existing else os.pathsep.join((service_source, existing))
    )
    process = subprocess.run(
        [sys.executable, "-m", "foliqant", "serve", "--config", str(config)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert process.returncode != 0
    assert process.stdout == ""
    assert '"status":"stopped"' not in process.stdout
    assert "LIFECYCLE_TEST_TOKEN" not in process.stderr

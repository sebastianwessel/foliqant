"""Progress and graceful curation pause controls."""

from __future__ import annotations

import io
import os
import signal
import subprocess
from pathlib import Path

import pytest

from foliqant_model.curation.runtime import CurationControl
from foliqant_model.errors import ModelError


def test_first_interrupt_pauses_at_checkpoint_and_restores_handler(tmp_path: Path) -> None:
    stream = io.StringIO()
    previous = signal.getsignal(signal.SIGINT)
    control = CurationControl(progress="always", stream=stream, handle_signals=True)

    with pytest.raises(ModelError) as stopped:
        with control.active():
            control.report("generating", tmp_path, completed=0, total=2)
            os.kill(os.getpid(), signal.SIGINT)
            control.checkpoint()

    assert stopped.value.code == "INTERRUPTED"
    assert signal.getsignal(signal.SIGINT) == previous
    assert "phase=generating" in stream.getvalue()
    assert "Resume by running the same command" in stream.getvalue()


def test_second_interrupt_is_immediate_and_not_reported_as_safe_pause() -> None:
    stream = io.StringIO()
    previous = signal.getsignal(signal.SIGINT)
    control = CurationControl(progress="never", stream=stream, handle_signals=True)

    with pytest.raises(KeyboardInterrupt):
        with control.active():
            os.kill(os.getpid(), signal.SIGINT)
            os.kill(os.getpid(), signal.SIGINT)

    assert signal.getsignal(signal.SIGINT) == previous
    assert "paused safely" not in stream.getvalue()
    assert "current candidate may repeat" in stream.getvalue()


def test_failure_after_pause_request_does_not_claim_safe_boundary() -> None:
    stream = io.StringIO()
    control = CurationControl(progress="never", stream=stream, handle_signals=True)

    with pytest.raises(ModelError, match="test network failure"):
        with control.active():
            os.kill(os.getpid(), signal.SIGINT)
            raise ModelError("NETWORK_FAILED", "test network failure")

    assert "paused safely" not in stream.getvalue()
    assert "stopped before a safe pause boundary" in stream.getvalue()
    assert "current candidate may repeat" in stream.getvalue()


def test_auto_progress_reuses_one_tty_line(tmp_path: Path) -> None:
    class TtyStream(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = TtyStream()
    control = CurationControl(progress="auto", stream=stream)
    control.report("preparing", tmp_path)
    control.report("generating", tmp_path, completed=1, total=2)

    assert stream.getvalue().count("\r") == 2
    assert stream.getvalue().count("\n") == 1
    assert stream.getvalue().count(f"run={tmp_path}") == 1


def test_request_timeout_stops_without_retry_and_explains_resume() -> None:
    control = CurationControl(progress="never", stream=io.StringIO())
    attempted = 0
    with pytest.raises(ModelError) as stopped:
        for _ in range(2):
            with control.local_request():
                attempted += 1
                raise ModelError("TIMEOUT", "worker deadline")

    assert attempted == 1
    assert stopped.value.code == "TIMEOUT"
    assert stopped.value.exit_code == 4
    assert "completed results are saved" in stopped.value.message
    assert "unchanged settings" in stopped.value.message
    assert "not the whole run" in stopped.value.message


def test_other_request_failures_keep_their_original_error() -> None:
    control = CurationControl(progress="never", stream=io.StringIO())
    failure = ModelError("NETWORK_FAILED", "test network failure")
    with pytest.raises(ModelError) as stopped:
        with control.local_request():
            raise failure
    assert stopped.value is failure


def test_curate_wrapper_execs_runtime_without_signal_forwarding_parent(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    binaries = tmp_path / "bin"
    binaries.mkdir()
    runtime = binaries / "runtime-python"
    runtime.write_text(
        "#!/usr/bin/env bash\n"
        "count=0\n"
        "trap 'count=$((count + 1)); echo INT:$count >&2; "
        "if (( count > 1 )); then exit 99; fi' INT\n"
        "echo READY >&2\n"
        "sleep 1 || true\n"
        "echo DONE >&2\n",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == sync ]]; then exit 0; fi\n"
        "printf '%s\\n' \"$TEST_RUNTIME\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    environment = {
        "HOME": str(tmp_path),
        "PATH": f"{binaries}:/usr/bin:/bin",
        "TEST_RUNTIME": str(runtime),
    }
    process = subprocess.Popen(
        ["/bin/bash", str(root / "scripts/curate-data"), "--progress", "never"],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stderr is not None
    assert process.stderr.readline().strip() == "READY"
    os.killpg(process.pid, signal.SIGINT)
    _stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 0
    assert stderr.count("INT:1") == 1
    assert "INT:2" not in stderr


def test_curate_wrapper_ignores_openrouter_key_without_exporting_it(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    isolated = tmp_path / "repo"
    scripts = isolated / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "curate-data"
    wrapper.write_bytes((root / "scripts/curate-data").read_bytes())
    wrapper.chmod(0o755)
    isolated.joinpath(".env").write_text(
        "OPENROUTER_API_KEY=must-not-reach-runtime\n", encoding="utf-8"
    )

    binaries = tmp_path / "bin"
    binaries.mkdir()
    runtime = binaries / "runtime-python"
    runtime.write_text(
        "#!/usr/bin/env bash\n[[ -z ${OPENROUTER_API_KEY+x} ]] || exit 41\nexit 0\n",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == sync ]]; then exit 0; fi\n"
        "printf '%s\\n' \"$TEST_RUNTIME\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--progress", "never"],
        cwd=isolated,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{binaries}:/usr/bin:/bin",
            "TEST_RUNTIME": str(runtime),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "unsupported configuration key" not in result.stderr


def test_curate_wrapper_allows_and_exports_reasoning_effort(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    isolated = tmp_path / "repo"
    scripts = isolated / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "curate-data"
    wrapper.write_bytes((root / "scripts/curate-data").read_bytes())
    wrapper.chmod(0o755)
    isolated.joinpath(".env").write_text(
        "FOLIQANT_CURATION_REASONING_EFFORT=xhigh\n", encoding="utf-8"
    )

    binaries = tmp_path / "bin"
    binaries.mkdir()
    runtime = binaries / "runtime-python"
    runtime.write_text(
        "#!/usr/bin/env bash\n"
        "[[ ${FOLIQANT_CURATION_REASONING_EFFORT:-} == xhigh ]] || exit 41\n"
        "exit 0\n",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == sync ]]; then exit 0; fi\n"
        "printf '%s\\n' \"$TEST_RUNTIME\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--progress", "never"],
        cwd=isolated,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{binaries}:/usr/bin:/bin",
            "TEST_RUNTIME": str(runtime),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "unsupported configuration key" not in result.stderr

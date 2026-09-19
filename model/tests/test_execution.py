"""Process isolation and protocol tests for the parent worker runner."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from foliqant_model import execution
from foliqant_model.contracts import WorkerRequest, WorkerResult
from foliqant_model.errors import ModelError


def _doctor_request(request_id: str = "doctor-parent") -> WorkerRequest:
    return WorkerRequest.model_validate(
        {"schemaVersion": 1, "requestId": request_id, "operation": "doctor"}
    )


def _doctor_result(request_id: str = "doctor-parent") -> WorkerResult:
    return WorkerResult.model_validate(
        {
            "schemaVersion": 1,
            "requestId": request_id,
            "operation": "doctor",
            "ok": True,
            "result": {
                "mlxImportAvailable": False,
                "metalAvailable": False,
                "mlxVersion": None,
                "mlxLmVersion": None,
                "unavailableReason": "not probed",
            },
        }
    )


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _descendant_script(tmp_path: Path) -> tuple[str, Path, Path]:
    ready = tmp_path / "descendant-ready"
    terminated = tmp_path / "descendant-terminated"
    descendant = (
        "import os,signal,time; from pathlib import Path; "
        f"ready=Path({str(ready)!r}); stopped=Path({str(terminated)!r}); "
        "signal.signal(signal.SIGTERM, lambda *_: (stopped.write_text('yes'), os._exit(0))); "
        "ready.write_text('yes'); time.sleep(60)"
    )
    parent = "\n".join(
        (
            "import subprocess,sys,time",
            "from pathlib import Path",
            f"subprocess.Popen([sys.executable,'-c',{descendant!r}])",
            f"ready=Path({str(ready)!r})",
            "deadline=time.monotonic()+5",
            "while not ready.exists() and time.monotonic()<deadline:",
            "    time.sleep(0.01)",
            "time.sleep(60)",
        )
    )
    return parent, ready, terminated


def test_run_child_uses_scrubbed_offline_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    script = (
        "import os; print(os.getenv('HF_TOKEN')); print(os.getenv('OPENAI_API_KEY')); "
        "print(os.environ['HF_HUB_OFFLINE']); print(os.environ['TRANSFORMERS_OFFLINE'])"
    )
    return_code = execution._run_child(
        [sys.executable, "-c", script],
        workspace=tmp_path,
        log_path=tmp_path / "backend.log",
        timeout_seconds=5,
    )
    assert return_code == 0
    assert (tmp_path / "backend.log").read_text(encoding="utf-8").splitlines() == [
        "None",
        "None",
        "1",
        "1",
    ]
    assert (tmp_path / "backend.log").stat().st_mode & 0o777 == 0o600


def test_timeout_terminates_descendant_process_group(tmp_path: Path) -> None:
    script, ready, terminated = _descendant_script(tmp_path)
    with pytest.raises(ModelError) as raised:
        execution._run_child(
            [sys.executable, "-c", script],
            workspace=tmp_path,
            log_path=tmp_path / "backend.log",
            timeout_seconds=1,
        )
    assert raised.value.code == "TIMEOUT"
    assert ready.is_file()
    assert terminated.read_text(encoding="utf-8") == "yes"


def test_sigterm_interrupt_terminates_descendant_process_group(tmp_path: Path) -> None:
    script, ready, terminated = _descendant_script(tmp_path)
    timer = threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with pytest.raises(ModelError) as raised:
            execution._run_child(
                [sys.executable, "-c", script],
                workspace=tmp_path,
                log_path=tmp_path / "backend.log",
                timeout_seconds=10,
            )
    finally:
        timer.cancel()
        timer.join()
    assert raised.value.code == "INTERRUPTED"
    assert ready.is_file()
    assert terminated.read_text(encoding="utf-8") == "yes"


def test_keyboard_interrupt_terminates_descendant_process_group(tmp_path: Path) -> None:
    import _thread

    script, ready, terminated = _descendant_script(tmp_path)
    timer = threading.Timer(0.5, _thread.interrupt_main)
    timer.start()
    try:
        with pytest.raises(ModelError) as raised:
            execution._run_child(
                [sys.executable, "-c", script],
                workspace=tmp_path,
                log_path=tmp_path / "backend.log",
                timeout_seconds=10,
            )
    finally:
        timer.cancel()
        timer.join()
    assert raised.value.code == "INTERRUPTED"
    assert ready.is_file()
    assert terminated.read_text(encoding="utf-8") == "yes"


def test_read_worker_result_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    _write_private(
        result_path,
        b'{"schemaVersion":1,"requestId":"a","requestId":"b","operation":"doctor",'
        b'"ok":false,"error":{"code":"INTERNAL","message":"failed"}}',
    )
    with pytest.raises(ModelError) as duplicate:
        execution._read_worker_result(result_path)
    assert duplicate.value.code == "OUTPUT_INVALID"

    result_path.unlink()
    _write_private(result_path, b'{"value":NaN}')
    with pytest.raises(ModelError) as nonfinite:
        execution._read_worker_result(result_path)
    assert nonfinite.value.code == "OUTPUT_INVALID"


def test_read_worker_result_rejects_missing_oversized_and_nonprivate_files(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    with pytest.raises(ModelError) as missing:
        execution._read_worker_result(result_path)
    assert missing.value.code == "BACKEND_FAILED"

    _write_private(result_path, b"x" * (execution._MAX_EXCHANGE_BYTES + 1))
    with pytest.raises(ModelError) as oversized:
        execution._read_worker_result(result_path)
    assert oversized.value.code == "OUTPUT_INVALID"

    result_path.write_bytes(b"{}")
    result_path.chmod(0o644)
    with pytest.raises(ModelError) as public:
        execution._read_worker_result(result_path)
    assert public.value.code == "OUTPUT_INVALID"


def test_result_must_match_request_id_and_operation() -> None:
    with pytest.raises(ModelError) as wrong_id:
        execution._validate_result_correlation(_doctor_request(), _doctor_result("other"))
    assert wrong_id.value.code == "OUTPUT_INVALID"

    train_failure = WorkerResult.model_validate(
        {
            "schemaVersion": 1,
            "requestId": "doctor-parent",
            "operation": "train",
            "ok": False,
            "error": {"code": "TRAINING_FAILED", "message": "failed"},
        }
    )
    with pytest.raises(ModelError) as wrong_operation:
        execution._validate_result_correlation(_doctor_request(), train_failure)
    assert wrong_operation.value.code == "OUTPUT_INVALID"


def test_request_file_is_exclusive_and_private(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    execution._write_exclusive_private(request_path, b"{}\n")
    assert request_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ModelError) as raised:
        execution._write_exclusive_private(request_path, b"replacement")
    assert raised.value.code == "OUTPUT_EXISTS"
    assert request_path.read_bytes() == b"{}\n"


def test_run_worker_rejects_invalid_timeout_before_creating_files(tmp_path: Path) -> None:
    for invalid in (True, 0, 604_801):
        with pytest.raises(ModelError) as raised:
            execution.run_worker(_doctor_request(), workspace=tmp_path, timeout_seconds=invalid)
        assert raised.value.code == "ARGUMENT_INVALID"
    assert list(tmp_path.iterdir()) == []


def test_fifo_log_is_rejected_without_blocking(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    os.mkfifo(log_path, 0o600)
    started = time.monotonic()
    with pytest.raises(ModelError) as raised:
        execution._run_child(
            [sys.executable, "-c", "pass"],
            workspace=tmp_path,
            log_path=log_path,
            timeout_seconds=5,
        )
    assert raised.value.code == "IO_FAILED"
    assert time.monotonic() - started < 1

    result_path = tmp_path / "result.json"
    os.mkfifo(result_path, 0o600)
    started = time.monotonic()
    with pytest.raises(ModelError) as result_error:
        execution._read_worker_result(result_path)
    assert result_error.value.code == "OUTPUT_INVALID"
    assert time.monotonic() - started < 1


def test_run_worker_rejects_symlink_workspace(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ModelError) as raised:
        execution.run_worker(_doctor_request(), workspace=link, timeout_seconds=5)
    assert raised.value.code == "UNSAFE_ARTIFACT_PATH"


@pytest.mark.integration
def test_run_worker_real_doctor_exchange(tmp_path: Path) -> None:
    result = execution.run_worker(_doctor_request(), workspace=tmp_path, timeout_seconds=30)
    assert result.root.ok is True
    assert result.root.requestId == "doctor-parent"
    assert result.root.operation == "doctor"
    assert (tmp_path / "request.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "result.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "backend.log").stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))["requestId"] == (
        "doctor-parent"
    )

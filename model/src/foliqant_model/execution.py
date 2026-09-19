"""Parent-side execution boundary for the isolated MLX worker."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import BinaryIO, NoReturn

from pydantic import ValidationError

from .contracts import WorkerRequest, WorkerResult
from .errors import ModelError

_MAX_EXCHANGE_BYTES = 16 * 1024 * 1024
_MAX_TIMEOUT_SECONDS = 604_800
_TERMINATE_GRACE_SECONDS = 2.0
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
type _SignalHandler = Callable[[int, FrameType | None], object] | int | None


class _SignalInterruption(BaseException):
    """Internal control flow raised by the temporary SIGTERM handler."""


def run_worker(
    request: WorkerRequest,
    *,
    workspace: Path,
    timeout_seconds: int,
) -> WorkerResult:
    """Run one typed worker request in an isolated child process.

    The caller remains responsible for inspecting any paths reported by a
    successful result before publishing an artifact.
    """

    if (
        type(timeout_seconds) is not int
        or timeout_seconds <= 0
        or timeout_seconds > _MAX_TIMEOUT_SECONDS
    ):
        raise ModelError("ARGUMENT_INVALID", "Worker timeout must be between 1 and 604800 seconds")
    workspace = _validated_workspace(workspace)
    request_path = workspace / "request.json"
    result_path = workspace / "result.json"
    log_path = workspace / "backend.log"
    if result_path.exists() or result_path.is_symlink():
        raise ModelError("OUTPUT_EXISTS", "Worker result path already exists")

    request_bytes = (request.model_dump_json() + "\n").encode("utf-8")
    if len(request_bytes) > _MAX_EXCHANGE_BYTES:
        raise ModelError("ARGUMENT_INVALID", "Worker request exceeds the size limit")
    _write_exclusive_private(request_path, request_bytes)

    argv = [
        sys.executable,
        "-m",
        "foliqant_model.backend",
        "--request",
        str(request_path),
        "--result",
        str(result_path),
    ]
    return_code = _run_child(
        argv,
        workspace=workspace,
        log_path=log_path,
        timeout_seconds=timeout_seconds,
    )
    if return_code != 0:
        raise ModelError("BACKEND_FAILED", "Worker process failed")
    result = _read_worker_result(result_path)
    _validate_result_correlation(request, result)
    return result


def _validate_result_correlation(request: WorkerRequest, result: WorkerResult) -> None:
    expected = request.root
    actual = result.root
    if actual.requestId != expected.requestId or actual.operation != expected.operation:
        raise ModelError("OUTPUT_INVALID", "Worker result does not match its request")


def _validated_workspace(workspace: Path) -> Path:
    try:
        metadata = workspace.lstat()
    except OSError as error:
        raise ModelError("IO_FAILED", "Worker workspace is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Worker workspace must be a real directory")
    return workspace.absolute()


def _write_exclusive_private(path: Path, data: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise ModelError("OUTPUT_EXISTS", "Worker request path already exists") from error
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot persist worker request") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _worker_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "WANDB_DISABLED": "true",
            "WANDB_MODE": "disabled",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _open_private_log(path: Path) -> BinaryIO:
    descriptor = -1
    try:
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise OSError("log is not a regular file")
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | _NOFOLLOW,
            0o600,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("log is not a regular file")
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "ab", buffering=0)
        descriptor = -1
        return handle
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot open private worker log") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _run_child(
    argv: Sequence[str],
    *,
    workspace: Path,
    log_path: Path,
    timeout_seconds: float,
) -> int:
    """Run one controlled child and enforce deadline and group cancellation."""

    if timeout_seconds <= 0 or timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise ModelError("ARGUMENT_INVALID", "Worker timeout is outside the supported range")
    with _open_private_log(log_path) as log:
        process: subprocess.Popen[bytes] | None = None
        interrupted_during_spawn = False

        def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
            nonlocal interrupted_during_spawn
            if process is None:
                interrupted_during_spawn = True
                return
            raise _SignalInterruption

        previous_handler = _install_sigterm_handler(handle_sigterm)
        try:
            try:
                process = subprocess.Popen(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    cwd=workspace,
                    env=_worker_environment(),
                    shell=False,
                    start_new_session=True,
                )
            except OSError as error:
                if interrupted_during_spawn:
                    raise _SignalInterruption from error
                raise ModelError("BACKEND_FAILED", "Cannot start worker process") from error
            if interrupted_during_spawn:
                raise _SignalInterruption
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            if process is not None:
                _terminate_process_group(process)
            raise ModelError("TIMEOUT", "Worker process exceeded its deadline") from error
        except (KeyboardInterrupt, _SignalInterruption) as error:
            if process is not None:
                _terminate_process_group(process)
            raise ModelError("INTERRUPTED", "Worker process was interrupted") from error
        finally:
            _restore_sigterm_handler(previous_handler)
            if process is not None and process.poll() is None:
                _terminate_process_group(process)


def _install_sigterm_handler(
    handler: Callable[[int, FrameType | None], object],
) -> _SignalHandler:
    if threading.current_thread() is not threading.main_thread():
        return None
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handler)
    return previous


def _restore_sigterm_handler(previous: _SignalHandler) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline and _process_group_exists(process_group_id):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            pass
        time.sleep(min(0.01, remaining))
    if _process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _read_worker_result(path: Path) -> WorkerResult:
    raw = _read_private_regular_file(path)
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        return WorkerResult.model_validate(decoded)
    except (UnicodeDecodeError, ValueError, ValidationError) as error:
        raise ModelError("OUTPUT_INVALID", "Worker result is invalid") from error


def _read_private_regular_file(path: Path) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o077:
            raise OSError("result is not a private regular file")
        if before.st_size <= 0 or before.st_size > _MAX_EXCHANGE_BYTES:
            raise ValueError("result size is invalid")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("result changed during open")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_MAX_EXCHANGE_BYTES + 1)
        if not raw or len(raw) > _MAX_EXCHANGE_BYTES:
            raise ValueError("result size is invalid")
        return raw
    except FileNotFoundError as error:
        raise ModelError("BACKEND_FAILED", "Worker did not produce a result") from error
    except ValueError as error:
        raise ModelError("OUTPUT_INVALID", "Worker result is invalid") from error
    except OSError as error:
        raise ModelError("OUTPUT_INVALID", "Worker result cannot be read safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

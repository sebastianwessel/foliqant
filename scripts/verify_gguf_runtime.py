#!/usr/bin/env python3
"""Smoke-test one verified GGUF export in a local llama.cpp executable."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

_PROMPT = "Hello, my name is"
_MAX_PREDICT = 16
_CONTEXT_SIZE = 512
_SEED = 7


class SmokeError(Exception):
    """A redacted release-smoke failure."""


class _SignalInterruption(BaseException):
    """Internal control flow raised by the temporary SIGTERM handler."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_file(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise SmokeError("runtime executable is not a regular file")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _private_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment


def _terminate(process: subprocess.Popen[str]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            break
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            pass
        time.sleep(min(0.01, remaining))
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_bounded(argv: list[str], *, timeout_seconds: int) -> tuple[str, str, int]:
    process: subprocess.Popen[str] | None = None
    interrupted_during_spawn = False

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted_during_spawn
        if process is None:
            interrupted_during_spawn = True
            return
        raise _SignalInterruption

    previous_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=_private_environment(),
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            if interrupted_during_spawn:
                raise _SignalInterruption from error
            raise SmokeError("could not start llama.cpp") from error
        if interrupted_during_spawn:
            raise _SignalInterruption
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _terminate(process)
            raise SmokeError("llama.cpp smoke test exceeded its deadline") from error
        return stdout, stderr, process.returncode
    except (KeyboardInterrupt, _SignalInterruption) as error:
        if process is not None:
            _terminate(process)
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SmokeError("llama.cpp smoke test was interrupted") from error
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if process is not None and process.poll() is None:
            _terminate(process)


def _write_exclusive(path: Path, value: object) -> None:
    path = path.absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise SmokeError("evidence parent must be a real directory")
    data = _canonical_bytes(value) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp-")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise SmokeError("evidence output already exists") from error
        except OSError as error:
            raise SmokeError("could not publish evidence output") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="GGUF export directory")
    parser.add_argument("--output", type=Path, required=True, help="new evidence JSON path")
    parser.add_argument(
        "--llama-executable",
        type=Path,
        help="llama-cli path; defaults to llama-cli found on PATH",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    return parser


def _main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.timeout_seconds <= 3600:
        raise SmokeError("timeout must be between 1 and 3600 seconds")
    if args.output.exists() or args.output.is_symlink():
        raise SmokeError("evidence output already exists")

    from foliqant_model.artifacts import load_verified_artifact, require_disjoint_output
    from foliqant_model.errors import ModelError

    try:
        require_disjoint_output(args.output, [args.artifact])
    except ModelError as error:
        raise SmokeError(error.message) from error

    artifact = args.artifact.absolute()
    manifest = load_verified_artifact(artifact)
    if manifest.root.kind != "export" or manifest.root.details.exportMetadata.format != "gguf":
        raise SmokeError("artifact must be a verified GGUF export")
    models = [
        artifact / entry.path for entry in manifest.root.files if entry.path.endswith(".gguf")
    ]
    if len(models) != 1:
        raise SmokeError("GGUF export must inventory exactly one GGUF model")
    executable_value = (
        str(args.llama_executable)
        if args.llama_executable is not None
        else shutil.which("llama-cli")
    )
    if executable_value is None:
        raise SmokeError("llama-cli was not found; pass --llama-executable")
    executable = Path(executable_value).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SmokeError("llama executable is not an executable regular file")
    model = models[0]
    runtime_argv = [
        str(executable),
        "--model",
        str(model),
        "--prompt",
        _PROMPT,
        "--n-predict",
        str(_MAX_PREDICT),
        "--ctx-size",
        str(_CONTEXT_SIZE),
        "--temp",
        "0",
        "--seed",
        str(_SEED),
        "--no-conversation",
        "--simple-io",
        "--single-turn",
    ]
    started = time.monotonic()
    stdout, stderr, return_code = _run_bounded(runtime_argv, timeout_seconds=args.timeout_seconds)
    elapsed = float(time.monotonic() - started)
    if return_code != 0 or not stdout.strip() or not math.isfinite(elapsed):
        raise SmokeError("llama.cpp did not produce a successful bounded response")
    version_stdout, version_stderr, version_code = _run_bounded(
        [str(executable), "--version"], timeout_seconds=min(args.timeout_seconds, 10)
    )
    if version_code != 0 or not (version_stdout + version_stderr).strip():
        raise SmokeError("llama.cpp version probe failed")
    if load_verified_artifact(artifact).root.artifactId != manifest.root.artifactId:
        raise SmokeError("artifact changed during runtime verification")
    result = {
        "returnCode": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "outputCharacters": len(stdout),
        "elapsedSeconds": elapsed,
    }
    command = {"argv": runtime_argv, "timeoutSeconds": args.timeout_seconds}
    evidence = {
        "runtime": "llama.cpp",
        "runtimeVersion": (version_stdout + version_stderr).strip(),
        "checkedAt": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "inputArtifactId": manifest.root.artifactId,
        "commandSha256": hashlib.sha256(_canonical_bytes(command)).hexdigest(),
        "resultSha256": hashlib.sha256(_canonical_bytes(result)).hexdigest(),
    }
    report: dict[str, object] = {
        "schemaVersion": 1,
        "target": "llama.cpp",
        "evidence": evidence,
        "executableSha256": _digest_file(executable),
        "result": result,
    }
    report["evidenceId"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    _write_exclusive(args.output, report)
    print(
        _canonical_bytes(
            {
                "artifactId": manifest.root.artifactId,
                "evidenceId": report["evidenceId"],
                "outputCharacters": len(stdout),
                "returnCode": return_code,
                "runtime": "llama.cpp",
            }
        ).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except SmokeError as error:
        print(f"GGUF runtime verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None

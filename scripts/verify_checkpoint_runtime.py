#!/usr/bin/env python3
"""Smoke-test one verified checkpoint export in offline Transformers on CPU."""

from __future__ import annotations

import argparse
import hashlib
import importlib
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

_PROMPT = "Reply with one short greeting."
_MAX_NEW_TOKENS = 16
_SEED = 7
_MAX_RESULT_BYTES = 1024 * 1024


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
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
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


def _run_child(argv: list[str], *, timeout_seconds: int) -> tuple[str, str]:
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
            raise SmokeError("could not start Transformers smoke child") from error
        if interrupted_during_spawn:
            raise _SignalInterruption
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _terminate(process)
            raise SmokeError("Transformers smoke test exceeded its deadline") from error
        if process.returncode != 0:
            raise SmokeError("Transformers smoke child failed")
        return stdout, stderr
    except (KeyboardInterrupt, _SignalInterruption) as error:
        if process is not None:
            _terminate(process)
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SmokeError("Transformers smoke test was interrupted") from error
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


def _read_child_result(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_RESULT_BYTES:
        raise SmokeError("Transformers child result is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SmokeError("Transformers child result is invalid") from error
    if not isinstance(value, dict):
        raise SmokeError("Transformers child result is invalid")
    required = {
        "elapsedSeconds",
        "generatedText",
        "generatedTokens",
        "pythonVersion",
        "torchVersion",
        "transformersVersion",
    }
    if set(value) != required:
        raise SmokeError("Transformers child result is invalid")
    generated_tokens = value["generatedTokens"]
    elapsed_seconds = value["elapsedSeconds"]
    string_fields = (
        value["generatedText"],
        value["pythonVersion"],
        value["torchVersion"],
        value["transformersVersion"],
    )
    if (
        isinstance(generated_tokens, bool)
        or not isinstance(generated_tokens, int)
        or not 1 <= generated_tokens <= _MAX_NEW_TOKENS
        or isinstance(elapsed_seconds, bool)
        or not isinstance(elapsed_seconds, (int, float))
        or not math.isfinite(elapsed_seconds)
        or elapsed_seconds < 0
        or not all(isinstance(item, str) and item for item in string_fields)
    ):
        raise SmokeError("Transformers child result is invalid")
    return value


def _runtime_child(artifact: Path, result_path: Path) -> int:
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    auto_model = transformers.AutoModelForCausalLM
    auto_tokenizer = transformers.AutoTokenizer
    started = time.monotonic()
    torch.manual_seed(_SEED)
    tokenizer = auto_tokenizer.from_pretrained(
        artifact,
        trust_remote_code=False,
        local_files_only=True,
    )
    model = (
        auto_model.from_pretrained(
            artifact,
            trust_remote_code=False,
            local_files_only=True,
            dtype=torch.float32,
        )
        .to("cpu")
        .eval()
    )
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": _PROMPT}],
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.inference_mode():
        tokens = model.generate(
            **inputs,
            max_new_tokens=_MAX_NEW_TOKENS,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = tokens[0, inputs.input_ids.shape[1] :]
    generated_count = int(generated.numel())
    elapsed = float(time.monotonic() - started)
    if generated_count <= 0 or generated_count > _MAX_NEW_TOKENS or not math.isfinite(elapsed):
        raise SmokeError("Transformers produced an invalid bounded result")
    result = {
        "generatedTokens": generated_count,
        "generatedText": tokenizer.decode(generated, skip_special_tokens=True),
        "elapsedSeconds": elapsed,
        "pythonVersion": sys.version.split()[0],
        "torchVersion": str(torch.__version__),
        "transformersVersion": str(transformers.__version__),
    }
    _write_exclusive(result_path, result)
    return 0


def _public_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="checkpoint export directory")
    parser.add_argument("--output", type=Path, required=True, help="new evidence JSON path")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def _main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "--runtime-child":
        return _runtime_child(Path(argv[1]), Path(argv[2]))
    args = _public_parser().parse_args(argv)
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
    if (
        manifest.root.kind != "export"
        or manifest.root.details.exportMetadata.format != "checkpoint"
    ):
        raise SmokeError("artifact must be a verified checkpoint export")
    artifact_id = manifest.root.artifactId
    output_parent = args.output.absolute().parent
    output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_directory = Path(tempfile.mkdtemp(prefix=".checkpoint-smoke-", dir=output_parent))
    temporary_directory.chmod(0o700)
    child_result = temporary_directory / "result.json"
    try:
        _run_child(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--runtime-child",
                str(artifact),
                str(child_result),
            ],
            timeout_seconds=args.timeout_seconds,
        )
        result = _read_child_result(child_result)
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)
    if load_verified_artifact(artifact).root.artifactId != artifact_id:
        raise SmokeError("artifact changed during runtime verification")
    runtime_version = result.get("transformersVersion")
    if not isinstance(runtime_version, str) or not runtime_version:
        raise SmokeError("Transformers child omitted its runtime version")
    command = {
        "artifactPath": str(artifact),
        "dtype": "float32",
        "localFilesOnly": True,
        "maxNewTokens": _MAX_NEW_TOKENS,
        "prompt": _PROMPT,
        "pythonExecutable": str(Path(sys.executable).resolve()),
        "scriptSha256": _digest_file(Path(__file__)),
        "seed": _SEED,
        "timeoutSeconds": args.timeout_seconds,
        "trustRemoteCode": False,
    }
    evidence = {
        "runtime": "transformers",
        "runtimeVersion": runtime_version,
        "checkedAt": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "inputArtifactId": artifact_id,
        "commandSha256": hashlib.sha256(_canonical_bytes(command)).hexdigest(),
        "resultSha256": hashlib.sha256(_canonical_bytes(result)).hexdigest(),
    }
    report: dict[str, object] = {
        "schemaVersion": 1,
        "target": "transformers",
        "evidence": evidence,
        "executableSha256": _digest_file(Path(sys.executable)),
        "result": result,
    }
    report["evidenceId"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    _write_exclusive(args.output, report)
    print(
        _canonical_bytes(
            {
                "artifactId": artifact_id,
                "evidenceId": report["evidenceId"],
                "generatedTokens": result["generatedTokens"],
                "runtime": "transformers",
                "runtimeVersion": runtime_version,
            }
        ).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except SmokeError as error:
        print(f"checkpoint runtime verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None

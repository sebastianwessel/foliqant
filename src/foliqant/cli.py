"""Command-line interface for compiling and running in-memory workflows."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Never, TextIO, cast

from foliqant.compiler import CompilationError
from foliqant.contracts.decoding import MAX_ENVELOPE_BYTES, decode_envelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.plan import (
    DecisionStepPlan,
    HandlerStepPlan,
    LlmStepPlan,
    McpStepPlan,
    SourceLocation,
    WorkflowPlan,
)

if TYPE_CHECKING:
    from foliqant.adapters.telemetry.logging import LoggingRuntime, LogLabels
    from foliqant.bootstrap import PreparedApplication

_EXIT_INPUT = 2
_EXIT_DEPENDENCY = 3
_EXIT_RUNTIME = 4
_EXIT_CANCELLED = 130

_MESSAGES: dict[str, str] = {
    "invalid_arguments": "Invalid arguments; use --help for supported options.",
    ErrorCode.INVALID_CONFIGURATION.value: "The workflow configuration is invalid.",
    ErrorCode.INVALID_INPUT.value: "The input does not satisfy the required contract.",
    ErrorCode.INVALID_OUTPUT.value: "An operation returned an invalid result.",
    ErrorCode.UNAUTHENTICATED.value: "Authentication is required.",
    ErrorCode.FORBIDDEN.value: "The operation is not authorized.",
    ErrorCode.NOT_FOUND.value: "The requested resource was not found.",
    ErrorCode.MISSING_BINDING.value: "A required input binding is unavailable.",
    ErrorCode.TIMEOUT.value: "The operation exceeded its deadline.",
    ErrorCode.BUDGET_EXHAUSTED.value: "The execution budget is exhausted.",
    ErrorCode.DEPENDENCY_FAILURE.value: "A required dependency is unavailable.",
    ErrorCode.CONFLICT.value: "The request conflicts with an existing operation.",
    ErrorCode.UNCERTAIN_EFFECT.value: "An external operation requires reconciliation.",
    ErrorCode.CANCELLED.value: "The execution was cancelled.",
    ErrorCode.CAPACITY_EXCEEDED.value: "The service has reached its admission limit.",
}

_SCAFFOLD: Mapping[str, str] = {
    "foliqant.yaml": """version: 1
workflows:
  demo: workflows/demo
""",
    "workflows/demo/workflow.yaml": """version: 1
name: demo
start: done
steps:
  done:
    type: finish
    outcome: completed
""",
    ".env.example": "# Add only environment variables referenced by foliqant.yaml.\n",
    ".gitignore": ".env\n",
    "envelope.json": """{
  "payload": {
    "message": "hello"
  },
  "metadata": {}
}
""",
    "README.md": """# Foliqant workflow

Run the synthetic, model-free workflow:

```sh
foliqant run --workflow demo --input envelope.json
```
""",
}

_OPTIONAL_COMPONENTS: Mapping[str, tuple[str, ...]] = {
    "anthropic": ("anthropic",),
    "mcp": ("mcp", "httpx2", "cryptography"),
    "openai": ("openai",),
    "telemetry": ("opentelemetry.sdk",),
}


@dataclass(frozen=True, slots=True)
class _CliFailure(Exception):
    code: str
    exit_code: int
    location: SourceLocation | None = None
    reason: str | None = None
    field: str | None = None
    hint: str | None = None
    retryable: bool = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise _CliFailure("invalid_arguments", _EXIT_INPUT)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="foliqant", description="Compile and run deterministic workflows.")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create a minimal model-free project")
    init.add_argument("destination", type=Path, metavar="DEST")

    for name, help_text in (
        ("validate", "Compile all configured workflows offline"),
        ("doctor", "Check configuration and installed optional dependencies offline"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--config",
            type=Path,
            default=Path("foliqant.yaml"),
            help="configuration file (default: ./foliqant.yaml)",
        )

    explain = commands.add_parser("explain", help="Describe compiled workflow plans offline")
    explain.add_argument(
        "--config",
        type=Path,
        default=Path("foliqant.yaml"),
        help="configuration file (default: ./foliqant.yaml)",
    )
    explain.add_argument("--workflow")

    run = commands.add_parser("run", help="Run one workflow with an envelope")
    run.add_argument(
        "--config",
        type=Path,
        default=Path("foliqant.yaml"),
        help="configuration file (default: ./foliqant.yaml)",
    )
    run.add_argument("--workflow", required=True)
    run.add_argument("--input", required=True, metavar="PATH|-")
    run.add_argument("--tenant-id")
    run.add_argument("--principal-id")
    run.add_argument("--debug", action="store_true")

    return parser


def _json_line(value: object, *, stream: TextIO | None = None) -> None:
    destination = sys.stdout if stream is None else stream
    destination.write(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    destination.write("\n")


def _safe_location(location: SourceLocation | None) -> dict[str, object] | None:
    if location is None or type(location.path) is not str:
        return None
    path = PurePosixPath(location.path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(location.path) > 512
        or any(not part or any(ord(character) < 32 for character in part) for part in path.parts)
        or type(location.line) is not int
        or type(location.column) is not int
        or not 1 <= location.line <= 10_000_000
        or not 1 <= location.column <= 10_000_000
    ):
        return None
    return {"path": location.path, "line": location.line, "column": location.column}


def _failure_payload(failure: _CliFailure) -> dict[str, object]:
    error: dict[str, object] = {
        "code": failure.code,
        "message": _MESSAGES.get(failure.code, _MESSAGES[ErrorCode.DEPENDENCY_FAILURE.value]),
        "retryable": failure.retryable,
    }
    if failure.reason is not None and failure.reason.replace("_", "").isalnum():
        error["reason"] = failure.reason[:64]
    if failure.field is not None:
        error["field"] = failure.field
    if failure.hint is not None:
        error["hint"] = failure.hint
    location = _safe_location(failure.location)
    if location is not None:
        error["location"] = location
    return {"error": error}


def _exit_for_service_error(error: ServiceError) -> int:
    if error.code in {
        ErrorCode.INVALID_CONFIGURATION,
        ErrorCode.INVALID_INPUT,
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.NOT_FOUND,
        ErrorCode.CONFLICT,
    }:
        return _EXIT_INPUT
    if error.code is ErrorCode.CANCELLED:
        return _EXIT_CANCELLED
    return _EXIT_RUNTIME


def _prepare(config_path: Path) -> PreparedApplication:
    from foliqant.bootstrap import prepare_application

    try:
        return prepare_application(config_path)
    except CompilationError as error:
        raise _CliFailure(
            ErrorCode.INVALID_CONFIGURATION.value,
            _EXIT_INPUT,
            location=error.location,
            reason=error.reason,
            field=error.field,
            hint=error.hint,
        ) from None
    except ServiceError:
        raise
    except (OSError, TypeError, ValueError):
        raise _CliFailure(ErrorCode.INVALID_CONFIGURATION.value, _EXIT_INPUT) from None


def _init(destination: Path) -> dict[str, object]:
    target = destination.absolute()
    parent = target.parent
    if target.exists() or target.is_symlink() or not parent.is_dir():
        raise _CliFailure(ErrorCode.CONFLICT.value, _EXIT_INPUT)
    temporary = Path(tempfile.mkdtemp(prefix=".foliqant-init-", dir=parent))
    try:
        for relative, content in _SCAFFOLD.items():
            output = temporary / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8", newline="\n")
        if target.exists() or target.is_symlink():
            raise _CliFailure(ErrorCode.CONFLICT.value, _EXIT_INPUT)
        os.rename(temporary, target)
    except _CliFailure:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except FileExistsError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise _CliFailure(ErrorCode.CONFLICT.value, _EXIT_INPUT) from None
    except OSError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise _CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME) from None
    return {"command": "init", "status": "created"}


def _plan_report(plan: WorkflowPlan) -> dict[str, object]:
    steps: list[dict[str, object]] = []
    for step in plan.steps:
        item: dict[str, object] = {"name": step.name, "type": step.type}
        if step.next is not None:
            item["next"] = step.next
        if step.on_unresolved is not None:
            item["on_unresolved"] = step.on_unresolved
        if isinstance(step, DecisionStepPlan):
            item["model"] = step.model
            item["on_answer"] = dict(step.on_answer)
        elif isinstance(step, LlmStepPlan):
            item["model"] = step.model
            if step.tools is not None:
                item["tools"] = {"server": step.tools.server, "allow": list(step.tools.allow)}
        elif isinstance(step, McpStepPlan):
            item["server"], item["tool"] = step.server, step.tool
        elif isinstance(step, HandlerStepPlan):
            item["handler"] = step.handler
        steps.append(item)
    return {
        "name": plan.name,
        "revision": plan.revision,
        "start": plan.start,
        "steps": steps,
    }


def _validate(config_path: Path) -> dict[str, object]:
    prepared = _prepare(config_path)
    return {
        "command": "validate",
        "status": "valid",
        "configuration_digest": prepared.configuration_digest,
        "workflows": sorted(prepared.plans),
    }


def _explain(config_path: Path, workflow: str | None) -> dict[str, object]:
    prepared = _prepare(config_path)
    if workflow is not None:
        plan = prepared.plans.get(workflow)
        if plan is None:
            raise _CliFailure(ErrorCode.NOT_FOUND.value, _EXIT_INPUT)
        plans = [plan]
    else:
        plans = [prepared.plans[name] for name in sorted(prepared.plans)]
    return {
        "command": "explain",
        "configuration_digest": prepared.configuration_digest,
        "workflows": [_plan_report(plan) for plan in plans],
    }


def _doctor(config_path: Path) -> dict[str, object]:
    prepared = _prepare(config_path)
    optional = {
        name: all(_module_available(module) for module in modules)
        for name, modules in _OPTIONAL_COMPONENTS.items()
    }
    return {
        "command": "doctor",
        "status": "ok",
        "configuration_digest": prepared.configuration_digest,
        "workflows": sorted(prepared.plans),
        "optional_dependencies": optional,
    }


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _read_envelope(source: str) -> bytes:
    if source == "-":
        try:
            data = sys.stdin.buffer.read(MAX_ENVELOPE_BYTES + 1)
        except OSError:
            raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT) from None
        if len(data) > MAX_ENVELOPE_BYTES:
            raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT)
        return data

    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_ENVELOPE_BYTES:
                raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT)
            chunks: list[bytes] = []
            remaining = MAX_ENVELOPE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
    except _CliFailure:
        raise
    except OSError:
        raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT) from None
    if len(data) > MAX_ENVELOPE_BYTES:
        raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT)
    return data


def _logging_labels(prepared: PreparedApplication) -> LogLabels:
    from foliqant.adapters.telemetry.logging import LogLabels

    return LogLabels(
        services=frozenset({"foliqant"}),
        workflows=frozenset(prepared.plans),
        steps=frozenset(step.name for plan in prepared.plans.values() for step in plan.steps),
    )


async def _close_logging(runtime: LoggingRuntime) -> None:
    if not await asyncio.to_thread(runtime.close):
        _json_line({"level": "warning", "event": "logging_shutdown_incomplete"}, stream=sys.stderr)


async def _run(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    from foliqant.adapters.telemetry.logging import configure_logging
    from foliqant.bootstrap import open_application

    prepared = _prepare(args.config)
    if args.workflow not in prepared.plans:
        raise _CliFailure(ErrorCode.NOT_FOUND.value, _EXIT_INPUT)
    try:
        envelope = decode_envelope(_read_envelope(args.input))
        identity = (
            None
            if args.tenant_id is None and args.principal_id is None
            else Identity(tenant_id=args.tenant_id, principal_id=args.principal_id)
        )
    except ServiceError:
        raise
    except (TypeError, ValueError):
        raise _CliFailure(ErrorCode.INVALID_INPUT.value, _EXIT_INPUT) from None
    logging_runtime = configure_logging(debug=args.debug, labels=_logging_labels(prepared))
    try:
        async with open_application(
            prepared, environment=os.environ, install_global_telemetry=True
        ) as application:
            result = await application.run(args.workflow, envelope, identity=identity)
    finally:
        await _close_logging(logging_runtime)
    if result.execution.status not in {"completed", "needs_review"}:
        error = result.execution.error
        if error is None:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        raise ServiceError(error.code, retryable=error.retryable)
    return cast(dict[str, object], result.model_dump(mode="json")), 0


def _dispatch(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    if args.command == "init":
        return _init(args.destination), 0
    if args.command == "validate":
        return _validate(args.config), 0
    if args.command == "explain":
        return _explain(args.config, args.workflow), 0
    if args.command == "doctor":
        return _doctor(args.config), 0
    return asyncio.run(_run(args))


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one command with a single JSON success or safe failure object."""
    try:
        args = _parser().parse_args(argv)
        payload, exit_code = _dispatch(args)
        _json_line(payload)
        return exit_code
    except _CliFailure as failure:
        _json_line(_failure_payload(failure), stream=sys.stderr)
        return failure.exit_code
    except ServiceError as error:
        service_failure = _CliFailure(
            error.code.value, _exit_for_service_error(error), retryable=error.retryable
        )
        _json_line(_failure_payload(service_failure), stream=sys.stderr)
        return service_failure.exit_code
    except (ImportError, ModuleNotFoundError):
        dependency_failure = _CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_DEPENDENCY)
        _json_line(_failure_payload(dependency_failure), stream=sys.stderr)
        return dependency_failure.exit_code
    except KeyboardInterrupt:
        cancelled_failure = _CliFailure(ErrorCode.CANCELLED.value, _EXIT_CANCELLED)
        _json_line(_failure_payload(cancelled_failure), stream=sys.stderr)
        return cancelled_failure.exit_code
    except (OSError, TypeError, ValueError):
        runtime_failure = _CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME)
        _json_line(_failure_payload(runtime_failure), stream=sys.stderr)
        return runtime_failure.exit_code
    except Exception:
        # No exception text or traceback may cross this public boundary.
        internal_failure = _CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME)
        _json_line(_failure_payload(internal_failure), stream=sys.stderr)
        return internal_failure.exit_code


__all__ = ["main"]

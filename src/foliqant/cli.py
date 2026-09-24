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
from foliqant.compiler.errors import render_problems
from foliqant.contracts.decoding import MAX_ENVELOPE_BYTES, decode_envelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.plan import Diagnostic, SourceLocation

if TYPE_CHECKING:
    from foliqant.adapters.telemetry.logging import LoggingRuntime, LogLabels
    from foliqant.bootstrap import PreparedApplication

_EXIT_STALE = 1
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
    "stale_output": "The generated file differs from the current configuration.",
}

_SCAFFOLD: Mapping[str, str] = {
    "config/settings.yaml": """models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: false
    concurrency: 1
    queue_limit: 0
    request_timeout: 300
    options:
      max_tokens: 8192
      temperature: 0.1
      reasoning_effort: low
execution:
  model_timeout: 300
  run_timeout: 310
""",
    "config/demo/workflow.yaml": """defaults:
  model: local
  on_unresolved:
    outcome: needs_review
output:
  pointer: /flows/summarize/result
flows:
  summarize:
    input:
      message:
        pointer: /payload/message
    transition:
      outcome: completed
""",
    "config/demo/summarize/flow.yaml": """output:
  pointer: /steps/summarize/result
steps:
  - summarize
""",
    "config/demo/summarize/summarize.step.md": """---
type: llm
input:
  message:
    pointer: /payload/message
output: text
---
Summarize the supplied message in one sentence, preserving its language.
""",
    "config/.env.example": """# Copy to config/.env and set your local model ID.
MODEL_ID=your-served-model-id
MODEL_BASE_URL=http://127.0.0.1:8000/v1
""",
    ".gitignore": ".env\n.foliqant/\n",
    "envelope.json": """{
  "payload": {"message": "Please send my account statement."},
  "metadata": {}
}
""",
    "README.md": """# Workflow project

Install `foliqant[openai]`. Copy `config/.env.example` to `config/.env`
and set the endpoint and model ID served by your local backend.

Validate without contacting the model, look at the graph, then run:

```sh
foliqant validate --strict
foliqant explain --format mermaid
foliqant run --workflow demo --input envelope.json
```

The default configuration is `config/settings.yaml`; use `--config` for another path.
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
    diagnostics: tuple[Diagnostic, ...] = ()
    problems: tuple[Diagnostic, ...] = ()


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise _CliFailure("invalid_arguments", _EXIT_INPUT)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="foliqant", description="Compile and run deterministic workflows.")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create a minimal local-model workflow project")
    init.add_argument("destination", type=Path, metavar="DEST")

    for name, help_text in (
        ("validate", "Compile all configured workflows offline and report diagnostics"),
        ("doctor", "Check configuration and installed optional dependencies offline"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--config",
            type=Path,
            default=Path("config/settings.yaml"),
            help="configuration file (default: ./config/settings.yaml)",
        )
        if name == "validate":
            command.add_argument(
                "--strict", action="store_true", help="fail when any warning is reported"
            )

    explain = commands.add_parser("explain", help="Describe compiled workflow graphs offline")
    explain.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="configuration file (default: ./config/settings.yaml)",
    )
    explain.add_argument("--workflow")
    explain.add_argument(
        "--format",
        choices=("json", "mermaid", "dot"),
        default="json",
        help="json (default) or a Mermaid/Graphviz graph",
    )
    explain.add_argument(
        "--all",
        action="store_true",
        help="every workflow; with mermaid/dot one Markdown document with a section each",
    )
    explain.add_argument(
        "--legend",
        action="store_true",
        help="append a legend of step shapes to a mermaid/dot graph (--all always has one)",
    )
    explain.add_argument("--output", type=Path, help="write the rendering to this file")
    explain.add_argument(
        "--check",
        action="store_true",
        help="compare --output with the current rendering; exit 1 when it is stale",
    )

    run = commands.add_parser("run", help="Run one workflow with an envelope")
    run.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="configuration file (default: ./config/settings.yaml)",
    )
    run.add_argument("--workflow", required=True)
    run.add_argument("--input", required=True, metavar="PATH|-")
    run.add_argument("--tenant-id")
    run.add_argument("--principal-id")
    run.add_argument("--debug", action="store_true")

    evaluation = commands.add_parser(
        "evaluate", help="Measure configured ground truth; save detailed private results"
    )
    evaluation.add_argument("--config", type=Path)
    mode = evaluation.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="validate gold and targets without I/O to providers"
    )
    mode.add_argument(
        "--replay", type=Path, metavar="REPORT", help="rescore saved results without inference"
    )
    mode.add_argument(
        "--compare", type=Path, metavar="REPORT", help="compare a saved candidate report offline"
    )
    evaluation.add_argument(
        "--baseline", type=Path, metavar="REPORT", help="saved baseline report for --compare"
    )
    evaluation.add_argument(
        "--output", type=Path, help="new private report file; never overwritten"
    )
    evaluation.add_argument("--max-concurrency", type=int)
    evaluation.add_argument(
        "--timeout", type=float, help="per-case deadline in seconds (default: 300)"
    )
    evaluation.add_argument("--repeat", type=int, help="attempts per source case (default: 1)")

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
    payload: dict[str, object] = {"error": error}
    if failure.problems:
        payload["problems"] = _diagnostics(failure.problems)
    if failure.diagnostics:
        payload["diagnostics"] = _diagnostics(failure.diagnostics)
    return payload


def _failure_text(failure: _CliFailure) -> str:
    """Human-readable failure for standard error: one line per problem, then a summary."""
    message = _MESSAGES.get(failure.code, _MESSAGES[ErrorCode.DEPENDENCY_FAILURE.value])
    if failure.problems:
        count = len(failure.problems)
        summary = f"foliqant: {failure.code}: {count} problem{'s' if count != 1 else ''}."
        return render_problems(failure.problems) + "\n" + summary + "\n"
    hint = f" (hint: {failure.hint})" if failure.hint else ""
    return f"foliqant: {failure.code}: {message}{hint}\n"


def _diagnostics(items: Sequence[Diagnostic]) -> list[dict[str, object]]:
    """Content-free compiler findings with safe source coordinates."""
    result: list[dict[str, object]] = []
    for item in items:
        value: dict[str, object] = {"code": item.code, "level": item.level, "message": item.message}
        location = _safe_location(item.location)
        if location is not None:
            value["location"] = location
        if item.field is not None:
            value["field"] = item.field
        if item.hint is not None:
            value["hint"] = item.hint
        result.append(value)
    return result


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


def _compilation_failure(error: CompilationError) -> _CliFailure:
    return _CliFailure(
        ErrorCode.INVALID_CONFIGURATION.value,
        _EXIT_INPUT,
        location=error.location,
        reason=error.reason,
        field=error.field,
        hint=error.hint,
        diagnostics=error.diagnostics,
        problems=error.problems,
    )


def _prepare(config_path: Path, *, strict: bool = False) -> PreparedApplication:
    from foliqant.bootstrap import prepare_application

    try:
        return prepare_application(config_path, strict=strict)
    except CompilationError as error:
        raise _compilation_failure(error) from None
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


def _validate(config_path: Path, *, strict: bool = False) -> dict[str, object]:
    # A strict preparation fails with every warning, not only the first.
    prepared = _prepare(config_path, strict=strict)
    return {
        "command": "validate",
        "status": "valid",
        "configuration_digest": prepared.configuration_digest,
        "workflows": sorted(prepared.plans),
        "diagnostics": _diagnostics(prepared.diagnostics),
    }


def _explain(args: argparse.Namespace) -> tuple[dict[str, object] | str, int]:
    from foliqant.graph import render_document, render_dot, render_mermaid, workflow_graph

    output_format: str = args.format
    if (
        (args.check and args.output is None)
        or (args.all and args.workflow is not None)
        or (args.legend and output_format == "json")
    ):
        raise _CliFailure(
            "invalid_arguments",
            _EXIT_INPUT,
            hint=(
                "--check needs --output; --all and --workflow exclude each other; "
                "--legend needs --format mermaid or dot."
            ),
        )
    prepared = _prepare(args.config)
    if args.workflow is not None:
        plan = prepared.plans.get(args.workflow)
        if plan is None:
            raise _CliFailure(ErrorCode.NOT_FOUND.value, _EXIT_INPUT)
        plans = [plan]
    else:
        plans = [prepared.plans[name] for name in sorted(prepared.plans)]
    rendering: dict[str, object] | str
    if output_format == "json":
        rendering = {
            "command": "explain",
            "configuration_digest": prepared.configuration_digest,
            "workflows": [workflow_graph(plan, prepared).to_json() for plan in plans],
        }
    elif args.all:
        rendering = render_document(prepared, "mermaid" if output_format == "mermaid" else "dot")
    elif len(plans) != 1:
        # One diagram per output; select the workflow or render the document.
        raise _CliFailure(
            "invalid_arguments",
            _EXIT_INPUT,
            hint="Select one workflow with --workflow NAME, or render all with --all.",
        )
    else:
        graph = workflow_graph(plans[0], prepared)
        render = render_mermaid if output_format == "mermaid" else render_dot
        rendering = render(graph, legend=args.legend)
    if args.output is None:
        return rendering, 0
    text = (
        rendering
        if isinstance(rendering, str)
        else json.dumps(rendering, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    )
    return _write_rendering(args.output, text, check=args.check), 0


def _write_rendering(path: Path, text: str, *, check: bool) -> dict[str, object]:
    """Write generated documentation atomically, or compare it for drift with ``check``."""
    if check:
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            current = None
        if current != text:
            raise _CliFailure(
                "stale_output",
                _EXIT_STALE,
                hint="Run the same command without --check to regenerate the file.",
            )
        return {"command": "explain", "status": "current", "output": str(path)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".foliqant-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except OSError:
        raise _CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME) from None
    return {"command": "explain", "status": "written", "output": str(path)}


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
        "handlers": sorted(prepared.handler_contracts),
        "diagnostics": _diagnostics(prepared.diagnostics),
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
        flows=frozenset(flow.name for plan in prepared.plans.values() for flow in plan.flows),
        steps=frozenset(
            step.name
            for plan in prepared.plans.values()
            for flow in plan.flows
            for step in flow.steps
        ),
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
    except CompilationError as failure:
        # Declared handlers need host registrations; the generic CLI has none.
        raise _compilation_failure(failure) from None
    finally:
        await _close_logging(logging_runtime)
    if result.execution.status not in {"completed", "needs_review"}:
        error = result.execution.error
        if error is None:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        raise ServiceError(error.code, retryable=error.retryable)
    return cast(dict[str, object], result.model_dump(mode="json")), 0


def _dispatch(args: argparse.Namespace) -> tuple[dict[str, object] | str, int]:
    if args.command == "init":
        return _init(args.destination), 0
    if args.command == "validate":
        return _validate(args.config, strict=args.strict), 0
    if args.command == "explain":
        return _explain(args)
    if args.command == "doctor":
        return _doctor(args.config), 0
    if args.command == "evaluate":
        from foliqant.evaluation.command import compare_report_files, evaluate_configuration

        if args.check and args.output is not None:
            raise _CliFailure("invalid_arguments", _EXIT_INPUT)
        if args.compare is not None:
            if (
                args.baseline is None
                or args.config is not None
                or args.max_concurrency is not None
                or args.timeout is not None
                or args.repeat is not None
            ):
                raise _CliFailure("invalid_arguments", _EXIT_INPUT)
            return compare_report_files(args.compare, args.baseline, output=args.output)
        if args.baseline is not None:
            raise _CliFailure("invalid_arguments", _EXIT_INPUT)
        return asyncio.run(
            evaluate_configuration(
                _prepare(args.config or Path("config/settings.yaml")),
                check=args.check,
                replay=args.replay,
                output=args.output,
                max_concurrency=1 if args.max_concurrency is None else args.max_concurrency,
                timeout=300.0 if args.timeout is None else args.timeout,
                repeat=args.repeat,
            )
        )
    return asyncio.run(_run(args))


def _report(failure: _CliFailure) -> int:
    """Failure status as one JSON object on stdout; the readable text on stderr."""
    _json_line(_failure_payload(failure))
    sys.stderr.write(_failure_text(failure))
    return failure.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one command: one JSON status object (or rendering) on stdout.

    Failures print the same JSON failure object on stdout and readable text on
    stderr: ``<file>:<line>:<column>: <code> at <field>: <message> (hint: ...)``
    for every configuration problem. Exit codes: ``0`` success, ``1`` stale
    ``explain --check`` output or gold mismatch, ``2`` invalid arguments, input
    or configuration, ``3`` missing optional dependency, ``4`` runtime failure,
    ``130`` interruption.
    """
    try:
        args = _parser().parse_args(argv)
        payload, exit_code = _dispatch(args)
        if isinstance(payload, str):
            # Graph renderings are plain text for direct use in diagram tools.
            sys.stdout.write(payload)
        else:
            _json_line(payload)
        return exit_code
    except _CliFailure as failure:
        return _report(failure)
    except CompilationError as error:
        return _report(_compilation_failure(error))
    except ServiceError as error:
        return _report(
            _CliFailure(error.code.value, _exit_for_service_error(error), retryable=error.retryable)
        )
    except (ImportError, ModuleNotFoundError):
        return _report(_CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_DEPENDENCY))
    except KeyboardInterrupt:
        return _report(_CliFailure(ErrorCode.CANCELLED.value, _EXIT_CANCELLED))
    except (OSError, TypeError, ValueError):
        return _report(_CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME))
    except Exception:
        # No exception text or traceback may cross this public boundary.
        return _report(_CliFailure(ErrorCode.DEPENDENCY_FAILURE.value, _EXIT_RUNTIME))


__all__ = ["main"]

"""Small example-only environment and command helpers; no workflow execution code."""

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from foliqant import load_environment
from foliqant.compiler import CompilationError
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import JsonValue
from foliqant.evaluation import EvaluationReport

ROOT = Path(__file__).resolve().parents[1]


def example_environment(environment: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Explicitly read the shared root .env; process values take precedence."""
    return load_environment(ROOT / "foliqant.yaml", environment)


def command(run: Callable[[], Awaitable[dict[str, JsonValue]]]) -> int:
    """Print one JSON result or a sanitized error, without hiding cancellation."""

    async def invoke() -> dict[str, JsonValue]:
        return await run()

    try:
        result = asyncio.run(invoke())
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        code = (
            error.code
            if isinstance(error, ServiceError)
            else ErrorCode.INVALID_CONFIGURATION
            if isinstance(error, CompilationError)
            else ErrorCode.DEPENDENCY_FAILURE
        )
        safe = ServiceError(code)
        print(json.dumps({"error": {"code": safe.code, "message": str(safe)}}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok", True) else 1


def evaluation_output(reports: Sequence[EvaluationReport], *, mode: str) -> dict[str, JsonValue]:
    """Expose measured reports, retaining failed assertions in the command status."""
    return {
        "ok": all(report.checks.passed == report.checks.total for report in reports),
        "mode": mode,
        "reports": [report.to_dict() for report in reports],
    }

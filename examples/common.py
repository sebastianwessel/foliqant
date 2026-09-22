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
from foliqant.core.json import JsonValue, thaw_json
from foliqant.evaluation import EvaluationReport, EvaluationSuite
from foliqant.evaluation.artifact import default_artifact_path, write_report
from foliqant.evaluation.dataset import EvaluationDataset, read_dataset

ROOT = Path(__file__).resolve().parents[1]


def example_environment(environment: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Explicitly read the shared root .env; process values take precedence."""
    return load_environment(ROOT / "settings.yaml", environment)


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


async def evaluation_output(
    reports: Sequence[EvaluationReport],
    *,
    mode: str,
    dataset: EvaluationDataset,
    output: Path | None = None,
) -> dict[str, JsonValue]:
    """Persist the full private report and return only safe counts plus its path."""
    destination = (
        private_output_path(output)
        if output is not None
        else default_artifact_path(ROOT / ".foliqant/evaluations", prefix="example-report")
    )
    assert destination is not None
    await asyncio.to_thread(
        write_report,
        destination,
        reports,
        mode=mode,
        dataset_name=dataset.name,
        dataset_revision=dataset.revision,
    )
    return {
        "ok": all(report.checks.passed == report.checks.total for report in reports),
        "mode": mode,
        "suites": len(reports),
        "cases": sum(report.source_case_count or len(report.cases) for report in reports),
        "attempts": sum(len(report.cases) for report in reports),
        "passed_checks": sum(report.checks.passed for report in reports),
        "total_checks": sum(report.checks.total for report in reports),
        "report": str(destination.absolute()),
    }


def suite_document(
    suite: "EvaluationSuite",
    *,
    workflow: str,
    step: str | None = None,
    flow: str | None = None,
    metrics: Sequence[JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Represent authored synthetic Python gold in the shared dataset wire format."""
    document: dict[str, JsonValue] = {
        "name": suite.name,
        "workflow": workflow,
        "cases": [
            {
                "id": case.id,
                "input": case.envelope().model_dump(mode="json"),
                "expectations": [
                    {
                        "name": check.name,
                        "path": check.path,
                        "expected": thaw_json(check.expected),
                        "comparison": check.comparison,
                    }
                    for check in case.expectations
                ],
            }
            for case in suite.cases
        ],
        "metrics": list(metrics or ()),
    }
    if flow is not None:
        document["flow"] = flow
    if step is not None:
        document["step"] = step
    return document


def read_example_dataset(path: Path) -> EvaluationDataset:
    """Read canonical gold with the shared bounded case-reference loader."""
    return read_dataset(path)


def private_output_path(path: Path | None) -> Path | None:
    """Reject overwrites and tracked checkout destinations before example execution."""
    if path is None:
        return None
    if path.exists() or path.is_symlink():
        raise FileExistsError("example output already exists")
    selected = path.resolve()
    if selected.is_relative_to(ROOT) and not selected.is_relative_to(ROOT / ".foliqant"):
        raise ValueError("private example outputs inside the checkout belong under .foliqant")
    return selected


def _write_dataset(dataset: EvaluationDataset, path: Path) -> dict[str, JsonValue]:
    selected = private_output_path(path)
    assert selected is not None
    encoded = dataset.model_dump_json(indent=2) + "\n"
    selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(selected, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(encoded)
    return {
        "ok": True,
        "dataset": dataset.name,
        "revision": dataset.revision,
        "suite_count": len(dataset.suites),
    }


async def write_example_dataset(dataset: EvaluationDataset, path: Path) -> dict[str, JsonValue]:
    """Explicitly export synthetic gold off-loop without overwriting existing data."""
    return await asyncio.to_thread(_write_dataset, dataset, path)

"""Evaluation-only composition for the CLI; no imports from model training tooling."""

import asyncio
import json
import math
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.settings import PreparedApplication

from .artifact import MAX_REPORT_BYTES, write_report
from .contracts import EvaluationReport, EvaluationVariant, Pipeline
from .dataset import EvaluationDataset, SuiteSpec, load_dataset, metric_specs, read_json
from .runner import evaluate


def _replay_targets(
    path: Path, dataset: EvaluationDataset, prepared: PreparedApplication
) -> dict[str, Callable[[str], Awaitable[ExecutionResult]]]:
    """Match saved inputs/configuration, permitting revised gold but never new inputs."""
    raw = read_json(path, max_bytes=MAX_REPORT_BYTES)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("invalid report version")
    identity = raw.get("dataset")
    if not isinstance(identity, dict) or identity.get("name") != dataset.name:
        raise ValueError("report belongs to another dataset")
    reports = raw.get("reports")
    if not isinstance(reports, list) or len(reports) != len(dataset.suites):
        raise ValueError("report suite count differs")
    saved: dict[str, dict[str, object]] = {}
    for report in reports:
        if not isinstance(report, dict) or not isinstance(report.get("suite_name"), str):
            raise ValueError("invalid report")
        name = report["suite_name"]
        if name in saved or report.get("configuration_revision") != prepared.configuration_digest:
            raise ValueError("report configuration differs")
        saved[name] = report
    targets: dict[str, Callable[[str], Awaitable[ExecutionResult]]] = {}
    for spec in dataset.suites:
        report = saved.get(spec.name)
        if report is None or (
            report.get("target_step") != spec.step or report.get("target_workflow") != spec.workflow
        ):
            # The report declares isolated targets; older reports cannot silently
            # be interpreted as a different execution mode.
            raise ValueError("report target differs")
        cases = report.get("cases")
        if not isinstance(cases, list) or len(cases) != len(spec.gold_cases):
            raise ValueError("report case count differs")
        outcomes: dict[str, tuple[ExecutionResult | None, str | None]] = {}
        for gold, case in zip(spec.gold_cases, cases, strict=True):
            if not isinstance(case, dict) or case.get("id") != gold.id:
                raise ValueError("report case order or identity differs")
            details = case.get("details")
            if not isinstance(details, dict) or json.dumps(
                details.get("input"), sort_keys=True
            ) != json.dumps(gold.input.model_dump(mode="json"), sort_keys=True):
                raise ValueError("report requires identical saved inputs")
            result = details.get("result")
            error = case.get("error_code")
            if result is None:
                if case.get("status") != "error" or not isinstance(error, str):
                    raise ValueError("report is missing an execution result")
                outcomes[gold.id] = (None, error)
            else:
                parsed = ExecutionResult.model_validate(result, strict=True)
                if (
                    parsed.execution.workflow != spec.workflow
                    or parsed.execution.revision != prepared.plans[spec.workflow].revision
                ):
                    raise ValueError("saved workflow differs")
                outcomes[gold.id] = (parsed, None)

        async def invoke(
            case_id: str,
            records: dict[str, tuple[ExecutionResult | None, str | None]] = outcomes,
        ) -> ExecutionResult:
            result, error = records[case_id]
            if result is not None:
                return result
            if error == "timeout":
                raise TimeoutError
            try:
                code = ErrorCode(error or "")
            except (ValueError, TypeError):
                raise RuntimeError("saved execution failed") from None
            raise ServiceError(code)

        targets[spec.name] = invoke
    return targets


async def evaluate_configuration(
    prepared: PreparedApplication,
    *,
    check: bool = False,
    replay: Path | None = None,
    output: Path | None = None,
    max_concurrency: int = 1,
    timeout: float = 300.0,
) -> tuple[dict[str, object], int]:
    """Validate gold offline, execute configured targets, or rescore saved results.

    Execution is explicit; check and replay do not construct application clients
    or resolve secrets. CLI stdout stays content-free; full reports are private.
    """
    try:
        if not 1 <= max_concurrency <= 64 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid evaluation bounds")
        dataset = await asyncio.to_thread(load_dataset, prepared)
        targets = (
            await asyncio.to_thread(_replay_targets, replay, dataset, prepared)
            if replay is not None
            else None
        )
    except (OSError, ValueError, TypeError, RecursionError, ServiceError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None
    summary: dict[str, object] = {
        "command": "evaluate",
        "mode": "check" if check else "replay" if replay is not None else "execution",
        "suites": len(dataset.suites),
        "cases": sum(len(suite.gold_cases) for suite in dataset.suites),
    }
    if check:
        return {**summary, "status": "valid"}, 0
    destination = output or (
        prepared.source.parent
        / ".foliqant/evaluations"
        / f"report-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
    )
    if destination.exists() or destination.is_symlink():
        raise ServiceError(ErrorCode.CONFLICT)
    reports: list[EvaluationReport] = []

    async def measure(spec: SuiteSpec, run: Pipeline) -> None:
        reports.append(
            await evaluate(
                dataset.to_suite(spec),
                EvaluationVariant(
                    name="configured",
                    revision=prepared.configuration_digest,
                    run=run,
                    configuration_revision=prepared.configuration_digest,
                    step=spec.step,
                    workflow=spec.workflow,
                ),
                max_concurrency=max_concurrency,
                timeout=timeout,
                metrics=metric_specs(spec),
                include_details=True,
            )
        )

    if targets is not None:
        # Evaluator schedules cases in suite order. Select the saved case before
        # the first await; duplicate input values still refer to distinct cases.
        for spec in dataset.suites:
            identifiers = iter(case.id for case in spec.gold_cases)

            async def replay_case(
                envelope: Envelope,
                target: Callable[[str], Awaitable[ExecutionResult]] = targets[spec.name],
                ids: Iterator[str] = identifiers,
            ) -> ExecutionResult:
                del envelope
                return await target(next(ids))

            await measure(spec, replay_case)
    else:
        from foliqant.bootstrap import open_application

        async with open_application(prepared, environment=os.environ) as application:
            for spec in dataset.suites:

                async def invoke(envelope: Envelope, selected: SuiteSpec = spec) -> ExecutionResult:
                    if selected.step is None:
                        return await application.run(selected.workflow, envelope)
                    return await application.run_step(selected.workflow, selected.step, envelope)

                await measure(spec, invoke)
    await asyncio.to_thread(
        write_report,
        destination,
        reports,
        mode=cast(str, summary["mode"]),
        dataset_name=dataset.name,
        dataset_revision=dataset.revision,
    )
    passed = all(report.checks.passed == report.checks.total for report in reports)
    # Case IDs, metric labels, expected/actual values and explanations are not logs.
    runtime_error = any(
        case.status in {"failed", "cancelled", "error"}
        for report in reports
        for case in report.cases
    )
    summary.update(
        status="error" if runtime_error else "passed" if passed else "failed",
        passed_checks=sum(report.checks.passed for report in reports),
        total_checks=sum(report.checks.total for report in reports),
        report=str(destination.absolute()),
    )
    return summary, 4 if runtime_error else 0 if passed else 1

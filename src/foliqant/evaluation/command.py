"""Evaluation-only composition for the CLI; no imports from model training tooling."""

import asyncio
import json
import math
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from foliqant.contracts.envelope import Envelope
from foliqant.contracts.execution import ExecutionResult
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.settings import PreparedApplication

from .analysis import compare_reports
from .artifact import (
    MAX_REPORT_BYTES,
    default_artifact_path,
    report_document,
    write_private_json,
    write_report,
)
from .contracts import EvaluationReport, EvaluationVariant, Pipeline
from .dataset import EvaluationDataset, SuiteSpec, load_dataset, metric_specs, read_json
from .runner import evaluate
from .summaries import summarize_latency


@dataclass(frozen=True, slots=True)
class _SavedOutcome:
    id: str
    repetition: int
    result: ExecutionResult | None
    error: str | None
    elapsed_seconds: float


def compare_report_files(
    candidate: Path, baseline: Path, *, output: Path | None = None
) -> tuple[dict[str, object], int]:
    """Compare two full artifacts offline and persist the detailed comparison."""
    destination = output or default_artifact_path(
        Path.cwd() / ".foliqant/evaluations", prefix="comparison"
    )
    if destination.exists() or destination.is_symlink():
        raise ServiceError(ErrorCode.CONFLICT)
    try:
        comparison = compare_reports(candidate, baseline)
    except (OSError, ValueError, TypeError, RecursionError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None
    try:
        write_private_json(destination, comparison.to_dict())
    except FileExistsError:
        raise ServiceError(ErrorCode.CONFLICT) from None
    changes = comparison.document["case_changes"]
    assert isinstance(changes, dict)
    return {
        "command": "evaluate",
        "mode": "compare",
        "status": "compared",
        "suites": comparison.document["suite_count"],
        "attempts": comparison.document["attempt_count"],
        "improved": changes["improved"],
        "regressed": changes["regressed"],
        "mixed": changes["mixed"],
        "unchanged": changes["unchanged"],
        "report": str(destination.absolute()),
    }, 0


def _replay_targets(
    path: Path,
    dataset: EvaluationDataset,
    prepared: PreparedApplication,
    requested_repeat: int | None,
) -> tuple[dict[str, tuple[_SavedOutcome, ...]], int]:
    """Match saved inputs/configuration, permitting revised gold but never new inputs."""
    raw = report_document(read_json(path, max_bytes=MAX_REPORT_BYTES))
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
    targets: dict[str, tuple[_SavedOutcome, ...]] = {}
    artifact_repeat: int | None = None
    for spec in dataset.suites:
        report = saved.get(spec.name)
        if report is None or (
            report.get("target_step") != spec.step
            or report.get("target_flow") != spec.flow
            or report.get("target_workflow") != spec.workflow
        ):
            # Rescoring must preserve the exact execution scope.
            raise ValueError("report target differs")
        cases = report.get("cases")
        repeat = report.get("repeat")
        if type(repeat) is not int or repeat < 1:
            raise ValueError("report repeat is invalid")
        if artifact_repeat is None:
            artifact_repeat = repeat
        elif artifact_repeat != repeat:
            raise ValueError("report suites use different repeat counts")
        if requested_repeat is not None and requested_repeat != repeat:
            raise ValueError("report repeat differs")
        if not isinstance(cases, list) or len(cases) != len(spec.gold_cases) * repeat:
            raise ValueError("report case count differs")
        outcomes: list[_SavedOutcome] = []
        expected = (
            (gold, repetition) for gold in spec.gold_cases for repetition in range(1, repeat + 1)
        )
        for (gold, repetition), case in zip(expected, cases, strict=True):
            if (
                not isinstance(case, dict)
                or case.get("id") != gold.id
                or case.get("repetition") != repetition
            ):
                raise ValueError("report case order or identity differs")
            elapsed_value = case.get("elapsed_seconds")
            if (
                type(elapsed_value) not in (int, float)
                or not math.isfinite(cast(float, elapsed_value))
                or cast(float, elapsed_value) < 0
            ):
                raise ValueError("report case timing is invalid")
            elapsed = float(cast(int | float, elapsed_value))
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
                outcomes.append(_SavedOutcome(gold.id, repetition, None, error, elapsed))
            else:
                parsed = ExecutionResult.model_validate(result, strict=True)
                if (
                    parsed.execution.workflow != spec.workflow
                    or parsed.execution.revision != prepared.plans[spec.workflow].revision
                ):
                    raise ValueError("saved workflow differs")
                outcomes.append(_SavedOutcome(gold.id, repetition, parsed, None, elapsed))

        targets[spec.name] = tuple(outcomes)
    assert artifact_repeat is not None
    return targets, artifact_repeat


async def evaluate_configuration(
    prepared: PreparedApplication,
    *,
    check: bool = False,
    replay: Path | None = None,
    output: Path | None = None,
    max_concurrency: int = 1,
    timeout: float = 300.0,
    repeat: int | None = None,
) -> tuple[dict[str, object], int]:
    """Validate gold offline, execute configured targets, or rescore saved results.

    Execution is explicit; check and replay do not construct application clients
    or resolve secrets. CLI stdout stays content-free; full reports are private.
    """
    try:
        if (
            type(max_concurrency) is not int
            or not 1 <= max_concurrency <= 64
            or type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or (repeat is not None and (type(repeat) is not int or repeat < 1))
        ):
            raise ValueError("invalid evaluation bounds")
        dataset = await asyncio.to_thread(load_dataset, prepared)
        if replay is not None:
            targets, selected_repeat = await asyncio.to_thread(
                _replay_targets, replay, dataset, prepared, repeat
            )
        else:
            targets, selected_repeat = None, repeat or 1
    except (OSError, ValueError, TypeError, RecursionError, ServiceError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None
    summary: dict[str, object] = {
        "command": "evaluate",
        "mode": "check" if check else "replay" if replay is not None else "execution",
        "suites": len(dataset.suites),
        "cases": sum(len(suite.gold_cases) for suite in dataset.suites),
        "repeat": selected_repeat,
        "attempts": sum(len(suite.gold_cases) for suite in dataset.suites) * selected_repeat,
    }
    if check:
        return {**summary, "status": "valid"}, 0
    destination = output or default_artifact_path(prepared.source.parent / ".foliqant/evaluations")
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
                    flow=spec.flow,
                    workflow=spec.workflow,
                ),
                max_concurrency=max_concurrency,
                timeout=timeout,
                metrics=metric_specs(spec),
                include_details=True,
                repeat=selected_repeat,
            )
        )

    if targets is not None:
        # Evaluator schedules cases in suite order. Select the saved case before
        # the first await; duplicate input values still refer to distinct cases.
        for spec in dataset.suites:
            records = targets[spec.name]
            observations = iter(records)

            async def replay_case(
                envelope: Envelope,
                saved: Iterator[_SavedOutcome] = observations,
            ) -> ExecutionResult:
                del envelope
                record = next(saved)
                if record.result is not None:
                    return record.result
                if record.error == "timeout":
                    raise TimeoutError
                try:
                    code = ErrorCode(record.error or "")
                except (ValueError, TypeError):
                    raise RuntimeError("saved execution failed") from None
                raise ServiceError(code)

            await measure(spec, replay_case)
            rescored = reports[-1]
            if len(rescored.cases) != len(records):
                raise ServiceError(ErrorCode.INVALID_INPUT)
            source_cases = tuple(
                replace(case, elapsed_seconds=record.elapsed_seconds)
                for case, record in zip(rescored.cases, records, strict=True)
            )
            reports[-1] = replace(
                rescored,
                cases=source_cases,
                latency=summarize_latency(case.elapsed_seconds for case in source_cases),
            )
    else:
        from foliqant.bootstrap import open_application

        async with open_application(prepared, environment=os.environ) as application:
            for spec in dataset.suites:

                async def invoke(envelope: Envelope, selected: SuiteSpec = spec) -> ExecutionResult:
                    if selected.flow is None:
                        return await application.run(selected.workflow, envelope)
                    if selected.step is None:
                        return await application.run_flow(
                            selected.workflow, selected.flow, envelope
                        )
                    return await application.run_step(
                        selected.workflow, selected.flow, selected.step, envelope
                    )

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

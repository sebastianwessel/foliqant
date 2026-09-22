"""Typed execution-record traversal, never inferred from business result shapes."""

from collections.abc import Mapping
from dataclasses import dataclass

from foliqant.contracts.execution import (
    ExecutionResult,
    FlowCollectionResult,
    FlowResult,
    StepResult,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.json import MAX_EXECUTION_JSON_DEPTH, MAX_JSON_DEPTH, FrozenJson, freeze_json
from foliqant.core.plan import MAX_COLLECTION_DEPTH


@dataclass(frozen=True, slots=True)
class FlowObservation:
    name: str
    path: str
    record: FlowResult


@dataclass(frozen=True, slots=True)
class StepObservation:
    flow: str
    name: str
    path: str
    record: StepResult


def _token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def execution_records(
    result: ExecutionResult,
) -> tuple[tuple[FlowObservation, ...], tuple[StepObservation, ...]]:
    """Index every actual invocation in authored order, including skipped records.

    A marked collection owns child records exactly once. Root/flow projections
    and unmarked business values are not additional execution observations.
    """
    flows: list[FlowObservation] = []
    steps: list[StepObservation] = []

    def visit(name: str, flow: FlowResult, path: str) -> None:
        flows.append(FlowObservation(name, path, flow))
        for step_name, step in flow.steps.items():
            step_path = f"{path}/steps/{_token(step_name)}"
            steps.append(StepObservation(name, step_name, step_path, step))
            if step.kind != "flow_collection":
                continue
            field = "partial_result" if step.partial_result is not None else "result"
            if field == "partial_result":
                ledger = step.partial_result
            elif "result" in step.model_fields_set:
                ledger = FlowCollectionResult.model_validate(step.result, strict=True)
            else:
                continue
            assert ledger is not None
            for index, child in enumerate(ledger.items):
                visit(child.flow, child, f"{step_path}/{field}/items/{index}")

    for name, flow in result.flows.items():
        visit(name, flow, f"/flows/{_token(name)}")
    return tuple(flows), tuple(steps)


def path_observation(
    path: str,
    flows: tuple[FlowObservation, ...],
    steps: tuple[StepObservation, ...],
) -> FlowObservation | StepObservation | None:
    """Return the innermost real record owning a pointer, on token boundaries."""
    records: tuple[FlowObservation | StepObservation, ...] = (*flows, *steps)
    matches = [
        record for record in records if path == record.path or path.startswith(record.path + "/")
    ]
    return max(matches, key=lambda record: len(record.path), default=None)


def document_path_status(path: str, document: FrozenJson) -> str | None:
    """Find scoped status in validated frozen JSON using explicit collection markers."""
    parts = [token.replace("~1", "/").replace("~0", "~") for token in path.split("/")[1:]]
    if not isinstance(document, Mapping) or len(parts) < 2 or parts[0] != "flows":
        return None
    roots = document.get("flows")
    flow = roots.get(parts[1]) if isinstance(roots, Mapping) else None
    offset = 2
    status = None
    while isinstance(flow, Mapping):
        status = flow.get("status")
        if len(parts) < offset + 2 or parts[offset] != "steps":
            break
        steps = flow.get("steps")
        step = steps.get(parts[offset + 1]) if isinstance(steps, Mapping) else None
        if not isinstance(step, Mapping):
            break
        status = step.get("status")
        offset += 2
        if (
            step.get("kind") != "flow_collection"
            or len(parts) < offset + 3
            or parts[offset] not in {"result", "partial_result"}
            or parts[offset + 1] != "items"
        ):
            break
        ledger = step.get(parts[offset])
        items = ledger.get("items") if isinstance(ledger, Mapping) else None
        token = parts[offset + 2]
        if (
            not isinstance(items, tuple)
            or not token.isascii()
            or not token.isdigit()
            or len(token) > 4
            or (len(token) > 1 and token[0] == "0")
            or int(token) >= len(items)
        ):
            break
        flow = items[int(token)]
        offset += 3
    return status if isinstance(status, str) else None


def snapshot_execution(
    result: ExecutionResult,
) -> tuple[FrozenJson, tuple[FlowObservation, ...], tuple[StepObservation, ...]]:
    """Bound business leaves independently from generated invocation containers.

    Each child adds exactly five pointer segments beneath its owning flow.
    Projections can contain that same generated ledger, so their bound includes
    only the structural allowance established by marked records in this result.
    Unmarked operation results retain the ordinary business-value depth bound.
    """
    flows, steps = execution_records(result)
    depths = {flow.path: (len(flow.path.split("/")) - 3) // 5 for flow in flows}
    maximum = max(depths.values(), default=0)
    if maximum > MAX_COLLECTION_DEPTH:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    freeze_json(result.payload, max_depth=MAX_JSON_DEPTH + 5 * maximum)
    for flow in flows:
        child_depth = (
            max(
                (
                    depth
                    for path, depth in depths.items()
                    if path == flow.path or path.startswith(flow.path + "/")
                ),
                default=depths[flow.path],
            )
            - depths[flow.path]
        )
        if "result" in flow.record.model_fields_set:
            freeze_json(flow.record.result, max_depth=MAX_JSON_DEPTH + 5 * child_depth)
    for step in steps:
        if step.record.kind != "flow_collection" and "result" in step.record.model_fields_set:
            freeze_json(step.record.result)
    document = freeze_json(
        result.model_dump(mode="json"),
        max_depth=min(MAX_EXECUTION_JSON_DEPTH, MAX_JSON_DEPTH + 5 + 5 * maximum),
    )
    return document, flows, steps

"""Resolve scoped suite inputs from workflow cases with the compiled boundary bindings."""

from collections.abc import Mapping

from foliqant.contracts.envelope import Envelope
from foliqant.core.bindings import resolve_bindings
from foliqant.core.json import JsonValue, freeze_json, thaw_json
from foliqant.settings import PreparedApplication


def flow_input(
    prepared: PreparedApplication,
    workflow: str,
    flow: str,
    envelope: Envelope,
    *,
    flow_results: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """The input the workflow binds for ``flow`` from this workflow envelope.

    ``flow_results`` supplies authored results of upstream flows (for example the
    reviewed classification a downstream extraction flow consumes), recorded as
    completed flows; a binding to an upstream flow that is not supplied uses its
    authored default or fails. Nothing executes and no client is opened, so a flow
    suite can isolate one flow on exactly the input the workflow would pass it.
    """
    plan = prepared.plans.get(workflow)
    if plan is None:
        raise ValueError("unknown evaluation workflow")
    selected = next((item for item in plan.flows if item.name == flow), None)
    if selected is None:
        raise ValueError("unknown evaluation flow")
    context = freeze_json(
        {
            "payload": envelope.payload,
            "metadata": envelope.metadata.model_dump(mode="json"),
            "flows": {
                name: {"status": "completed", "result": value}
                for name, value in (flow_results or {}).items()
            },
        }
    )
    resolved = thaw_json(resolve_bindings(selected.input, context))
    assert isinstance(resolved, dict)
    return resolved

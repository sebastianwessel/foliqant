"""Example-owned planning and disposition. These are not universal business rules."""

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.contracts.decisions import RequestUnitsResult
from foliqant.contracts.execution import FlowCollectionItem, FlowCollectionResult
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, JsonValue, freeze_json, thaw_json
from foliqant.ports.execution import StepContext


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanningInput(ClosedModel):
    assessment: RequestUnitsResult
    language: Literal["en", "de"]


class HeldRequest(ClosedModel):
    id: str
    reason: Literal[
        "incomplete_assessment",
        "related_requests",
        "conditional_request",
        "unsupported_request",
        "missing_reference",
        "too_many_requests",
    ]


class IgnoredRequest(ClosedModel):
    id: str
    reason: Literal["withdrawn", "quoted", "duplicate"]


class RequestPlan(ClosedModel):
    items: list[FlowCollectionItem]
    request_ids: dict[str, str]
    held: list[HeldRequest]
    ignored: list[IgnoredRequest]
    assessment: RequestUnitsResult


class DispositionInput(ClosedModel):
    plan: RequestPlan
    collection: FlowCollectionResult


class Disposition(ClosedModel):
    disposition: Literal["ready", "partial_review", "review", "no_action"]
    prepared: list[str]
    review: list[str]
    ignored: list[IgnoredRequest]


class GuidanceInput(ClosedModel):
    language: Literal["en", "de"]


class Guidance(ClosedModel):
    language: Literal["en", "de"]
    next_step: str


def plan_requests(value: PlanningInput) -> RequestPlan:
    """Plan independent reads only; uncertainty is never promoted into permission.

    This public-office example requires a fully answerable assessment. Related or
    conditional work needs human coordination. Another use case can choose a
    different explicit policy without changing the framework.
    """
    assessment = value.assessment
    plan = RequestPlan(items=[], request_ids={}, held=[], ignored=[], assessment=assessment)
    if assessment.answerability.status != "answerable" or assessment.answer is None:
        plan.held.append(HeldRequest(id="assessment", reason="incomplete_assessment"))
        return plan
    if assessment.answer.relations:
        plan.held.append(HeldRequest(id="assessment", reason="related_requests"))
        return plan
    seen: set[tuple[str, str | None]] = set()
    for unit in assessment.answer.units:
        if unit.status in {"withdrawn", "quoted"}:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason=unit.status))
            continue
        if unit.status == "conditional":
            plan.held.append(HeldRequest(id=unit.id, reason="conditional_request"))
            continue
        if unit.categoryId not in {"request_status", "guidance"}:
            plan.held.append(HeldRequest(id=unit.id, reason="unsupported_request"))
            continue
        if unit.categoryId == "request_status" and (
            unit.subject is None or re.fullmatch(r"FOI-[0-9]{4}-[0-9]{4}", unit.subject) is None
        ):
            plan.held.append(HeldRequest(id=unit.id, reason="missing_reference"))
            continue
        key = (unit.categoryId, unit.subject if unit.categoryId == "request_status" else None)
        if key in seen:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason="duplicate"))
            continue
        seen.add(key)
        if len(plan.items) >= 8:
            plan.held.append(HeldRequest(id=unit.id, reason="too_many_requests"))
            continue
        inputs: dict[str, JsonValue] = {"language": value.language}
        flow = "prepare_guidance"
        if unit.categoryId == "request_status":
            inputs["reference"] = unit.subject
            flow = "lookup_status"
        item_id = f"task_{len(plan.items) + 1}"
        plan.items.append(FlowCollectionItem(id=item_id, flow=flow, input=inputs))
        plan.request_ids[item_id] = unit.id
    return plan


async def plan_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = plan_requests(PlanningInput.model_validate(thaw_json(inputs)))
    return StepOutcome(freeze_json(value.model_dump(mode="json")))


async def disposition_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = DispositionInput.model_validate(thaw_json(inputs))
    planned = [(item.id, item.flow) for item in value.plan.items]
    received = [(item.id, item.flow) for item in value.collection.items]
    if planned != received or set(value.plan.request_ids) != {item.id for item in value.plan.items}:
        raise ValueError("The ledger must represent the complete plan in order")
    prepared = [
        value.plan.request_ids[item.id]
        for item in value.collection.items
        if item.status == "completed"
    ]
    review = [item.id for item in value.plan.held]
    review.extend(
        value.plan.request_ids[item.id]
        for item in value.collection.items
        if item.status != "completed"
    )
    disposition: Literal["ready", "partial_review", "review", "no_action"] = (
        "partial_review"
        if review and prepared
        else "review"
        if review
        else "ready"
        if prepared
        else "no_action"
    )
    result = Disposition(
        disposition=disposition, prepared=prepared, review=review, ignored=value.plan.ignored
    )
    return StepOutcome(freeze_json(result.model_dump(mode="json")), needs_review=bool(review))


async def guidance_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = GuidanceInput.model_validate(thaw_json(inputs))
    result = Guidance(
        language=value.language,
        next_step="Describe the records you need and submit the public-record form."
        if value.language == "en"
        else "Beschreiben Sie die benötigten Unterlagen und reichen Sie das Auskunftsformular ein.",
    )
    return StepOutcome(freeze_json(result.model_dump(mode="json")))


def _schema(model: type[BaseModel]) -> FrozenObject:
    result = freeze_json(model.model_json_schema())
    assert isinstance(result, Mapping)
    return result


HANDLERS = {
    "plan_requests": HandlerRegistration(
        plan_handler, _schema(PlanningInput), _schema(RequestPlan)
    ),
    "decide_disposition": HandlerRegistration(
        disposition_handler, _schema(DispositionInput), _schema(Disposition)
    ),
    "prepare_guidance": HandlerRegistration(
        guidance_handler, _schema(GuidanceInput), _schema(Guidance)
    ),
}

"""Conservative support-specific planning and disposition for collections."""

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.contracts.decisions import RequestUnitsResult
from foliqant.contracts.execution import FlowCollectionItem, FlowCollectionResult
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.ports.execution import StepContext


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanningInput(ClosedModel):
    assessment: RequestUnitsResult


class HeldRequest(ClosedModel):
    id: str
    reason: Literal[
        "incomplete_assessment",
        "related_requests",
        "conditional_request",
        "unsupported_request",
        "missing_account_reference",
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


class AccountRecord(ClosedModel):
    account_reference: str
    plan: str
    renewal_date: str


class PreparationInput(ClosedModel):
    queue: Literal["billing", "cancellation"]
    account: AccountRecord


class PreparedTask(ClosedModel):
    queue: Literal["billing", "cancellation"]
    account_reference: str
    plan: str
    next_step: Literal["review_invoice", "review_cancellation"]


def plan_support_requests(value: PlanningInput) -> RequestPlan:
    """Plan only independent reads with explicit account references."""
    assessment = value.assessment
    plan = RequestPlan(items=[], request_ids={}, held=[], ignored=[], assessment=assessment)
    if assessment.answerability.status != "answerable" or assessment.answer is None:
        plan.held.append(HeldRequest(id="assessment", reason="incomplete_assessment"))
        return plan
    if assessment.answer.relations:
        plan.held.append(HeldRequest(id="assessment", reason="related_requests"))
        return plan
    seen: set[tuple[str, str, str]] = set()
    for unit in assessment.answer.units:
        if unit.status in {"withdrawn", "quoted"}:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason=unit.status))
            continue
        if unit.status == "conditional":
            plan.held.append(HeldRequest(id=unit.id, reason="conditional_request"))
            continue
        if unit.categoryId not in {"billing", "cancellation"}:
            plan.held.append(HeldRequest(id=unit.id, reason="unsupported_request"))
            continue
        if unit.subject is None or re.fullmatch(r"A-[0-9]+", unit.subject) is None:
            plan.held.append(HeldRequest(id=unit.id, reason="missing_account_reference"))
            continue
        # The same account can have distinct invoices or separate requests.
        key = (unit.categoryId, unit.subject, unit.description)
        if key in seen:
            plan.ignored.append(IgnoredRequest(id=unit.id, reason="duplicate"))
            continue
        seen.add(key)
        if len(plan.items) >= 8:
            plan.held.append(HeldRequest(id=unit.id, reason="too_many_requests"))
            continue
        item_id = f"task_{len(plan.items) + 1}"
        plan.items.append(
            FlowCollectionItem(
                id=item_id,
                flow="billing_task" if unit.categoryId == "billing" else "cancellation_task",
                input={"account_reference": unit.subject},
            )
        )
        plan.request_ids[item_id] = unit.id
    return plan


async def plan_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = plan_support_requests(PlanningInput.model_validate(thaw_json(inputs)))
    return StepOutcome(freeze_json(value.model_dump(mode="json")))


async def prepare_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = PreparationInput.model_validate(thaw_json(inputs))
    result = PreparedTask(
        queue=value.queue,
        account_reference=value.account.account_reference,
        plan=value.account.plan,
        next_step="review_invoice" if value.queue == "billing" else "review_cancellation",
    )
    return StepOutcome(freeze_json(result.model_dump(mode="json")))


async def disposition_handler(inputs: FrozenObject, context: StepContext) -> StepOutcome:
    del context
    value = DispositionInput.model_validate(thaw_json(inputs))
    planned = [(item.id, item.flow) for item in value.plan.items]
    received = [(item.id, item.flow) for item in value.collection.items]
    if planned != received or set(value.plan.request_ids) != {item.id for item in value.plan.items}:
        raise ValueError("The collection ledger must match the planned tasks")
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
        disposition=disposition,
        prepared=prepared,
        review=review,
        ignored=value.plan.ignored,
    )
    return StepOutcome(freeze_json(result.model_dump(mode="json")), needs_review=bool(review))


def _schema(model: type[BaseModel]) -> FrozenObject:
    result = freeze_json(model.model_json_schema())
    assert isinstance(result, Mapping)
    return result


HANDLERS = {
    "plan_support_requests": HandlerRegistration(
        plan_handler, _schema(PlanningInput), _schema(RequestPlan)
    ),
    "prepare_support_task": HandlerRegistration(
        prepare_handler, _schema(PreparationInput), _schema(PreparedTask)
    ),
    "decide_support_disposition": HandlerRegistration(
        disposition_handler, _schema(DispositionInput), _schema(Disposition)
    ),
}

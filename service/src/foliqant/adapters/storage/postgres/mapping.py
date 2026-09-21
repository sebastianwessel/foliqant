"""Lossless immutable core values at the SQL JSON boundary.

The database uses PostgreSQL json, not jsonb: payloads are opaque, and json keeps
object insertion order, numeric spelling and valid escaped strings such as NUL.
Canonical digests use the explicitly versioned CPython JSON encoding below.
"""

import json
import math
from dataclasses import asdict
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult, StepResult, to_execution_result
from foliqant.contracts.workflow import Id
from foliqant.core.envelope import AcceptedEnvelope
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import Failure, RunResult, StepRecord, TokenUsage, Usage
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json, thaw_json
from foliqant.core.runner import ExecutionLimits
from foliqant.core.storage import Checkpoint, DeliveryRequest, StoredExecution, Submission

# Database rows are private, driver-decoded values, validated on their way back
# into core/public contracts. Any is confined to this persistence boundary.
type Row = dict[str, Any]
_ID: TypeAdapter[str] = TypeAdapter(Id)


def identifier(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise ServiceError(ErrorCode.INVALID_INPUT)
    try:
        return _ID.validate_python(value, strict=True)
    except ValidationError:
        raise ServiceError(ErrorCode.INVALID_INPUT) from None


def opaque(value: object, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ServiceError(ErrorCode.INVALID_INPUT)
    return value


def uuid_value(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None


def duration(value: object, *, minimum: float, maximum: float) -> float:
    if (
        type(value) not in (float, int)
        or not math.isfinite(cast(float, value))
        or not minimum <= cast(float, value) <= maximum
    ):
        raise ServiceError(ErrorCode.INVALID_INPUT)
    return float(cast(float, value))


def normalize(submission: Submission, *, allow_anonymous: bool) -> Submission:
    if not isinstance(submission, Submission) or not isinstance(submission.identity, Identity):
        raise ServiceError(ErrorCode.INVALID_INPUT)
    identity = submission.identity
    if not allow_anonymous and identity == Identity():
        raise ServiceError(ErrorCode.FORBIDDEN)
    try:
        envelope = accept_envelope(
            Envelope.model_validate(
                {
                    "payload": thaw_json(submission.envelope.payload),
                    "metadata": thaw_json(submission.envelope.metadata),
                },
                strict=True,
            ),
            identity,
        )
        limits = ExecutionLimits(**asdict(submission.limits))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError(ErrorCode.INVALID_INPUT) from None
    delivery = submission.delivery
    if delivery is not None:
        if not isinstance(delivery, DeliveryRequest):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        opaque(delivery.destination)
        if type(delivery.max_attempts) is not int or not 1 <= delivery.max_attempts <= 32:
            raise ServiceError(ErrorCode.INVALID_INPUT)
    return Submission(
        identifier(submission.workflow),
        opaque(submission.revision, maximum=512),
        opaque(submission.idempotency_key),
        envelope,
        identity,
        identifier(submission.first_step),
        limits,
        delivery,
    )


def submission_json(value: Submission) -> Row:
    return {
        "workflow": value.workflow,
        "revision": value.revision,
        "idempotency_key": value.idempotency_key,
        "first_step": value.first_step,
        "envelope": {
            "payload": thaw_json(value.envelope.payload),
            "metadata": thaw_json(value.envelope.metadata),
        },
        "identity": asdict(value.identity),
        "limits": asdict(value.limits),
        "delivery": asdict(value.delivery) if value.delivery is not None else None,
    }


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def scope(identity: Identity) -> str:
    return sha256(canonical(asdict(identity)).encode()).hexdigest()


def digest(value: Submission) -> str:
    data = submission_json(value)
    data.pop("idempotency_key")
    return sha256(
        canonical({"domain": "foliqant.execution.input", "version": 1, **data}).encode()
    ).hexdigest()


def decode_submission(value: Row) -> Submission:
    return Submission(
        workflow=value["workflow"],
        revision=value["revision"],
        idempotency_key=value["idempotency_key"],
        envelope=AcceptedEnvelope(**value["envelope"]),
        identity=Identity(**value["identity"]),
        first_step=value["first_step"],
        limits=ExecutionLimits(**value["limits"]),
        delivery=DeliveryRequest(**value["delivery"]) if value["delivery"] is not None else None,
    )


def record_json(record: StepRecord) -> Row:
    if not isinstance(record, StepRecord) or type(record.has_result) is not bool:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    if not record.has_result and record.result is not None:
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    value: Row = {"status": record.status}
    if record.has_result:
        value["result"] = thaw_json(record.result)
    if record.error is not None:
        if (
            not isinstance(record.error, Failure)
            or not isinstance(record.error.code, ErrorCode)
            or type(record.error.retryable) is not bool
        ):
            raise ServiceError(ErrorCode.INVALID_OUTPUT)
        value["error"] = {
            "code": record.error.code.value,
            "message": record.error.message,
            "retryable": record.error.retryable,
        }
    try:
        return StepResult.model_validate(value, strict=True).model_dump(mode="json")
    except (ValueError, TypeError):
        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None


def decode_record(value: StepResult) -> StepRecord:
    return StepRecord(
        value.status,
        freeze_json(value.result),
        "result" in value.model_fields_set,
        Failure(value.error.code, value.error.retryable) if value.error is not None else None,
    )


def result_json(value: RunResult) -> Row:
    if not isinstance(value, RunResult):
        raise ServiceError(ErrorCode.INVALID_OUTPUT)
    try:
        for _, record in value.decisions:
            record_json(record)
        return to_execution_result(value).model_dump(mode="json")
    except (AttributeError, TypeError, ValueError):
        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None


def decode_result(value: Row) -> RunResult:
    public = ExecutionResult.model_validate(value, strict=True)
    usage = public.execution.usage
    return RunResult(
        public.execution.id,
        public.execution.workflow,
        public.execution.revision,
        public.execution.status,
        freeze_json(public.payload),
        cast(FrozenObject, freeze_json(public.metadata.model_dump(mode="json"))),
        tuple((key, decode_record(record)) for key, record in public.decisions.items()),
        Usage(
            usage.model_requests,
            usage.tool_calls,
            TokenUsage(
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_input_tokens,
                usage.cache_write_input_tokens,
                usage.reasoning_output_tokens,
            ),
        ),
        Failure(public.execution.error.code, public.execution.error.retryable)
        if public.execution.error is not None
        else None,
    )


def execution(row: Row, checkpoints: list[Row]) -> StoredExecution:
    return StoredExecution(
        str(row["execution_id"]),
        decode_submission(row["submission"]),
        row["accepted_at"],
        row["deadline"],
        row["status"],
        row["current_step"],
        row["cancel_requested"],
        tuple(
            Checkpoint(
                item["step_id"],
                decode_record(StepResult.model_validate(item["record"], strict=True)),
                item["next_step"],
            )
            for item in checkpoints
        ),
        decode_result(row["result"]) if row["result"] is not None else None,
    )


def aggregate(rows: list[Row]) -> Usage:
    result = Usage()
    for row in rows:
        if row["kind"] == "tool":
            result = result.plus(Usage(tool_calls=1))
        else:
            tokens = TokenUsage(**row["usage"]) if row["usage"] is not None else TokenUsage()
            result = result.plus(Usage(model_requests=1, tokens=tokens))
    return result

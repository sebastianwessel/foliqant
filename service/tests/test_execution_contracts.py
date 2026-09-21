"""Execution responses preserve JSON presence and expose only safe failures."""

from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from foliqant.contracts.envelope import Metadata
from foliqant.contracts.execution import (
    AcceptanceReceipt,
    ExecutionInfo,
    ExecutionResult,
    SafeError,
    StepResult,
    to_execution_result,
)
from foliqant.contracts.execution import (
    Usage as PublicUsage,
)
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import (
    Failure,
    RunResult,
    StepRecord,
    TokenUsage,
    Usage,
)
from foliqant.core.json import FrozenObject, freeze_json


def _error(code: str = "invalid_output") -> dict[str, object]:
    messages = {
        "invalid_output": "An operation returned an invalid result.",
        "cancelled": "The execution was cancelled.",
    }
    return {"code": code, "message": messages[code], "retryable": False}


def _usage() -> dict[str, object]:
    return {
        "model_requests": 1,
        "tool_calls": 2,
        "input_tokens": 10,
        "output_tokens": None,
        "cache_read_input_tokens": 3,
        "cache_write_input_tokens": None,
        "reasoning_output_tokens": 0,
    }


def _info(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "run-123",
        "workflow": "support_triage",
        "revision": "revision-1",
        "status": "completed",
        "usage": _usage(),
    }
    value.update(updates)
    return value


def test_safe_error_requires_known_code_canonical_message_and_no_reason() -> None:
    assert SafeError.model_validate(_error(), strict=True).model_dump(mode="json") == _error()
    for invalid in (
        {**_error(), "message": "PRIVATE provider failure"},
        {**_error(), "code": "unknown"},
        {**_error(), "code": 1},
        {**_error(), "code": True},
        {**_error(), "reason": "PRIVATE"},
        {**_error(), "location": None},
    ):
        with pytest.raises(ValidationError):
            SafeError.model_validate(invalid, strict=True)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_core_error_code_has_its_exact_public_message(code: ErrorCode) -> None:
    error = SafeError(
        code=code.value,
        message=str(ServiceError(code)),
        retryable=False,
    )
    assert error.code is code
    assert error.model_dump(mode="json")["code"] == code.value


def test_safe_error_optional_location_is_absent_or_bounded_string() -> None:
    absent = SafeError.model_validate(_error(), strict=True)
    located = SafeError.model_validate(
        {**_error(), "location": "steps/classify.yaml:3"}, strict=True
    )
    assert "location" not in absent.model_dump(mode="json")
    assert located.model_dump(mode="json")["location"] == "steps/classify.yaml:3"
    with pytest.raises(ValidationError):
        SafeError.model_validate({**_error(), "location": "x" * 513}, strict=True)


def test_usage_requires_every_measurement_and_preserves_unavailable_null() -> None:
    usage = PublicUsage.model_validate(_usage(), strict=True)
    assert usage.model_dump(mode="json") == _usage()
    for invalid in (
        {**_usage(), "model_requests": -1},
        {**_usage(), "tool_calls": True},
        {key: item for key, item in _usage().items() if key != "input_tokens"},
    ):
        with pytest.raises(ValidationError):
            PublicUsage.model_validate(invalid, strict=True)


@pytest.mark.parametrize(
    "updates",
    [
        {"input_tokens": 10, "cache_read_input_tokens": 11},
        {"input_tokens": 10, "cache_write_input_tokens": 11},
        {"output_tokens": 1, "reasoning_output_tokens": 2},
    ],
)
def test_usage_delegates_token_subset_rules_to_core(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PublicUsage.model_validate({**_usage(), **updates}, strict=True)


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"status": "completed", "result": None}, {"status": "completed", "result": None}),
        ({"status": "needs_review"}, {"status": "needs_review"}),
        (
            {"status": "needs_review", "result": {"answer": None}},
            {"status": "needs_review", "result": {"answer": None}},
        ),
        ({"status": "failed", "error": _error()}, {"status": "failed", "error": _error()}),
        ({"status": "cancelled"}, {"status": "cancelled"}),
        (
            {"status": "cancelled", "error": _error("cancelled")},
            {"status": "cancelled", "error": _error("cancelled")},
        ),
        ({"status": "skipped"}, {"status": "skipped"}),
    ],
)
def test_step_result_serializes_only_fields_that_are_present(
    value: dict[str, object], expected: dict[str, object]
) -> None:
    result = StepResult.model_validate(value, strict=True)
    assert result.model_dump(mode="json") == expected


@pytest.mark.parametrize(
    "value",
    [
        {"status": "completed"},
        {"status": "completed", "result": 1, "error": _error()},
        {"status": "needs_review", "error": _error()},
        {"status": "failed"},
        {"status": "failed", "result": None, "error": _error()},
        {"status": "failed", "error": None},
        {"status": "cancelled", "result": None},
        {"status": "skipped", "result": None},
        {"status": "skipped", "error": _error()},
    ],
)
def test_step_status_invariants_reject_wrong_presence(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        StepResult.model_validate(value, strict=True)


def test_step_json_schema_has_the_same_presence_invariants() -> None:
    validator = Draft202012Validator(StepResult.model_json_schema())
    assert validator.is_valid({"status": "completed", "result": None})
    assert validator.is_valid({"status": "needs_review"})
    assert validator.is_valid({"status": "cancelled", "error": _error("cancelled")})
    for invalid in (
        {"status": "completed"},
        {"status": "failed", "error": _error(), "result": None},
        {"status": "skipped", "error": _error()},
        {"status": "cancelled", "result": None},
    ):
        assert not validator.is_valid(invalid)


def test_step_instances_are_frozen_and_revalidated_after_unsafe_construction() -> None:
    valid = StepResult(status="completed", result=None)
    with pytest.raises(ValidationError):
        valid.status = "failed"  # type: ignore[misc]
    invalid = StepResult.model_construct(status="completed")
    with pytest.raises(ValidationError):
        StepResult.model_validate(invalid, strict=True)


def test_existing_mutated_metadata_instance_is_revalidated() -> None:
    metadata = Metadata(tenant_id="tenant")
    metadata.tenant_id = None
    raw = {
        "payload": None,
        "metadata": metadata,
        "decisions": {},
        "execution": _info(),
    }
    with pytest.raises(ValidationError):
        ExecutionResult.model_validate(raw, strict=True)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_and_cancelled_execution_require_error(status: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionInfo.model_validate(_info(status=status), strict=True)
    valid = ExecutionInfo.model_validate(
        _info(
            status=status, error=_error("cancelled" if status == "cancelled" else "invalid_output")
        ),
        strict=True,
    )
    assert valid.model_dump(mode="json")["status"] == status


@pytest.mark.parametrize("status", ["completed", "needs_review"])
def test_successful_execution_omits_error_and_rejects_explicit_null(status: str) -> None:
    result = ExecutionInfo.model_validate(_info(status=status), strict=True)
    assert "error" not in result.model_dump(mode="json")
    for error in (None, _error()):
        with pytest.raises(ValidationError):
            ExecutionInfo.model_validate(_info(status=status, error=error), strict=True)


def test_execution_info_schema_requires_only_failure_errors() -> None:
    validator = Draft202012Validator(ExecutionInfo.model_json_schema())
    assert validator.is_valid(_info())
    assert validator.is_valid(_info(status="failed", error=_error()))
    assert not validator.is_valid(_info(status="failed"))
    assert not validator.is_valid(_info(error=_error()))


@pytest.mark.parametrize(
    "metadata",
    [
        {"tenant_id": "unternehmen", "custom": "Grüße aus Köln"},
        {"principal_id": "sachbearbeiter", "custom": "Überweisung prüfen"},
        {
            "tenant_id": "unternehmen",
            "principal_id": "sachbearbeiter",
            "custom": "Gebühr zurückzahlen",
        },
    ],
)
def test_core_mapping_preserves_german_json_identity_presence_and_usage(
    metadata: dict[str, object],
) -> None:
    core = RunResult(
        execution_id="run-ä-123",
        workflow="support_triage",
        revision="revision-1",
        status="completed",
        payload=freeze_json({"nachricht": "Bitte Gebühr zurückzahlen."}),
        metadata=cast(FrozenObject, freeze_json(metadata)),
        decisions=(
            ("finish", StepRecord(status="completed", result=None, has_result=True)),
            (
                "classify",
                StepRecord(
                    status="needs_review",
                    result=freeze_json({"erklärung": "Beleg fehlt", "answer": None}),
                    has_result=True,
                ),
            ),
        ),
        usage=Usage(
            model_requests=1,
            tool_calls=2,
            tokens=TokenUsage(10, None, 3, None, 0),
        ),
    )
    mapped = to_execution_result(core)
    dumped = mapped.model_dump(mode="json")

    assert dumped["payload"] == {"nachricht": "Bitte Gebühr zurückzahlen."}
    assert dumped["metadata"] == metadata
    assert dumped["decisions"]["finish"] == {"status": "completed", "result": None}
    assert dumped["decisions"]["classify"]["result"] == {
        "erklärung": "Beleg fehlt",
        "answer": None,
    }
    assert dumped["execution"]["usage"] == _usage()
    assert "error" not in dumped["execution"]

    cast(dict[str, Any], mapped.payload)["nachricht"] = "verändert"
    assert core.payload["nachricht"] == "Bitte Gebühr zurückzahlen."  # type: ignore[index]


def test_failed_mapping_uses_only_canonical_core_error() -> None:
    core = RunResult(
        execution_id="run-123",
        workflow="support_triage",
        revision="revision-1",
        status="failed",
        payload=freeze_json(None),
        metadata=cast(FrozenObject, freeze_json({})),
        decisions=(
            (
                "classify",
                StepRecord(
                    status="failed",
                    error=Failure(ErrorCode.DEPENDENCY_FAILURE, retryable=True),
                ),
            ),
        ),
        usage=Usage(tokens=TokenUsage.zero()),
        error=Failure(ErrorCode.DEPENDENCY_FAILURE, retryable=True),
    )
    dumped = to_execution_result(core).model_dump(mode="json")
    expected = {
        "code": "dependency_failure",
        "message": "A required dependency is unavailable.",
        "retryable": True,
    }
    assert dumped["decisions"]["classify"]["error"] == expected
    assert dumped["execution"]["error"] == expected


def test_invalid_or_duplicate_core_records_fail_with_safe_invalid_output() -> None:
    invalid = StepRecord(status="completed", result=freeze_json("hidden"), has_result=False)
    valid = StepRecord(status="completed", result=None, has_result=True)
    for decisions in ((("one", invalid),), (("one", valid), ("one", valid))):
        core = RunResult(
            execution_id="run-123",
            workflow="support_triage",
            revision="revision-1",
            status="completed",
            payload=freeze_json(None),
            metadata=cast(FrozenObject, freeze_json({})),
            decisions=decisions,
            usage=Usage(tokens=TokenUsage.zero()),
        )
        with pytest.raises(ServiceError) as error:
            to_execution_result(core)
        assert error.value.code == ErrorCode.INVALID_OUTPUT
        assert str(error.value) == "An operation returned an invalid result."
        assert "hidden" not in str(error.value)
        assert error.value.__suppress_context__


def test_malformed_core_token_subsets_fail_with_safe_invalid_output() -> None:
    tokens = TokenUsage(10, 5, 2, 1, 1)
    object.__setattr__(tokens, "cache_read_input_tokens", 11)
    core = RunResult(
        execution_id="run-123",
        workflow="support_triage",
        revision="revision-1",
        status="completed",
        payload=freeze_json(None),
        metadata=cast(FrozenObject, freeze_json({})),
        decisions=(),
        usage=Usage(tokens=tokens),
    )
    with pytest.raises(ServiceError) as error:
        to_execution_result(core)
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert str(error.value) == "An operation returned an invalid result."
    assert error.value.__suppress_context__


def test_malformed_core_error_code_fails_without_exposing_its_value() -> None:
    failure = Failure(ErrorCode.INVALID_OUTPUT)
    object.__setattr__(failure, "code", "PRIVATE_UNKNOWN_CODE")
    core = RunResult(
        execution_id="run-123",
        workflow="support_triage",
        revision="revision-1",
        status="failed",
        payload=freeze_json(None),
        metadata=cast(FrozenObject, freeze_json({})),
        decisions=(),
        usage=Usage(tokens=TokenUsage.zero()),
        error=failure,
    )
    with pytest.raises(ServiceError) as error:
        to_execution_result(core)
    assert error.value.code == ErrorCode.INVALID_OUTPUT
    assert "PRIVATE_UNKNOWN_CODE" not in str(error.value)
    assert error.value.__suppress_context__


def test_acceptance_receipt_is_closed_and_literal() -> None:
    assert AcceptanceReceipt(execution_id="run-123", status="accepted").model_dump(mode="json") == {
        "execution_id": "run-123",
        "status": "accepted",
    }
    for invalid in (
        {"execution_id": "", "status": "accepted"},
        {"execution_id": "run-123", "status": "completed"},
        {"execution_id": "run-123", "status": "accepted", "result": None},
    ):
        with pytest.raises(ValidationError):
            AcceptanceReceipt.model_validate(invalid, strict=True)

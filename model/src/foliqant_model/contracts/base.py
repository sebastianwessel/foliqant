"""Shared scalar and record contracts for the model lifecycle."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema


class ContractModel(BaseModel):
    """Base for every closed strict contract object."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        populate_by_name=False,
        serialize_by_alias=True,
    )


def canonical_digest(value: object) -> str:
    """Return SHA-256 for the contract canonical JSON representation."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _non_empty(value: str) -> str:
    if not value:
        raise ValueError("must be nonempty")
    return value


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("must be finite")
    return value


def _timestamp(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise ValueError("must be an RFC 3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("must be a valid RFC 3339 timestamp") from error
    return value


def _safe_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("must be a nonempty POSIX relative path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("must be relative and have no drive prefix")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise ValueError("must not be a URI")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("must contain only safe path segments")
    return value


def _local_path(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("must be a nonempty local path")
    return value


def _json_pointer(value: str) -> str:
    if not re.fullmatch(r"(?:/(?:[^~]|~[01])*)*", value):
        raise ValueError("must be an RFC 6901 JSON Pointer")
    return value


def _sorted_unique[T: str](values: list[T], *, label: str) -> list[T]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must contain unique values")
    if values != sorted(values):
        raise ValueError(f"{label} must be sorted")
    return values


def _strict_true(value: object) -> object:
    if type(value) is not bool or value is not True:
        raise ValueError("must be the boolean true")
    return value


def _strict_one(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("must be the integer 1")
    return value


def _strict_false(value: object) -> object:
    if type(value) is not bool or value is not False:
        raise ValueError("must be the boolean false")
    return value


def _strict_zero(value: object) -> object:
    if type(value) is not int or value != 0:
        raise ValueError("must be the integer 0")
    return value


def _reject_explicit_null(value: object, *field_names: str) -> object:
    if isinstance(value, dict):
        for field_name in field_names:
            if field_name in value and value[field_name] is None:
                raise ValueError(f"{field_name} must be omitted rather than null")
    return value


NonEmptyStr = Annotated[
    str, StringConstraints(strict=True, min_length=1), AfterValidator(_non_empty)
]
Id = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        min_length=1,
        max_length=128,
    ),
]
RunId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$",
        min_length=32,
        max_length=32,
    ),
]
Digest = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
Commit = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$", min_length=40, max_length=40)
]
Timestamp = Annotated[str, StringConstraints(strict=True), AfterValidator(_timestamp)]
LocalPath = Annotated[str, StringConstraints(strict=True), AfterValidator(_local_path)]
SafePath = Annotated[str, StringConstraints(strict=True), AfterValidator(_safe_path)]
JsonPointer = Annotated[str, StringConstraints(strict=True), AfterValidator(_json_pointer)]
LanguageTag = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$"),
]
RepoId = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
UInt32 = Annotated[StrictInt, Field(ge=0, le=4_294_967_295)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
FiniteFloat = Annotated[StrictFloat, Field(allow_inf_nan=False), AfterValidator(_finite)]
NonNegativeFloat = Annotated[FiniteFloat, Field(ge=0)]
UnitFloat = Annotated[FiniteFloat, Field(ge=0, le=1)]
PositiveUnitFloat = Annotated[FiniteFloat, Field(gt=0, le=1)]
StrictTrue = Annotated[Literal[True], BeforeValidator(_strict_true)]
StrictFalse = Annotated[Literal[False], BeforeValidator(_strict_false)]
SchemaVersion = Annotated[Literal[1], BeforeValidator(_strict_one)]
StrictOne = Annotated[Literal[1], BeforeValidator(_strict_one)]
StrictZero = Annotated[Literal[0], BeforeValidator(_strict_zero)]

type JsonValue = (
    None | StrictBool | StrictInt | FiniteFloat | str | list[JsonValue] | dict[str, JsonValue]
)
type Omitted[T] = T | SkipJsonSchema[None]

type Command = Literal[
    "doctor",
    "schema",
    "setup",
    "fetch",
    "prepare",
    "prepare-source-projections",
    "curate",
    "quantize",
    "train",
    "customize",
    "evaluate",
    "calibrate",
    "audit",
    "merge",
    "export",
    "verify",
]
type ArtifactKind = Literal[
    "dataset",
    "checkpoint",
    "quantized",
    "adapter",
    "merged",
    "export",
    "evaluation",
    "policy",
    "audit",
]
type Stage = Literal["upstream", "shared", "customer", "none"]
type Split = Literal["train", "validation", "calibration", "test"]
type CliErrorCode = Literal[
    "ARGUMENT_INVALID",
    "CONFIG_INVALID",
    "CONFIG_TOO_LARGE",
    "CONFIG_DUPLICATE_KEY",
    "CONFIG_UNSUPPORTED_VERSION",
    "SCHEMA_DRIFT",
    "INPUT_NOT_FOUND",
    "OUTPUT_EXISTS",
    "DATA_RECORD_INVALID",
    "DATA_RIGHTS_DENIED",
    "DATA_DUPLICATE_ID",
    "DATA_DUPLICATE_CONVERSATION",
    "DATA_PARTITION_INVALID",
    "RISK_REPRESENTATIVE_MISSING",
    "RISK_SCORE_MISSING",
    "DEPENDENCY_MISSING",
    "ENVIRONMENT_UNSUPPORTED",
    "ARCHITECTURE_UNSUPPORTED",
    "BACKEND_FAILED",
    "IO_FAILED",
    "NETWORK_FAILED",
    "TIMEOUT",
    "OUTPUT_INVALID",
    "INTERNAL_ERROR",
    "INTEGRITY_FAILED",
    "UNSAFE_ARTIFACT_PATH",
    "LINEAGE_MISMATCH",
    "LEAKAGE_DETECTED",
    "PROFILE_MISMATCH",
    "SAMPLE_OVERLAP",
    "INTERRUPTED",
]


class FileEntry(ContractModel):
    path: SafePath
    size: NonNegativeInt
    sha256: Digest


class InventoryFileRef(ContractModel):
    path: SafePath
    sha256: Digest
    recordCount: NonNegativeInt
    format: Literal["json", "jsonl"]


class ConfigIdentity(ContractModel):
    kind: Literal["dataset", "train", "evaluation"]
    sha256: Digest


class CountRate(ContractModel):
    eligibleCount: NonNegativeInt
    passedCount: NonNegativeInt
    rate: UnitFloat | None

    @model_validator(mode="after")
    def validate_ratio(self) -> CountRate:
        if self.passedCount > self.eligibleCount:
            raise ValueError("passedCount must not exceed eligibleCount")
        if self.eligibleCount == 0:
            if self.rate is not None:
                raise ValueError("rate must be null for a zero denominator")
        else:
            expected = self.passedCount / self.eligibleCount
            if self.rate is None or self.rate != expected:
                raise ValueError("rate must equal passedCount / eligibleCount")
        return self


class VersionedComponent(ContractModel):
    name: NonEmptyStr
    version: NonEmptyStr
    role: Literal["backend", "converter", "inference-runtime", "library"]


class ProducerIdentity(ContractModel):
    name: Literal["foliqant-model"]
    version: NonEmptyStr
    command: Command
    pythonVersion: NonEmptyStr
    platform: NonEmptyStr
    machine: NonEmptyStr
    codeRevision: Commit | None
    codeDirty: StrictBool | None
    codeIdentityUnavailableReason: Omitted[NonEmptyStr] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    components: list[VersionedComponent]

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "codeIdentityUnavailableReason")

    @model_validator(mode="after")
    def validate_code_identity(self) -> ProducerIdentity:
        unavailable = self.codeRevision is None and self.codeDirty is None
        reason_missing = self.codeIdentityUnavailableReason is None
        if unavailable == reason_missing:
            raise ValueError(
                "code identity reason must appear exactly when identity is unavailable"
            )
        if (self.codeRevision is None) != (self.codeDirty is None):
            raise ValueError("codeRevision and codeDirty must be jointly available")
        pairs = [(item.role, item.name) for item in self.components]
        if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
            raise ValueError("components must be sorted and unique by role/name")
        return self


class SourceRight(ContractModel):
    sourceId: Id
    license: NonEmptyStr
    licenseEvidence: NonEmptyStr
    trainingAllowed: StrictTrue
    sharedTrainingAllowed: StrictBool
    redistributionAllowed: StrictBool
    privacy: Literal["public", "private"]
    authorizationRef: Omitted[NonEmptyStr] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    attribution: str
    commercialUse: Literal["allowed", "restricted", "unknown"]
    restrictions: list[NonEmptyStr]

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "authorizationRef")

    @model_validator(mode="after")
    def validate_authorization(self) -> SourceRight:
        missing = self.authorizationRef is None
        if self.privacy == "private" and missing:
            raise ValueError("private sources require authorizationRef")
        if self.privacy == "public" and not missing:
            raise ValueError("public sources must omit authorizationRef")
        _sorted_unique(self.restrictions, label="restrictions")
        return self


class PrecisionChange(ContractModel):
    operation: Literal["quantize", "dequantize-for-fusion", "fuse", "convert"]
    fromPrecision: NonEmptyStr
    toPrecision: NonEmptyStr
    lossy: StrictBool


class CompatibilityEvidence(ContractModel):
    runtime: NonEmptyStr
    runtimeVersion: NonEmptyStr
    checkedAt: Timestamp
    inputArtifactId: Digest
    commandSha256: Digest
    resultSha256: Digest


class CompatibilityRecord(ContractModel):
    target: NonEmptyStr
    status: Literal["unverified", "verified", "failed", "unsupported"]
    evidence: CompatibilityEvidence | None

    @model_validator(mode="after")
    def validate_evidence(self) -> CompatibilityRecord:
        if (self.status == "unverified") != (self.evidence is None):
            raise ValueError("evidence is null exactly for unverified status")
        return self


class LeakageEntry(ContractModel):
    recordId: Id
    componentId: Digest
    groupKeyHashes: Annotated[list[Digest], Field(min_length=1)]
    conversationHash: Digest
    promptHash: Digest

    @model_validator(mode="after")
    def validate_order(self) -> LeakageEntry:
        _sorted_unique(self.groupKeyHashes, label="groupKeyHashes")
        return self


class LeakageIndexRef(ContractModel):
    file: InventoryFileRef
    splits: list[Split]
    entryCount: NonNegativeInt

    @model_validator(mode="after")
    def validate_index(self) -> LeakageIndexRef:
        if self.file.format != "jsonl":
            raise ValueError("leakage indexes must be JSONL")
        if self.entryCount != self.file.recordCount:
            raise ValueError("entryCount must equal file.recordCount")
        _sorted_unique(self.splits, label="splits")
        return self


class InheritedLeakageRef(ContractModel):
    ancestorArtifactId: Digest
    file: InventoryFileRef

    @model_validator(mode="after")
    def validate_path(self) -> InheritedLeakageRef:
        expected = f"inherited-leakage/{self.ancestorArtifactId}.jsonl"
        if self.file.path != expected or self.file.format != "jsonl":
            raise ValueError("inherited leakage path or format is invalid")
        return self


class MeasuredBytes(ContractModel):
    value: NonNegativeInt | None
    unavailableReason: Omitted[NonEmptyStr] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "unavailableReason")

    @model_validator(mode="after")
    def validate_measurement(self) -> MeasuredBytes:
        if (self.value is None) == (self.unavailableReason is None):
            raise ValueError("unavailableReason is required exactly when value is null")
        return self

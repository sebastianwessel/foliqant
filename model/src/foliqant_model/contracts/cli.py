"""Machine-readable CLI success and failure contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, StrictBool, StrictInt, model_validator

from .base import (
    ArtifactKind,
    CliErrorCode,
    Command,
    ContractModel,
    CountRate,
    Digest,
    Id,
    LocalPath,
    NonEmptyStr,
    NonNegativeFloat,
    NonNegativeInt,
    SchemaVersion,
    Stage,
    StrictFalse,
    StrictTrue,
    StrictZero,
    UnitFloat,
    VersionedComponent,
)
from .details import RiskSampleSummary
from .setup import SetupResult


class CreatedArtifactResult(ContractModel):
    outputPath: LocalPath
    artifactId: Digest
    kind: ArtifactKind
    stage: Stage
    customer: Id | None

    @model_validator(mode="after")
    def validate_scope(self) -> CreatedArtifactResult:
        if (self.stage == "customer") != (self.customer is not None):
            raise ValueError("customer is non-null exactly for customer stage")
        return self


class ProjectionPreparationResult(ContractModel):
    """Offline source projection paths and candidate counts; no inference is performed."""

    command: Literal["prepare-source-projections"]
    planPath: LocalPath
    reportPath: LocalPath
    rawRecords: NonNegativeInt
    eligibleRecords: NonNegativeInt
    selectedRecords: NonNegativeInt
    selectedFamilies: NonNegativeInt


class DoctorResult(ContractModel):
    command: Literal["doctor"]
    pythonVersion: NonEmptyStr
    platform: NonEmptyStr
    machine: NonEmptyStr
    mlxAvailable: StrictBool
    mlxDeviceAvailable: StrictBool
    freeDiskBytes: NonNegativeInt
    availableCommands: list[Command]
    unavailableCommands: list[Command]
    components: list[VersionedComponent]

    @model_validator(mode="after")
    def validate_lists(self) -> DoctorResult:
        for label, values in (
            ("availableCommands", self.availableCommands),
            ("unavailableCommands", self.unavailableCommands),
        ):
            if values != sorted(values) or len(values) != len(set(values)):
                raise ValueError(f"{label} must be sorted and unique")
        if set(self.availableCommands) & set(self.unavailableCommands):
            raise ValueError("available and unavailable commands must be disjoint")
        pairs = [(item.role, item.name) for item in self.components]
        if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
            raise ValueError("components must be sorted and unique")
        return self


class SchemaResult(ContractModel):
    command: Literal["schema"]
    mode: Literal["output", "check"]
    directory: LocalPath
    schemaCount: NonNegativeInt
    driftCount: StrictZero


class FetchResult(ContractModel):
    command: Literal["fetch"]
    artifact: CreatedArtifactResult
    fileCount: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def validate_artifact(self) -> FetchResult:
        _require_artifact(self.artifact, "checkpoint", "upstream")
        return self


class SplitRecordCounts(ContractModel):
    train: NonNegativeInt
    validation: NonNegativeInt
    calibration: NonNegativeInt
    test: NonNegativeInt


class PrepareResult(ContractModel):
    command: Literal["prepare"]
    artifact: CreatedArtifactResult
    datasetContentId: Digest
    partitionRecords: SplitRecordCounts
    diagnostic: StrictBool

    @model_validator(mode="after")
    def validate_artifact(self) -> PrepareResult:
        _require_artifact(self.artifact, "dataset", "none")
        return self


class CurateResult(ContractModel):
    command: Literal["curate"]
    status: Literal["prepared", "completed"]
    runPath: LocalPath
    configurationSha256: Digest
    sourceRecords: NonNegativeInt
    generatedAccepted: NonNegativeInt
    generatedQuarantined: NonNegativeInt
    datasets: Annotated[list[CreatedArtifactResult], Field(min_length=1)]
    reportPath: LocalPath
    warnings: list[NonEmptyStr]

    @model_validator(mode="after")
    def validate_result(self) -> CurateResult:
        for artifact in self.datasets:
            _require_artifact(artifact, "dataset", "none")
        identities = [(artifact.outputPath, artifact.artifactId) for artifact in self.datasets]
        if len(identities) != len(set(identities)):
            raise ValueError("curation datasets must be unique")
        if self.status == "prepared" and (
            self.generatedAccepted != 0 or self.generatedQuarantined != 0
        ):
            raise ValueError("prepared curation cannot report generated candidates")
        return self


class QuantizeResult(ContractModel):
    command: Literal["quantize"]
    artifact: CreatedArtifactResult
    bits: Literal[4, 8]
    groupSize: Literal[64]

    @model_validator(mode="after")
    def validate_artifact(self) -> QuantizeResult:
        if self.artifact.kind != "quantized":
            raise ValueError("quantize result must reference a quantized artifact")
        return self


class TrainResult(ContractModel):
    command: Literal["train"]
    artifact: CreatedArtifactResult
    finalLoss: NonNegativeFloat
    elapsedSeconds: NonNegativeFloat

    @model_validator(mode="after")
    def validate_artifact(self) -> TrainResult:
        _require_artifact(self.artifact, "adapter", "shared")
        return self


class CustomizeResult(ContractModel):
    command: Literal["customize"]
    artifact: CreatedArtifactResult
    finalLoss: NonNegativeFloat
    elapsedSeconds: NonNegativeFloat

    @model_validator(mode="after")
    def validate_artifact(self) -> CustomizeResult:
        _require_artifact(self.artifact, "adapter", "customer")
        return self


class EvaluateResult(ContractModel):
    command: Literal["evaluate"]
    artifact: CreatedArtifactResult
    split: Literal["validation", "calibration", "test"]
    exampleCount: NonNegativeInt
    representativeCount: NonNegativeInt
    exact: CountRate
    predictionsPath: LocalPath

    @model_validator(mode="after")
    def validate_artifact(self) -> EvaluateResult:
        if self.artifact.kind != "evaluation":
            raise ValueError("evaluate result must reference an evaluation artifact")
        if self.representativeCount > self.exampleCount:
            raise ValueError("representativeCount must not exceed exampleCount")
        return self


class CalibrateResult(ContractModel):
    command: Literal["calibrate"]
    artifact: CreatedArtifactResult
    threshold: Annotated[float, Field(strict=True, le=0, allow_inf_nan=False)] | None
    selection: RiskSampleSummary
    abstainAll: StrictBool

    @model_validator(mode="after")
    def validate_result(self) -> CalibrateResult:
        if self.artifact.kind != "policy":
            raise ValueError("calibrate result must reference a policy artifact")
        if self.abstainAll != (self.threshold is None):
            raise ValueError("abstainAll must match null threshold")
        return self


class AuditResult(ContractModel):
    command: Literal["audit"]
    artifact: CreatedArtifactResult
    status: Literal["insufficient", "meets-bound", "fails-bound"]
    counts: RiskSampleSummary
    coverage: UnitFloat
    upperErrorBound: UnitFloat | None

    @model_validator(mode="after")
    def validate_result(self) -> AuditResult:
        if self.artifact.kind != "audit":
            raise ValueError("audit result must reference an audit artifact")
        if (self.counts.acceptedCount == 0) != (self.upperErrorBound is None):
            raise ValueError("upperErrorBound nullability must match accepted count")
        return self


class MergeResult(ContractModel):
    command: Literal["merge"]
    artifact: CreatedArtifactResult
    modelArtifactId: Digest
    adapterArtifactId: Digest

    @model_validator(mode="after")
    def validate_artifact(self) -> MergeResult:
        if self.artifact.kind != "merged":
            raise ValueError("merge result must reference a merged artifact")
        return self


class ExportResult(ContractModel):
    command: Literal["export"]
    artifact: CreatedArtifactResult
    format: Literal["checkpoint", "gguf"]
    compatibilityStatus: Literal["unverified"]

    @model_validator(mode="after")
    def validate_artifact(self) -> ExportResult:
        if self.artifact.kind != "export":
            raise ValueError("export result must reference an export artifact")
        return self


class VerifyResult(ContractModel):
    command: Literal["verify"]
    path: LocalPath
    artifactId: Digest
    kind: ArtifactKind
    stage: Stage
    customer: Id | None
    fileCount: NonNegativeInt
    lineageNodeCount: NonNegativeInt
    valid: StrictTrue

    @model_validator(mode="after")
    def validate_scope(self) -> VerifyResult:
        if (self.stage == "customer") != (self.customer is not None):
            raise ValueError("customer is non-null exactly for customer stage")
        return self


type CliResultValue = Annotated[
    DoctorResult
    | SchemaResult
    | SetupResult
    | FetchResult
    | PrepareResult
    | CurateResult
    | QuantizeResult
    | TrainResult
    | CustomizeResult
    | EvaluateResult
    | CalibrateResult
    | AuditResult
    | MergeResult
    | ExportResult
    | VerifyResult,
    Field(discriminator="command"),
]


class _CliSuccessCommon(ContractModel):
    schemaVersion: SchemaVersion
    ok: StrictTrue


class DoctorSuccess(_CliSuccessCommon):
    command: Literal["doctor"]
    result: DoctorResult


class SchemaSuccess(_CliSuccessCommon):
    command: Literal["schema"]
    result: SchemaResult


class SetupSuccess(_CliSuccessCommon):
    command: Literal["setup"]
    result: SetupResult


class FetchSuccess(_CliSuccessCommon):
    command: Literal["fetch"]
    result: FetchResult


class PrepareSuccess(_CliSuccessCommon):
    command: Literal["prepare"]
    result: PrepareResult


class ProjectionPreparationSuccess(_CliSuccessCommon):
    command: Literal["prepare-source-projections"]
    result: ProjectionPreparationResult


class CurateSuccess(_CliSuccessCommon):
    command: Literal["curate"]
    result: CurateResult


class QuantizeSuccess(_CliSuccessCommon):
    command: Literal["quantize"]
    result: QuantizeResult


class TrainSuccess(_CliSuccessCommon):
    command: Literal["train"]
    result: TrainResult


class CustomizeSuccess(_CliSuccessCommon):
    command: Literal["customize"]
    result: CustomizeResult


class EvaluateSuccess(_CliSuccessCommon):
    command: Literal["evaluate"]
    result: EvaluateResult


class CalibrateSuccess(_CliSuccessCommon):
    command: Literal["calibrate"]
    result: CalibrateResult


class AuditSuccess(_CliSuccessCommon):
    command: Literal["audit"]
    result: AuditResult


class MergeSuccess(_CliSuccessCommon):
    command: Literal["merge"]
    result: MergeResult


class ExportSuccess(_CliSuccessCommon):
    command: Literal["export"]
    result: ExportResult


class VerifySuccess(_CliSuccessCommon):
    command: Literal["verify"]
    result: VerifyResult


type CliSuccessValue = Annotated[
    DoctorSuccess
    | SchemaSuccess
    | SetupSuccess
    | FetchSuccess
    | PrepareSuccess
    | ProjectionPreparationSuccess
    | CurateSuccess
    | QuantizeSuccess
    | TrainSuccess
    | CustomizeSuccess
    | EvaluateSuccess
    | CalibrateSuccess
    | AuditSuccess
    | MergeSuccess
    | ExportSuccess
    | VerifySuccess,
    Field(discriminator="command"),
]


class CliSuccess(RootModel[CliSuccessValue]):
    pass


class ErrorLocation(ContractModel):
    kind: Literal["argument", "config-pointer", "input-path", "artifact-path", "runtime"]
    value: NonEmptyStr


class CliError(ContractModel):
    code: CliErrorCode
    category: Literal["input", "environment", "execution", "integrity", "interrupted"]
    message: NonEmptyStr
    location: ErrorLocation | None
    artifactId: Digest | None

    @model_validator(mode="after")
    def validate_category(self) -> CliError:
        expected = _ERROR_CATEGORY[self.code]
        if self.category != expected:
            raise ValueError("error category does not match code")
        return self


class CliFailure(ContractModel):
    schemaVersion: SchemaVersion
    ok: StrictFalse
    command: Command | None
    exitCode: Literal[2, 3, 4, 5, 130]
    error: CliError

    @model_validator(mode="after")
    def validate_exit(self) -> CliFailure:
        if self.exitCode != _CATEGORY_EXIT[self.error.category]:
            raise ValueError("exitCode does not match error category")
        return self


def _require_artifact(artifact: CreatedArtifactResult, kind: ArtifactKind, stage: Stage) -> None:
    if artifact.kind != kind or artifact.stage != stage:
        raise ValueError(f"expected {kind}/{stage} artifact")


_INPUT_CODES = {
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
}
_ENVIRONMENT_CODES = {"DEPENDENCY_MISSING", "ENVIRONMENT_UNSUPPORTED", "ARCHITECTURE_UNSUPPORTED"}
_EXECUTION_CODES = {
    "BACKEND_FAILED",
    "IO_FAILED",
    "NETWORK_FAILED",
    "TIMEOUT",
    "OUTPUT_INVALID",
    "INTERNAL_ERROR",
}
_INTEGRITY_CODES = {
    "INTEGRITY_FAILED",
    "UNSAFE_ARTIFACT_PATH",
    "LINEAGE_MISMATCH",
    "LEAKAGE_DETECTED",
    "PROFILE_MISMATCH",
    "SAMPLE_OVERLAP",
}
_ERROR_CATEGORY: dict[str, str] = {
    **{code: "input" for code in _INPUT_CODES},
    **{code: "environment" for code in _ENVIRONMENT_CODES},
    **{code: "execution" for code in _EXECUTION_CODES},
    **{code: "integrity" for code in _INTEGRITY_CODES},
    "INTERRUPTED": "interrupted",
}
_CATEGORY_EXIT = {"input": 2, "environment": 3, "execution": 4, "integrity": 5, "interrupted": 130}

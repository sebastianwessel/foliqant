"""Kind-specific artifact detail and evaluation record contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from .base import (
    Commit,
    CompatibilityRecord,
    ContractModel,
    CountRate,
    Digest,
    Id,
    InheritedLeakageRef,
    InventoryFileRef,
    JsonPointer,
    JsonValue,
    LanguageTag,
    LeakageIndexRef,
    MeasuredBytes,
    NonEmptyStr,
    NonNegativeFloat,
    NonNegativeInt,
    Omitted,
    PositiveInt,
    PrecisionChange,
    RepoId,
    SafePath,
    SchemaVersion,
    Split,
    StrictTrue,
    StrictZero,
    UInt32,
    UnitFloat,
    VersionedComponent,
    _reject_explicit_null,
    _safe_path,
    _sorted_unique,
    canonical_digest,
)
from .inputs import ResolvedDatasetConfig, TrainConfig


class SourceFileSummary(ContractModel):
    sourceId: Id
    sha256: Digest
    size: NonNegativeInt
    recordCount: PositiveInt


class RecordAssignment(ContractModel):
    split: Split
    componentId: Digest
    groupIds: Annotated[list[Digest], Field(min_length=1)]

    @field_validator("groupIds")
    @classmethod
    def sorted_groups(cls, value: list[str]) -> list[str]:
        return _sorted_unique(value, label="groupIds")


class OriginCounts(ContractModel):
    human: NonNegativeInt
    synthetic: NonNegativeInt
    teacher: NonNegativeInt


class PartitionCounts(ContractModel):
    records: NonNegativeInt
    components: NonNegativeInt
    languages: dict[NonEmptyStr, NonNegativeInt]
    sources: dict[Id, NonNegativeInt]
    origins: OriginCounts

    @model_validator(mode="after")
    def validate_maps(self) -> PartitionCounts:
        for label, values in (("languages", self.languages), ("sources", self.sources)):
            if list(values) != sorted(values):
                raise ValueError(f"{label} keys must be sorted")
            if any(value == 0 for value in values.values()):
                raise ValueError(f"{label} must omit zero-valued entries")
            if sum(values.values()) != self.records:
                raise ValueError(f"{label} counts must sum to records")
        if self.origins.human + self.origins.synthetic + self.origins.teacher != self.records:
            raise ValueError("origin counts must sum to records")
        if self.components > self.records:
            raise ValueError("component count cannot exceed record count")
        return self


class ResolvedSplitSettings(ContractModel):
    seed: UInt32
    validationFraction: Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]
    calibrationFraction: Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]
    testFraction: Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def validate_sum(self) -> ResolvedSplitSettings:
        if self.validationFraction + self.calibrationFraction + self.testFraction >= 1:
            raise ValueError("split fractions must sum to less than one")
        return self


class PartitionMap(ContractModel):
    train: PartitionCounts
    validation: PartitionCounts
    calibration: PartitionCounts
    test: PartitionCounts


class SplitFileMap(ContractModel):
    train: InventoryFileRef
    validation: InventoryFileRef
    calibration: InventoryFileRef
    test: InventoryFileRef


class DatasetDetails(ContractModel):
    datasetConfig: ResolvedDatasetConfig
    datasetConfigSha256: Digest
    datasetContentId: Digest
    normalizationVersion: SchemaVersion
    splitSettings: ResolvedSplitSettings
    sourceFiles: Annotated[list[SourceFileSummary], Field(min_length=1)]
    assignments: dict[Id, RecordAssignment]
    partitions: PartitionMap
    chatFiles: SplitFileMap
    recordFiles: SplitFileMap
    leakageIndex: LeakageIndexRef
    diagnostic: StrictBool

    @model_validator(mode="after")
    def validate_dataset_details(self) -> DatasetDetails:
        if self.datasetConfigSha256 != canonical_digest(self.datasetConfig.model_dump(mode="json")):
            raise ValueError("datasetConfigSha256 does not match datasetConfig")
        expected_settings = {
            "seed": self.datasetConfig.seed,
            "validationFraction": self.datasetConfig.validationFraction,
            "calibrationFraction": self.datasetConfig.calibrationFraction,
            "testFraction": self.datasetConfig.testFraction,
        }
        if self.splitSettings.model_dump(mode="json") != expected_settings:
            raise ValueError("splitSettings do not match datasetConfig")
        source_ids = [source.sourceId for source in self.sourceFiles]
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            raise ValueError("sourceFiles must be sorted and unique by sourceId")
        if source_ids != [source.id for source in self.datasetConfig.sources]:
            raise ValueError("sourceFiles must cover the resolved sources exactly")
        if list(self.assignments) != sorted(self.assignments):
            raise ValueError("assignments must be sorted by record ID")
        expected_chat = {
            split: f"chats/{split}.jsonl"
            for split in ("train", "validation", "calibration", "test")
        }
        expected_record = {
            split: f"records/{split}.jsonl"
            for split in ("train", "validation", "calibration", "test")
        }
        for split, expected in expected_chat.items():
            ref = getattr(self.chatFiles, split)
            if ref.path != expected or ref.format != "jsonl":
                raise ValueError("chat file path or format is invalid")
            if ref.recordCount != getattr(self.partitions, split).records:
                raise ValueError("chat record count does not match partition")
        for split, expected in expected_record.items():
            ref = getattr(self.recordFiles, split)
            if ref.path != expected or ref.format != "jsonl":
                raise ValueError("record file path or format is invalid")
            if ref.recordCount != getattr(self.partitions, split).records:
                raise ValueError("record file count does not match partition")
        if self.leakageIndex.file.path != "leakage.jsonl":
            raise ValueError("dataset leakage index path must be leakage.jsonl")
        if self.leakageIndex.splits != ["calibration", "test", "train", "validation"]:
            raise ValueError("dataset leakage index must cover all sorted splits")
        total_records = sum(
            getattr(self.partitions, split).records
            for split in ("train", "validation", "calibration", "test")
        )
        if len(self.assignments) != total_records:
            raise ValueError("assignments must contain every partition record")
        if self.leakageIndex.entryCount != total_records:
            raise ValueError("dataset leakage index must contain every record")
        assignment_counts = {split: 0 for split in ("train", "validation", "calibration", "test")}
        for assignment in self.assignments.values():
            assignment_counts[assignment.split] += 1
        for split, count in assignment_counts.items():
            if count != getattr(self.partitions, split).records:
                raise ValueError("assignment split count does not match partition")
        return self


class ChatTemplateSource(ContractModel):
    kind: Literal["file", "tokenizer-config", "registered-default"]
    value: NonEmptyStr

    @model_validator(mode="after")
    def validate_source(self) -> ChatTemplateSource:
        if self.kind == "tokenizer-config" and self.value != "tokenizer_config.json#/chat_template":
            raise ValueError("tokenizer-config source has a fixed value")
        if self.kind == "file":
            _safe_path(self.value)
        return self


class ModelIdentity(ContractModel):
    architecture: NonEmptyStr
    weightFormat: Literal["safetensors", "gguf"]
    configSha256: Digest
    tokenizerSha256: Digest
    chatTemplateSha256: Digest
    tokenizerFiles: Annotated[list[SafePath], Field(min_length=1)]
    chatTemplateSource: ChatTemplateSource

    @field_validator("tokenizerFiles")
    @classmethod
    def sorted_tokenizer_files(cls, value: list[str]) -> list[str]:
        return _sorted_unique(value, label="tokenizerFiles")


class CheckpointDetails(ContractModel):
    model: ModelIdentity
    upstreamRepo: RepoId
    upstreamRevision: Commit
    licenseRef: NonEmptyStr
    weightPrecision: NonEmptyStr
    compatibility: list[CompatibilityRecord]

    @model_validator(mode="after")
    def validate_checkpoint(self) -> CheckpointDetails:
        if self.model.weightFormat != "safetensors":
            raise ValueError("checkpoint weight format must be safetensors")
        _validate_compatibility(self.compatibility)
        return self


class QuantizedDetails(ContractModel):
    model: ModelIdentity
    bits: Literal[4, 8]
    groupSize: Literal[64]
    parentArtifactId: Digest
    precisionHistory: Annotated[list[PrecisionChange], Field(min_length=1)]
    exposureLeakageIndex: LeakageIndexRef
    compatibility: list[CompatibilityRecord]
    inheritedLeakageIndexes: list[InheritedLeakageRef]

    @model_validator(mode="after")
    def validate_quantized(self) -> QuantizedDetails:
        if self.model.weightFormat != "safetensors":
            raise ValueError("quantized model format must be safetensors")
        if self.precisionHistory[-1].operation != "quantize":
            raise ValueError("precision history must end with quantize")
        _validate_inherited(self.inheritedLeakageIndexes)
        _validate_compatibility(self.compatibility)
        return self


class BackendTrainConfig(ContractModel):
    method: Literal["lora", "qlora"]
    maskPrompt: StrictTrue
    seed: UInt32
    steps: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    batchSize: Annotated[StrictInt, Field(ge=1, le=64)]
    gradientAccumulation: Annotated[StrictInt, Field(ge=1, le=1024)]
    maxSequenceLength: Annotated[StrictInt, Field(ge=64, le=131_072)]
    learningRate: Annotated[StrictFloat, Field(gt=0, le=1, allow_inf_nan=False)]
    numLayers: Annotated[StrictInt, Field(ge=-1, le=1024)]
    rank: Annotated[StrictInt, Field(ge=1, le=256)]
    scale: Annotated[StrictFloat, Field(gt=0, le=256, allow_inf_nan=False)]
    dropout: Annotated[StrictFloat, Field(ge=0, lt=1, allow_inf_nan=False)]
    gradientCheckpointing: StrictBool
    validationEvery: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    validationBatches: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    saveEvery: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    modelArtifactId: Digest
    datasetArtifactId: Digest
    trainFileSha256: Digest
    validationFileSha256: Digest

    @field_validator("numLayers")
    @classmethod
    def valid_num_layers(cls, value: int) -> int:
        if value == 0:
            raise ValueError("numLayers is -1 or positive")
        return value


class AdapterValidationObservation(ContractModel):
    iteration: NonNegativeInt
    loss: NonNegativeFloat
    elapsedSeconds: NonNegativeFloat


class AdapterDetails(ContractModel):
    modelArtifactId: Digest
    datasetArtifactId: Digest
    trainConfig: TrainConfig
    trainConfigSha256: Digest
    backendConfig: BackendTrainConfig
    warmStartArtifactId: Omitted[Digest] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    usedDataLeakageIndex: LeakageIndexRef
    inheritedLeakageIndexes: list[InheritedLeakageRef]
    finalLoss: NonNegativeFloat
    validation: list[AdapterValidationObservation]
    peakMemoryBytes: MeasuredBytes
    elapsedSeconds: NonNegativeFloat
    trainableTensorNames: Annotated[list[NonEmptyStr], Field(min_length=1)]
    adapterFormat: Literal["mlx-lora-v1"]

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "warmStartArtifactId")

    @model_validator(mode="after")
    def validate_adapter(self) -> AdapterDetails:
        if self.trainConfigSha256 != canonical_digest(self.trainConfig.model_dump(mode="json")):
            raise ValueError("trainConfigSha256 does not match trainConfig")
        if self.backendConfig.modelArtifactId != self.modelArtifactId:
            raise ValueError("backend model identity mismatch")
        if self.backendConfig.datasetArtifactId != self.datasetArtifactId:
            raise ValueError("backend dataset identity mismatch")
        projected = {
            key: getattr(self.trainConfig, key)
            for key in (
                "seed",
                "steps",
                "batchSize",
                "gradientAccumulation",
                "maxSequenceLength",
                "learningRate",
                "numLayers",
                "rank",
                "scale",
                "dropout",
                "gradientCheckpointing",
                "validationEvery",
                "validationBatches",
                "saveEvery",
            )
        }
        if any(getattr(self.backendConfig, key) != value for key, value in projected.items()):
            raise ValueError("backendConfig does not match normalized trainConfig")
        if self.usedDataLeakageIndex.file.path != "leakage.jsonl":
            raise ValueError("adapter leakage path must be leakage.jsonl")
        if self.usedDataLeakageIndex.splits != ["train", "validation"]:
            raise ValueError("adapter leakage splits must be train,validation")
        iterations = [item.iteration for item in self.validation]
        if iterations != sorted(iterations) or len(iterations) != len(set(iterations)):
            raise ValueError("adapter validation observations must have ordered unique iterations")
        _sorted_unique(self.trainableTensorNames, label="trainableTensorNames")
        _validate_inherited(self.inheritedLeakageIndexes)
        return self


class MergedDetails(ContractModel):
    model: ModelIdentity
    modelParentArtifactId: Digest
    adapterArtifactId: Digest
    precisionHistory: list[PrecisionChange]
    exposureLeakageIndex: LeakageIndexRef
    inheritedLeakageIndexes: list[InheritedLeakageRef]
    compatibility: list[CompatibilityRecord]

    @model_validator(mode="after")
    def validate_merged(self) -> MergedDetails:
        if self.model.weightFormat != "safetensors":
            raise ValueError("merged model format must be safetensors")
        if self.exposureLeakageIndex.file.path != "leakage.jsonl":
            raise ValueError("merged exposure path must be leakage.jsonl")
        _validate_inherited(self.inheritedLeakageIndexes)
        _validate_compatibility(self.compatibility)
        return self


class GenerationSettings(ContractModel):
    temperature: StrictZero
    seed: UInt32
    maxTokens: Annotated[StrictInt, Field(ge=1, le=32_768)]


class ValidatorSettings(ContractModel):
    outputSchemaSha256: Digest | None
    evidencePointer: JsonPointer | None
    fieldPointers: list[JsonPointer]
    jsonRequired: StrictBool

    @model_validator(mode="after")
    def validate_settings(self) -> ValidatorSettings:
        _sorted_unique(self.fieldPointers, label="fieldPointers")
        expected = (
            self.outputSchemaSha256 is not None
            or self.evidencePointer is not None
            or bool(self.fieldPointers)
        )
        if self.jsonRequired != expected:
            raise ValueError("jsonRequired does not match configured validators")
        return self


class ScoringIdentity(ContractModel):
    jsonParserVersion: Literal["rfc8259-v1"]
    exactVersion: Literal["canonical-json-or-stripped-text-v1"]
    evidenceVersion: Literal["exact-input-substring-v1"]
    fieldVersion: Literal["json-pointer-equality-v1"]


class DeploymentProfile(ContractModel):
    modelArtifactId: Digest
    adapterArtifactId: Digest | None
    configSha256: Digest
    tokenizerSha256: Digest
    chatTemplateSha256: Digest
    generation: GenerationSettings
    validators: ValidatorSettings
    scoring: ScoringIdentity
    pythonVersion: NonEmptyStr
    platform: NonEmptyStr
    machine: NonEmptyStr
    runtime: list[VersionedComponent]

    @model_validator(mode="after")
    def validate_runtime(self) -> DeploymentProfile:
        pairs = [(item.role, item.name) for item in self.runtime]
        if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
            raise ValueError("runtime must be sorted and unique by role/name")
        return self


class ComponentRepresentative(ContractModel):
    componentId: Digest
    recordId: Id
    groupIds: Annotated[list[Digest], Field(min_length=1)]

    @field_validator("groupIds")
    @classmethod
    def sorted_groups(cls, value: list[str]) -> list[str]:
        return _sorted_unique(value, label="groupIds")


class EvaluationAggregate(ContractModel):
    total: NonNegativeInt
    exact: CountRate
    jsonMetric: CountRate = Field(alias="json", serialization_alias="json")
    schemaMetric: CountRate = Field(alias="schema", serialization_alias="schema")
    evidence: CountRate
    applicableValidation: CountRate
    fields: dict[JsonPointer, CountRate]

    @model_validator(mode="after")
    def validate_aggregate(self) -> EvaluationAggregate:
        if self.exact.eligibleCount != self.total or self.jsonMetric.eligibleCount != self.total:
            raise ValueError("exact and json denominators must equal total")
        if list(self.fields) != sorted(self.fields):
            raise ValueError("field aggregate keys must be sorted")
        return self


class Prediction(ContractModel):
    id: Id
    sourceId: Id
    language: LanguageTag
    tags: list[str]
    componentId: Digest
    groupIds: Annotated[list[Digest], Field(min_length=1)]
    representative: StrictBool
    expected: str
    generated: str
    expectedJson: JsonValue | None
    generatedJson: JsonValue | None
    elapsedSeconds: NonNegativeFloat
    generatedTokens: NonNegativeInt
    meanTokenLogprob: Annotated[StrictFloat, Field(le=0, allow_inf_nan=False)] | None
    meanTokenLogprobUnavailableReason: Omitted[Literal["no-generated-tokens"]] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    jsonValid: StrictBool
    finishReason: Literal["stop", "length"]
    schemaValid: StrictBool | None
    evidenceValid: StrictBool | None
    fieldPresent: dict[JsonPointer, StrictBool]
    fieldValid: dict[JsonPointer, StrictBool]
    applicableValid: StrictBool
    exactCorrect: StrictBool

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "meanTokenLogprobUnavailableReason")

    @model_validator(mode="after")
    def validate_prediction(self) -> Prediction:
        _sorted_unique(self.tags, label="tags")
        _sorted_unique(self.groupIds, label="groupIds")
        if list(self.fieldPresent) != sorted(self.fieldPresent):
            raise ValueError("fieldPresent keys must be sorted")
        if list(self.fieldValid) != sorted(self.fieldValid):
            raise ValueError("fieldValid keys must be sorted")
        if self.fieldPresent.keys() != self.fieldValid.keys():
            raise ValueError("field presence and validity keys must match")
        if any(valid and not self.fieldPresent[key] for key, valid in self.fieldValid.items()):
            raise ValueError("a missing field cannot be valid")
        missing_reason = self.meanTokenLogprobUnavailableReason is None
        if self.generatedTokens == 0:
            if self.meanTokenLogprob is not None or missing_reason:
                raise ValueError("empty completion requires null score and reason")
        elif self.meanTokenLogprob is None or not missing_reason:
            raise ValueError("nonempty completion requires score and omits reason")
        if not self.jsonValid and self.generatedJson is not None:
            raise ValueError("invalid JSON must have a null generatedJson value")
        return self


class EvaluationDetails(ContractModel):
    modelArtifactId: Digest
    adapterArtifactId: Digest | None
    datasetArtifactId: Digest
    split: Literal["validation", "calibration", "test"]
    evaluationConfigSha256: Digest
    deploymentProfile: DeploymentProfile
    deploymentProfileId: Digest
    selectedRecordIds: list[Id]
    selectedGroupIds: list[Digest]
    representatives: list[ComponentRepresentative]
    selectionUnit: Literal["component-representative-v1"]
    predictions: InventoryFileRef
    outputSchemaFile: InventoryFileRef | None
    aggregate: EvaluationAggregate
    languages: dict[LanguageTag, EvaluationAggregate]
    sources: dict[Id, EvaluationAggregate]
    diagnostic: StrictBool

    @model_validator(mode="after")
    def validate_evaluation(self) -> EvaluationDetails:
        if self.deploymentProfileId != canonical_digest(
            self.deploymentProfile.model_dump(mode="json")
        ):
            raise ValueError("deploymentProfileId does not match deploymentProfile")
        if self.modelArtifactId != self.deploymentProfile.modelArtifactId:
            raise ValueError("deployment profile model identity mismatch")
        if self.adapterArtifactId != self.deploymentProfile.adapterArtifactId:
            raise ValueError("deployment profile adapter identity mismatch")
        _sorted_unique(self.selectedRecordIds, label="selectedRecordIds")
        _sorted_unique(self.selectedGroupIds, label="selectedGroupIds")
        rep_keys = [rep.componentId for rep in self.representatives]
        if rep_keys != sorted(rep_keys) or len(rep_keys) != len(set(rep_keys)):
            raise ValueError("representatives must be sorted and unique by componentId")
        if self.predictions.format != "jsonl":
            raise ValueError("predictions must be JSONL")
        if self.predictions.recordCount != len(self.selectedRecordIds):
            raise ValueError("prediction count must equal selected record count")
        configured_schema = self.deploymentProfile.validators.outputSchemaSha256 is not None
        if configured_schema != (self.outputSchemaFile is not None):
            raise ValueError("outputSchemaFile must match validator configuration")
        if self.outputSchemaFile is not None and self.outputSchemaFile.format != "json":
            raise ValueError("output schema file must be JSON")
        if self.outputSchemaFile is not None and (
            self.outputSchemaFile.sha256 != self.deploymentProfile.validators.outputSchemaSha256
        ):
            raise ValueError("output schema digest does not match validators")
        representative_ids = [item.recordId for item in self.representatives]
        if any(record_id not in self.selectedRecordIds for record_id in representative_ids):
            raise ValueError("representative records must be selected")
        representative_groups = {
            group_id for item in self.representatives for group_id in item.groupIds
        }
        if not representative_groups.issubset(set(self.selectedGroupIds)):
            raise ValueError("representative groups must be selected")
        _validate_aggregate(
            self.aggregate,
            self.deploymentProfile.validators,
            len(self.selectedRecordIds),
        )
        if list(self.languages) != sorted(self.languages) or list(self.sources) != sorted(
            self.sources
        ):
            raise ValueError("slice map keys must be sorted")
        if sum(item.total for item in self.languages.values()) != self.aggregate.total:
            raise ValueError("language slice totals must equal aggregate total")
        if sum(item.total for item in self.sources.values()) != self.aggregate.total:
            raise ValueError("source slice totals must equal aggregate total")
        for item in [*self.languages.values(), *self.sources.values()]:
            _validate_aggregate(item, self.deploymentProfile.validators, item.total)
        return self


class RiskSampleSummary(ContractModel):
    representativeCount: NonNegativeInt
    eligibleCount: NonNegativeInt
    acceptedCount: NonNegativeInt
    errors: NonNegativeInt

    @model_validator(mode="after")
    def validate_counts(self) -> RiskSampleSummary:
        if not (
            self.errors <= self.acceptedCount <= self.eligibleCount <= self.representativeCount
        ):
            raise ValueError("risk sample counts are inconsistent")
        return self


class PolicyDetails(ContractModel):
    evaluationArtifactId: Digest
    deploymentProfileId: Digest
    datasetArtifactId: Digest
    selectionUnit: Literal["component-representative-v1"]
    representativeRecordIds: Annotated[list[Id], Field(min_length=1)]
    representativeGroupIds: list[Digest]
    threshold: Annotated[StrictFloat, Field(le=0, allow_inf_nan=False)] | None
    maxError: Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]
    minAccepted: PositiveInt
    selection: RiskSampleSummary
    reason: Omitted[NonEmptyStr] = Field(default=None, exclude_if=lambda value: value is None)
    scoreMethod: Literal["mean-generated-token-logprob-v1"]

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "reason")

    @model_validator(mode="after")
    def validate_policy(self) -> PolicyDetails:
        _sorted_unique(self.representativeRecordIds, label="representativeRecordIds")
        _sorted_unique(self.representativeGroupIds, label="representativeGroupIds")
        if self.selection.representativeCount != len(self.representativeRecordIds):
            raise ValueError("selection representative count does not match IDs")
        reason_missing = self.reason is None
        if self.threshold is None:
            if self.selection.acceptedCount or self.selection.errors or reason_missing:
                raise ValueError("abstain-all policy requires zero accepted/errors and a reason")
        elif not reason_missing:
            raise ValueError("non-abstain policy must omit reason")
        return self


class AuditDetails(ContractModel):
    evaluationArtifactId: Digest
    policyArtifactId: Digest
    deploymentProfileId: Digest
    datasetArtifactId: Digest
    selectionUnit: Literal["component-representative-v1"]
    representativeRecordIds: Annotated[list[Id], Field(min_length=1)]
    representativeGroupIds: list[Digest]
    counts: RiskSampleSummary
    total: NonNegativeInt
    coverage: UnitFloat
    upperErrorBound: UnitFloat | None
    status: Literal["insufficient", "meets-bound", "fails-bound"]
    diagnostic: StrictBool
    assumptions: Annotated[list[NonEmptyStr], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_audit(self) -> AuditDetails:
        _sorted_unique(self.representativeRecordIds, label="representativeRecordIds")
        _sorted_unique(self.representativeGroupIds, label="representativeGroupIds")
        _sorted_unique(self.assumptions, label="assumptions")
        if self.counts.representativeCount != len(self.representativeRecordIds):
            raise ValueError("audit representative count does not match IDs")
        if self.total < self.counts.representativeCount:
            raise ValueError("audit total cannot be smaller than representative count")
        expected = (
            self.counts.acceptedCount / self.counts.representativeCount
            if self.counts.representativeCount
            else 0.0
        )
        if self.coverage != expected:
            raise ValueError("coverage must equal acceptedCount / representativeCount")
        if (self.counts.acceptedCount == 0) != (self.upperErrorBound is None):
            raise ValueError("upperErrorBound is null exactly when none are accepted")
        return self


class CheckpointExportMetadata(ContractModel):
    format: Literal["checkpoint"]
    safetensorsFileCount: PositiveInt
    tensorCount: PositiveInt


class GgufExportMetadata(ContractModel):
    format: Literal["gguf"]
    ggufVersion: PositiveInt
    tensorCount: PositiveInt
    metadataKeyCount: PositiveInt
    quantizationType: NonEmptyStr


type ExportMetadata = Annotated[
    CheckpointExportMetadata | GgufExportMetadata,
    Field(discriminator="format"),
]


class ExportDetails(ContractModel):
    model: ModelIdentity
    mergedArtifactId: Digest
    exportMetadata: ExportMetadata
    precisionHistory: list[PrecisionChange]
    exposureLeakageIndex: LeakageIndexRef
    inheritedLeakageIndexes: list[InheritedLeakageRef]
    compatibility: list[CompatibilityRecord]

    @model_validator(mode="after")
    def validate_export(self) -> ExportDetails:
        expected_format = "safetensors" if self.exportMetadata.format == "checkpoint" else "gguf"
        if self.model.weightFormat != expected_format:
            raise ValueError("model and export metadata formats disagree")
        if self.exposureLeakageIndex.file.path != "leakage.jsonl":
            raise ValueError("export exposure path must be leakage.jsonl")
        if any(
            record.status != "unverified" or record.evidence is not None
            for record in self.compatibility
        ):
            raise ValueError("export compatibility must remain unverified")
        _validate_inherited(self.inheritedLeakageIndexes)
        _validate_compatibility(self.compatibility)
        return self


def _validate_inherited(items: list[InheritedLeakageRef]) -> None:
    ids = [item.ancestorArtifactId for item in items]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("inherited leakage indexes must be sorted and unique")


def _validate_compatibility(items: list[CompatibilityRecord]) -> None:
    targets = [item.target for item in items]
    if targets != sorted(targets) or len(targets) != len(set(targets)):
        raise ValueError("compatibility records must be sorted and unique by target")


def _validate_aggregate(
    aggregate: EvaluationAggregate,
    validators: ValidatorSettings,
    total: int,
) -> None:
    if aggregate.total != total:
        raise ValueError("aggregate total does not match its slice")
    if aggregate.exact.eligibleCount != total or aggregate.jsonMetric.eligibleCount != total:
        raise ValueError("exact and json denominators must equal aggregate total")
    if aggregate.applicableValidation.eligibleCount != total:
        raise ValueError("applicable-validation denominator must equal aggregate total")
    expected_schema = total if validators.outputSchemaSha256 is not None else 0
    expected_evidence = total if validators.evidencePointer is not None else 0
    if aggregate.schemaMetric.eligibleCount != expected_schema:
        raise ValueError("schema denominator does not match validator configuration")
    if aggregate.evidence.eligibleCount != expected_evidence:
        raise ValueError("evidence denominator does not match validator configuration")
    if list(aggregate.fields) != validators.fieldPointers:
        raise ValueError("field aggregates must match configured field pointers")
    if any(value.eligibleCount != total for value in aggregate.fields.values()):
        raise ValueError("configured field denominators must equal aggregate total")

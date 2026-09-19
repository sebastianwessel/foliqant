"""User supplied model-lifecycle input contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from .base import (
    ContractModel,
    Digest,
    Id,
    JsonPointer,
    LanguageTag,
    LocalPath,
    NonEmptyStr,
    Omitted,
    SchemaVersion,
    Split,
    StrictTrue,
    UInt32,
    _reject_explicit_null,
)


class ChatMessage(ContractModel):
    role: Literal["system", "user", "assistant"]
    content: NonEmptyStr

    @field_validator("content")
    @classmethod
    def content_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 1_048_576:
            raise ValueError("content exceeds 1 MiB")
        return value


class GenerationProvenance(ContractModel):
    provider: Literal["openai-compatible"]
    modelId: NonEmptyStr
    modelIdentitySha256: Digest
    promptSha256: Digest
    parametersSha256: Digest
    requestSha256: Digest
    parentRecordIds: Annotated[list[Id], Field(min_length=1)]
    modelWeightsSha256: Omitted[Digest] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "modelWeightsSha256")

    @field_validator("parentRecordIds")
    @classmethod
    def sorted_parent_ids(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("parentRecordIds must be sorted and unique")
        return value


class DataRecord(ContractModel):
    schemaVersion: SchemaVersion
    id: Id
    sourceId: Id
    language: LanguageTag
    groupKeys: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=256)]
    messages: Annotated[list[ChatMessage], Field(min_length=2, max_length=256)]
    tags: list[str] = Field(default_factory=list)
    origin: Literal["human", "synthetic", "teacher"]
    reviewed: StrictBool
    familyId: Omitted[Id] = Field(default=None, exclude_if=lambda value: value is None)
    generation: Omitted[GenerationProvenance] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "familyId", "generation")

    @model_validator(mode="after")
    def validate_record(self) -> DataRecord:
        if len(self.groupKeys) != len(set(self.groupKeys)):
            raise ValueError("groupKeys must be unique")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        if self.generation is not None:
            if self.origin not in {"synthetic", "teacher"}:
                raise ValueError("generation provenance is only valid for generated records")
            if self.reviewed:
                raise ValueError("generated records with generation provenance cannot be reviewed")
            if self.familyId is None:
                raise ValueError("generation provenance requires familyId")
            if self.id in self.generation.parentRecordIds:
                raise ValueError("a generated record cannot be its own parent")
        roles = [message.role for message in self.messages]
        if roles[0] == "system":
            roles = roles[1:]
        if len(roles) < 2 or roles[-2:] != ["user", "assistant"]:
            raise ValueError("conversation must end with user then assistant")
        expected = "user"
        for role in roles:
            if role != expected:
                raise ValueError("messages must alternate user and assistant")
            expected = "assistant" if expected == "user" else "user"
        return self


class FrozenFamilyAssignment(ContractModel):
    split: Split
    sourceSplits: Annotated[list[NonEmptyStr], Field(min_length=1)]

    @field_validator("sourceSplits")
    @classmethod
    def sorted_source_splits(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("sourceSplits must be sorted and unique")
        return value


class SourceDeclaration(ContractModel):
    id: Id
    path: LocalPath
    license: NonEmptyStr
    licenseEvidence: NonEmptyStr
    trainingAllowed: StrictTrue
    sharedTrainingAllowed: StrictBool = False
    redistributionAllowed: StrictBool
    privacy: Literal["public", "private"]
    authorizationRef: Omitted[NonEmptyStr] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    attribution: str = ""
    commercialUse: Literal["allowed", "restricted", "unknown"] = "unknown"
    restrictions: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "authorizationRef")

    @model_validator(mode="after")
    def validate_authorization(self) -> SourceDeclaration:
        missing = self.authorizationRef is None
        if self.privacy == "private" and missing:
            raise ValueError("private sources require authorizationRef")
        if self.privacy == "public" and not missing:
            raise ValueError("public sources must omit authorizationRef")
        if len(self.restrictions) != len(set(self.restrictions)):
            raise ValueError("restrictions must be unique")
        return self


class ResolvedSourceDeclaration(ContractModel):
    id: Id
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
    def validate_authorization(self) -> ResolvedSourceDeclaration:
        missing = self.authorizationRef is None
        if self.privacy == "private" and missing:
            raise ValueError("private sources require authorizationRef")
        if self.privacy == "public" and not missing:
            raise ValueError("public sources must omit authorizationRef")
        if len(self.restrictions) != len(set(self.restrictions)):
            raise ValueError("restrictions must be unique")
        return self


Fraction = Annotated[StrictFloat, Field(gt=0, lt=1, allow_inf_nan=False)]


class DatasetConfig(ContractModel):
    schemaVersion: SchemaVersion
    name: Id
    sources: Annotated[list[SourceDeclaration], Field(min_length=1, max_length=1024)]
    seed: UInt32 = 42
    validationFraction: Fraction = 0.1
    calibrationFraction: Fraction = 0.1
    testFraction: Fraction = 0.1
    maxRecords: Annotated[StrictInt, Field(ge=4, le=1_000_000)] = 100_000
    maxRecordBytes: Annotated[StrictInt, Field(ge=1, le=16_777_216)] = 1_048_576
    frozenFamilies: Omitted[dict[Id, FrozenFamilyAssignment]] = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Complete source-family assignments frozen before augmentation; when present, "
            "these assignments override fraction-based preparation and the fractions remain "
            "planning hints"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "frozenFamilies")

    @model_validator(mode="after")
    def validate_dataset_config(self) -> DatasetConfig:
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source IDs must be unique")
        if self.validationFraction + self.calibrationFraction + self.testFraction >= 1:
            raise ValueError("split fractions must sum to less than one")
        if self.frozenFamilies is not None:
            if not self.frozenFamilies:
                raise ValueError("frozenFamilies must be nonempty when provided")
            if list(self.frozenFamilies) != sorted(self.frozenFamilies):
                raise ValueError("frozenFamilies keys must be sorted")
        return self


class ResolvedDatasetConfig(ContractModel):
    schemaVersion: SchemaVersion
    name: Id
    sources: Annotated[list[ResolvedSourceDeclaration], Field(min_length=1, max_length=1024)]
    seed: UInt32
    validationFraction: Fraction
    calibrationFraction: Fraction
    testFraction: Fraction
    maxRecords: Annotated[StrictInt, Field(ge=4, le=1_000_000)]
    maxRecordBytes: Annotated[StrictInt, Field(ge=1, le=16_777_216)]
    frozenFamilies: Omitted[dict[Id, FrozenFamilyAssignment]] = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Complete source-family assignments frozen before augmentation; when present, "
            "these assignments override fraction-based preparation and the fractions remain "
            "planning hints"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "frozenFamilies")

    @model_validator(mode="after")
    def validate_dataset_config(self) -> ResolvedDatasetConfig:
        ids = [source.id for source in self.sources]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("resolved sources must be sorted and unique by ID")
        if self.validationFraction + self.calibrationFraction + self.testFraction >= 1:
            raise ValueError("split fractions must sum to less than one")
        if self.frozenFamilies is not None:
            if not self.frozenFamilies:
                raise ValueError("frozenFamilies must be nonempty when provided")
            if list(self.frozenFamilies) != sorted(self.frozenFamilies):
                raise ValueError("frozenFamilies keys must be sorted")
        return self


class TrainConfig(ContractModel):
    schemaVersion: SchemaVersion
    name: Id
    seed: UInt32 = 42
    steps: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 100
    batchSize: Annotated[StrictInt, Field(ge=1, le=64)] = 1
    gradientAccumulation: Annotated[StrictInt, Field(ge=1, le=1024)] = 1
    maxSequenceLength: Annotated[StrictInt, Field(ge=64, le=131_072)] = 2048
    learningRate: Annotated[StrictFloat, Field(gt=0, le=1, allow_inf_nan=False)] = 0.0001
    numLayers: Annotated[StrictInt, Field(ge=-1, le=1024)] = -1
    rank: Annotated[StrictInt, Field(ge=1, le=256)] = 8
    scale: Annotated[StrictFloat, Field(gt=0, le=256, allow_inf_nan=False)] = 20.0
    dropout: Annotated[StrictFloat, Field(ge=0, lt=1, allow_inf_nan=False)] = 0.0
    gradientCheckpointing: StrictBool = True
    validationEvery: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 25
    validationBatches: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 10
    saveEvery: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 25
    timeoutSeconds: Annotated[StrictInt, Field(ge=1, le=604_800)] = 3600

    @field_validator("numLayers")
    @classmethod
    def valid_num_layers(cls, value: int) -> int:
        if value == 0:
            raise ValueError("numLayers is -1 or a positive integer")
        return value


class EvaluationConfig(ContractModel):
    schemaVersion: SchemaVersion
    seed: UInt32 = 42
    maxTokens: Annotated[StrictInt, Field(ge=1, le=32_768)] = 256
    maxExamples: Omitted[Annotated[StrictInt, Field(ge=1)]] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    outputSchema: Omitted[LocalPath] = Field(default=None, exclude_if=lambda value: value is None)
    evidencePointer: Omitted[JsonPointer] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    fieldPointers: list[JsonPointer] = Field(default_factory=list)
    timeoutSeconds: Annotated[StrictInt, Field(ge=1, le=604_800)] = 3600

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        return _reject_explicit_null(value, "maxExamples", "outputSchema", "evidencePointer")

    @field_validator("fieldPointers")
    @classmethod
    def unique_pointers(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("fieldPointers must be unique")
        return value

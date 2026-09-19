"""Strict contracts for unattended source curation and local generation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from ..contracts.base import (
    ContractModel,
    Digest,
    Id,
    NonEmptyStr,
    NonNegativeInt,
    SchemaVersion,
    Split,
    UInt32,
)
from ..contracts.inputs import DataRecord, FrozenFamilyAssignment, ResolvedSourceDeclaration
from ..contracts.setup import SetupAsset
from .endpoint import LocalEndpointConfig


class SourceSelection(ContractModel):
    """Select a pinned built-in source and a bounded conversion size."""

    id: Id
    maxRecords: Annotated[StrictInt, Field(ge=4, le=100_000)] = 1000


class ImportedRecord(ContractModel):
    """Normalized source annotation with its original partition and identity."""

    record: DataRecord
    originalSplit: Literal["train", "validation", "calibration", "test", "unspecified"]
    task: Literal["classification", "entailment", "decision", "question-answering"]
    originalId: NonEmptyStr


class SourceBatch(ContractModel):
    """Observed source records and permissions, never guessed acquisition results."""

    source: ResolvedSourceDeclaration
    revision: NonEmptyStr
    assets: list[SetupAsset]
    records: list[ImportedRecord]
    totalAvailable: NonNegativeInt
    excludedCounts: dict[str, NonNegativeInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> SourceBatch:
        if not self.records or any(row.record.sourceId != self.source.id for row in self.records):
            raise ValueError("source batch requires matching nonempty records")
        if len(self.records) > self.totalAvailable:
            raise ValueError("selected records cannot exceed available records")
        return self


type Scenario = Literal[
    "withdrawn-request",
    "multiple-intents",
    "missing-evidence",
    "conflicting-information",
    "changed-deadline",
]


def _default_languages() -> list[Literal["en", "de"]]:
    return ["en"]


def _default_scenarios() -> list[Scenario]:
    return [
        "withdrawn-request",
        "multiple-intents",
        "missing-evidence",
        "conflicting-information",
        "changed-deadline",
    ]


class GenerationSettings(ContractModel):
    """Explicit bounded generation; quarantine never becomes human review."""

    maxCandidates: Annotated[StrictInt, Field(ge=1, le=100_000)] = 100
    maxAttempts: Annotated[StrictInt, Field(ge=1, le=5)] = 2
    languages: Annotated[list[Literal["en", "de"]], Field(min_length=1, max_length=2)] = Field(
        default_factory=_default_languages
    )
    scenarios: Annotated[list[Scenario], Field(min_length=1)] = Field(
        default_factory=_default_scenarios
    )
    scenarioFamilies: Annotated[StrictInt, Field(ge=4, le=10_000)] = 20
    maxInputCharacters: Annotated[StrictInt, Field(ge=256, le=131_072)] = 16_000

    @field_validator("languages", "scenarios")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class CurationConfig(ContractModel):
    """Complete recipe for the source corpus and local augmentation workflow."""

    schemaVersion: SchemaVersion = 1
    name: Id = "financial-decisions-en-v1"
    sources: Annotated[list[SourceSelection], Field(min_length=1, max_length=32)] = Field(
        default_factory=lambda: [
            SourceSelection(id=source)
            for source in ("banking77", "typed-decisions", "wanli", "multidogo-finance", "tatqa")
        ]
    )
    endpoint: LocalEndpointConfig = Field(default_factory=LocalEndpointConfig)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    seed: UInt32 = 42

    @model_validator(mode="after")
    def unique_sources(self) -> CurationConfig:
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("source IDs must be unique")
        return self


class CandidateText(ContractModel):
    """A proposed input; the generator cannot change the reference answer."""

    input: Annotated[str, Field(min_length=1, max_length=131_072)]


class CandidateCheck(ContractModel):
    """Independent model judgment, not a correctness certificate."""

    answer: Annotated[str, Field(min_length=1, max_length=32_768)]
    supported: StrictBool
    issues: Annotated[list[NonEmptyStr], Field(max_length=32)]


class CandidateJob(ContractModel):
    """Immutable work item whose family and destination were fixed before inference."""

    jobId: Digest
    parentRecordId: Id
    familyId: Id
    split: Split
    language: Literal["en", "de"]
    purpose: Literal["training-augmentation", "synthetic-regression"]
    operation: Literal[
        "paraphrase",
        "source-question",
        "irrelevant-context",
        "withdrawn-request",
        "multiple-intents",
        "missing-evidence",
        "conflicting-information",
        "changed-deadline",
    ]


class CandidateOutcome(ContractModel):
    """Persisted automatic decision and safe reason for one generated candidate."""

    jobId: Digest
    status: Literal["accepted", "quarantined"]
    reason: NonEmptyStr
    attempts: Annotated[StrictInt, Field(ge=1, le=5)]
    requestSha256: Digest
    responseSha256: Digest
    record: DataRecord | None

    @model_validator(mode="after")
    def valid_record(self) -> CandidateOutcome:
        if (self.status == "accepted") != (self.record is not None):
            raise ValueError("only accepted outcomes contain published records")
        if self.record is not None and (self.record.origin == "human" or self.record.reviewed):
            raise ValueError("automatic outcomes cannot claim human review")
        return self


class CurationPlan(ContractModel):
    """Source assignments frozen independently of any generated model response."""

    schemaVersion: SchemaVersion = 1
    configurationSha256: Digest
    catalogSha256: Digest
    records: list[ImportedRecord]
    frozenFamilies: dict[Id, FrozenFamilyAssignment]
    excludedCounts: dict[str, NonNegativeInt]

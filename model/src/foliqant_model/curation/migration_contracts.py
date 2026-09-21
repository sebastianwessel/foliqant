"""Contracts for explicit offline dataset migration and isolated remaining work."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..contracts.base import ContractModel, Digest, Id, LocalPath, NonEmptyStr, SchemaVersion
from ..contracts.inputs import DataRecord, FrozenFamilyAssignment, ResolvedSourceDeclaration
from .contracts import CurationConfig
from .decision_seeds import DecisionSeed
from .endpoint import EndpointModelIdentity


class MigrationSource(ContractModel):
    """Original dataset identity and annotation record behind a derived row."""

    datasetId: Id
    recordId: Id
    originalId: NonEmptyStr
    snapshotSha256: Digest
    rightsSourceId: Id


class MigrationProvenance(ContractModel):
    """Explain derivation without rewriting historical generation provenance."""

    recordId: Id
    operation: Literal["retained", "reprojected", "source-projection", "question-variant", "rerun"]
    evidenceStatus: Literal[
        "historical-accepted",
        "authored-reference",
        "source-annotation",
        "deterministic-computation",
        "derived-reference",
        "model-verified",
    ]
    parentRecordIds: list[Id]
    sources: list[MigrationSource] = Field(min_length=1)
    rule: NonEmptyStr


class MigrationEntry(ContractModel):
    record: DataRecord
    provenance: MigrationProvenance

    @model_validator(mode="after")
    def matching_record(self) -> MigrationEntry:
        if self.record.id != self.provenance.recordId:
            raise ValueError("migration provenance must identify its record")
        return self


class MigrationPending(ContractModel):
    """A train-family task explicitly selected for a fresh bounded attempt."""

    seed: DecisionSeed
    reason: NonEmptyStr
    previousJobId: Digest | None = None
    sources: list[MigrationSource] = Field(min_length=1)


class MigrationReview(ContractModel):
    """Excluded or disputed material retained for inspection, without label chasing."""

    recordId: Id
    reason: NonEmptyStr
    previousJobId: Digest | None = None


class MigrationPlan(ContractModel):
    """Frozen migration inputs, transformed rows and the exact remaining queue."""

    schemaVersion: SchemaVersion = 1
    recipeSha256: Digest
    parentRun: LocalPath
    parentArtifactId: Digest
    parentFiles: dict[NonEmptyStr, Digest]
    config: CurationConfig
    model: EndpointModelIdentity
    sources: list[ResolvedSourceDeclaration]
    families: dict[Id, FrozenFamilyAssignment]
    entries: list[MigrationEntry]
    pending: list[MigrationPending]
    review: list[MigrationReview]
    projectionCounts: dict[NonEmptyStr, int]

    @model_validator(mode="after")
    def valid_membership(self) -> MigrationPlan:
        ids = [entry.record.id for entry in self.entries]
        pending_ids = [item.seed.parent.id for item in self.pending]
        if len(ids) != len(set(ids)) or len(pending_ids) != len(set(pending_ids)):
            raise ValueError("migration records and pending tasks must be unique")
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("migration source rights must be unique")
        for entry in self.entries:
            if entry.record.familyId not in self.families:
                raise ValueError("migration row must keep a frozen family")
            if entry.record.sourceId not in source_ids:
                raise ValueError("migration row requires source rights")
            if any(link.rightsSourceId not in source_ids for link in entry.provenance.sources):
                raise ValueError("migration provenance requires source rights")
        for item in self.pending:
            family = item.seed.parent.familyId
            if family not in self.families or self.families[family].split != "train":
                raise ValueError("only frozen training families may enter the rerun queue")
            if item.seed.parent.id in ids:
                raise ValueError("pending task cannot already be a published migration row")
            if item.seed.parent.sourceId not in source_ids or any(
                link.rightsSourceId not in source_ids for link in item.sources
            ):
                raise ValueError("pending task requires original source rights")
        if set(pending_ids) & {item.recordId for item in self.review}:
            raise ValueError("review tasks cannot enter the pending queue")
        return self

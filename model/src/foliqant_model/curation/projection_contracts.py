"""Strict contracts for immutable offline source-projection plans."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.base import (
    ContractModel,
    Digest,
    Id,
    LocalPath,
    NonEmptyStr,
    NonNegativeInt,
    SchemaVersion,
)
from ..contracts.inputs import FrozenFamilyAssignment, ResolvedSourceDeclaration
from .decision_seeds import DecisionSeed


class ProjectionSourceCounts(ContractModel):
    """Audit and selection counts for one frozen auxiliary source."""

    rawRecords: NonNegativeInt
    eligibleRecords: NonNegativeInt
    eligibleMultiIntentRecords: NonNegativeInt
    selectedRecords: NonNegativeInt
    selectedMultiIntentRecords: NonNegativeInt
    selectedFamilies: NonNegativeInt
    exclusionCounts: dict[NonEmptyStr, NonNegativeInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_multi_intent_counts(self) -> ProjectionSourceCounts:
        if self.eligibleMultiIntentRecords > self.eligibleRecords:
            raise ValueError("eligible multi-intent records cannot exceed eligible records")
        if self.selectedMultiIntentRecords > self.selectedRecords:
            raise ValueError("selected multi-intent records cannot exceed selected records")
        if self.selectedMultiIntentRecords > self.eligibleMultiIntentRecords:
            raise ValueError("selected multi-intent records cannot exceed eligible availability")
        return self


class ProjectionReport(ContractModel):
    """Complete deterministic audit summary for one projection plan."""

    rawRecords: NonNegativeInt
    eligibleRecords: NonNegativeInt
    selectedRecords: NonNegativeInt
    selectedFamilies: NonNegativeInt
    sourceCounts: dict[Id, ProjectionSourceCounts]
    selectedSourceCounts: dict[Id, NonNegativeInt]
    eligibleLanguageCounts: dict[NonEmptyStr, NonNegativeInt]
    selectedLanguageCounts: dict[NonEmptyStr, NonNegativeInt]
    eligibleSplitCounts: dict[NonEmptyStr, NonNegativeInt]
    selectedSplitCounts: dict[NonEmptyStr, NonNegativeInt]
    familyCounts: dict[Id, NonNegativeInt]
    questionTypeCounts: dict[NonEmptyStr, NonNegativeInt]
    selectedQuestionTypeCounts: dict[NonEmptyStr, NonNegativeInt]
    exclusionCounts: dict[NonEmptyStr, NonNegativeInt]

    @model_validator(mode="after")
    def validate_totals(self) -> ProjectionReport:
        if self.rawRecords != sum(item.rawRecords for item in self.sourceCounts.values()):
            raise ValueError("raw source counts must match the report total")
        if self.eligibleRecords != sum(item.eligibleRecords for item in self.sourceCounts.values()):
            raise ValueError("eligible source counts must match the report total")
        if self.selectedRecords != sum(item.selectedRecords for item in self.sourceCounts.values()):
            raise ValueError("selected source counts must match the report total")
        if self.selectedRecords != sum(self.selectedSourceCounts.values()):
            raise ValueError("selected source totals must match selectedRecords")
        if self.selectedRecords != sum(self.selectedLanguageCounts.values()):
            raise ValueError("selected language totals must match selectedRecords")
        if self.selectedRecords != sum(self.selectedSplitCounts.values()):
            raise ValueError("selected split totals must match selectedRecords")
        if self.selectedRecords != sum(self.familyCounts.values()):
            raise ValueError("selected family totals must match selectedRecords")
        if self.selectedFamilies != len(self.familyCounts):
            raise ValueError("selectedFamilies must match familyCounts")
        if self.exclusionCounts != dict(
            sorted(
                (
                    reason,
                    sum(item.exclusionCounts.get(reason, 0) for item in self.sourceCounts.values()),
                )
                for reason in {
                    reason for item in self.sourceCounts.values() for reason in item.exclusionCounts
                }
            )
        ):
            raise ValueError("source exclusions must match the global exclusion counts")
        return self


class ProjectionPlan(ContractModel):
    """New native seeds derived offline from one immutable curation snapshot."""

    schemaVersion: SchemaVersion = 1
    projectionVersion: NonEmptyStr
    selection: Literal["full", "pilot"]
    parentInputsSha256: Digest
    sourcePlanSha256: Digest
    configSha256: Digest
    parentRun: LocalPath
    sourceSnapshots: dict[Id, Digest]
    seeds: list[DecisionSeed]
    frozenFamilies: dict[Id, FrozenFamilyAssignment]
    sources: list[ResolvedSourceDeclaration]
    report: ProjectionReport

    @model_validator(mode="after")
    def validate_membership(self) -> ProjectionPlan:
        if list(self.sourceSnapshots) != sorted(self.sourceSnapshots):
            raise ValueError("sourceSnapshots must be sorted")
        if list(self.frozenFamilies) != sorted(self.frozenFamilies):
            raise ValueError("frozenFamilies must be sorted")
        seed_ids = [seed.parent.id for seed in self.seeds]
        if seed_ids != sorted(seed_ids) or len(seed_ids) != len(set(seed_ids)):
            raise ValueError("projection seeds must be sorted and unique")
        source_ids = [source.id for source in self.sources]
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            raise ValueError("projection sources must be sorted and unique")
        family_counts: Counter[str] = Counter()
        selected_sources: Counter[str] = Counter()
        selected_languages: Counter[str] = Counter()
        selected_splits: Counter[str] = Counter()
        selected_question_types: Counter[str] = Counter()
        for seed in self.seeds:
            family = seed.parent.familyId
            if family is None or family not in self.frozenFamilies:
                raise ValueError("every projection seed requires a frozen family")
            if seed.parent.sourceId not in source_ids:
                raise ValueError("every projection seed requires source rights")
            family_counts[family] += 1
            selected_sources[seed.parent.sourceId.removeprefix("native-")] += 1
            selected_languages[seed.parent.language] += 1
            selected_splits[self.frozenFamilies[family].split] += 1
            selected_question_types.update(question.type for question in seed.input.questions)
        if len(self.seeds) != self.report.selectedRecords:
            raise ValueError("projection seed count must match the report")
        if len(self.frozenFamilies) != self.report.selectedFamilies:
            raise ValueError("projection family count must match the report")
        if set(self.frozenFamilies) != set(family_counts):
            raise ValueError("projection families must be used by the new seeds")
        if self.report.familyCounts != dict(sorted(family_counts.items())):
            raise ValueError("reported family counts must match the new seeds")
        if self.report.selectedSourceCounts != dict(sorted(selected_sources.items())):
            raise ValueError("reported source counts must match the new seeds")
        if self.report.selectedLanguageCounts != dict(sorted(selected_languages.items())):
            raise ValueError("reported language counts must match the new seeds")
        if self.report.selectedSplitCounts != dict(sorted(selected_splits.items())):
            raise ValueError("reported split counts must match the new seeds")
        if self.report.selectedQuestionTypeCounts != dict(sorted(selected_question_types.items())):
            raise ValueError("reported question types must match the new seeds")
        return self

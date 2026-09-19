"""Deterministic family planning before any local generation request."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..contracts.base import Split, canonical_digest
from ..contracts.inputs import DataRecord, FrozenFamilyAssignment
from ..errors import ModelError
from .contracts import CurationPlan, ImportedRecord, SourceBatch

_SPLIT_STRENGTH = {
    "unspecified": 0,
    "train": 1,
    "validation": 2,
    "calibration": 3,
    "test": 4,
}


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self._parents = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parents[value]
        if parent != value:
            self._parents[value] = self.find(parent)
        return self._parents[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self._parents[second] = first


def freeze_sources(
    batches: list[SourceBatch], *, seed: int, configuration_digest: str, catalog_digest: str
) -> CurationPlan:
    """Keep official holdouts held out and freeze connected families before augmentation."""

    rows = sorted(
        (row for batch in batches for row in batch.records),
        key=lambda row: (row.record.id, row.record.sourceId, row.originalId),
    )
    ids = [row.record.id for row in rows]
    if len(ids) != len(set(ids)):
        raise ModelError("DATA_DUPLICATE_ID", "Imported source record IDs collide")

    excluded: Counter[str] = Counter()
    for batch in batches:
        excluded.update(batch.excludedCounts)

    # Build connected components from every observed row. Duplicate collapse must
    # happen later so an official holdout duplicate can constrain a training row.
    union = _UnionFind(ids)
    owners: dict[tuple[str, str], str] = {}
    conversations: dict[str, str] = {}
    for row in rows:
        record = row.record
        conversation = canonical_digest(
            [message.model_dump(mode="json") for message in record.messages]
        )
        prompt = canonical_digest(
            [message.model_dump(mode="json") for message in record.messages[:-1]]
        )
        conversations[record.id] = conversation
        keys = [("group", key) for key in record.groupKeys]
        if record.familyId is not None:
            keys.append(("family", record.familyId))
        keys.extend((("prompt", prompt), ("conversation", conversation)))
        for key in keys:
            previous = owners.setdefault(key, record.id)
            union.union(previous, record.id)

    components: dict[str, list[ImportedRecord]] = defaultdict(list)
    for row in rows:
        components[union.find(row.record.id)].append(row)
    if len(components) < 4:
        raise ModelError("DATA_PARTITION_INVALID", "Curation requires four independent families")

    grouped = sorted(
        (sorted(group, key=lambda row: row.record.id) for group in components.values()),
        key=lambda group: canonical_digest(
            {"seed": seed, "records": [row.record.id for row in group]}
        ),
    )
    families: dict[str, FrozenFamilyAssignment] = {}
    assigned: list[ImportedRecord] = []
    flexible: list[str] = []

    for index, group in enumerate(grouped):
        family = (
            "family-"
            + canonical_digest(sorted({row.record.familyId or row.record.id for row in group}))[:48]
        )
        observed = {row.originalSplit for row in group}
        split: Split
        if "test" in observed:
            split = "test"
        elif "calibration" in observed:
            split = "calibration"
        elif "validation" in observed:
            # Both destinations remain held out from generation and training.
            split = "calibration" if index % 2 else "validation"
        else:
            # This allocation is frozen once; generated variants inherit it.
            split = (
                "validation" if index % 10 == 0 else ("calibration" if index % 10 == 1 else "train")
            )
            flexible.append(family)

        families[family] = FrozenFamilyAssignment(
            split=split,
            sourceSplits=sorted({row.record.sourceId + ":" + row.originalSplit for row in group}),
        )

        # Collapse exact conversations only after all rows contributed grouping
        # and split constraints. Keep the strongest original holdout as the
        # representative, then use record ID as the deterministic tie-breaker.
        by_conversation: dict[str, list[ImportedRecord]] = defaultdict(list)
        for row in group:
            by_conversation[conversations[row.record.id]].append(row)
        for duplicates in by_conversation.values():
            representative = min(
                duplicates,
                key=lambda row: (-_SPLIT_STRENGTH[row.originalSplit], row.record.id),
            )
            excluded["duplicate-conversation"] += len(duplicates) - 1
            record = DataRecord.model_validate(
                {**representative.record.model_dump(mode="json"), "familyId": family}
            )
            assigned.append(
                ImportedRecord.model_validate(
                    {
                        **representative.model_dump(mode="json"),
                        "record": record.model_dump(mode="json"),
                    }
                )
            )

    # Small configured samples still need four partitions. Only original
    # training/unspecified families may fill a missing partition. A donor must
    # retain at least one family, so no earlier partition is emptied.
    for missing in ("train", "validation", "calibration", "test"):
        counts = Counter(value.split for value in families.values())
        if counts[missing]:
            continue
        candidate = next(
            (key for key in flexible if counts[families[key].split] > 1),
            None,
        )
        if candidate is None:
            raise ModelError(
                "DATA_PARTITION_INVALID", "Source splits cannot form four safe partitions"
            )
        families[candidate] = FrozenFamilyAssignment.model_validate(
            {**families[candidate].model_dump(mode="json"), "split": missing}
        )

    return CurationPlan(
        configurationSha256=configuration_digest,
        catalogSha256=catalog_digest,
        records=sorted(assigned, key=lambda row: row.record.id),
        frozenFamilies={key: families[key] for key in sorted(families)},
        excludedCounts=dict(sorted(excluded.items())),
    )

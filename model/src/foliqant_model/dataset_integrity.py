"""Standalone semantic verification for retained dataset artifact content."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from .contracts.base import LeakageEntry, SourceRight
from .contracts.details import DatasetDetails, PartitionCounts, RecordAssignment
from .contracts.inputs import DataRecord, ResolvedDatasetConfig
from .errors import ModelError

_SPLITS = ("train", "validation", "calibration", "test")


def _failure(message: str) -> ModelError:
    return ModelError("INTEGRITY_FAILED", message)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_digest(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _parse_json(raw: bytes, label: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(value)

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _failure(f"{label} is not strict UTF-8 JSON") from error


def _jsonl_rows(data: bytes, label: str) -> list[object]:
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise _failure(f"{label} must end every row with LF")
    rows: list[object] = []
    for raw in data[:-1].split(b"\n"):
        if not raw:
            raise _failure(f"{label} contains an empty row")
        value = _parse_json(raw, label)
        if raw != _canonical_bytes(value):
            raise _failure(f"{label} row is not canonical JSON")
        rows.append(value)
    return rows


@dataclass(frozen=True)
class _RecordContent:
    record: DataRecord
    messages: list[dict[str, object]]
    conversation_bytes: bytes
    conversation_hash: str
    prompt_bytes: bytes
    prompt_hash: str
    group_key_hashes: list[str]


def _record_content(value: object) -> _RecordContent:
    try:
        record = DataRecord.model_validate(value, strict=True)
    except ValidationError as error:
        raise _failure("retained dataset record does not satisfy its contract") from error
    for message in record.messages:
        normalized = unicodedata.normalize(
            "NFC", message.content.replace("\r\n", "\n").replace("\r", "\n")
        )
        if normalized != message.content:
            raise _failure("retained dataset record is not text-normalized")
    messages = [message.model_dump(mode="json") for message in record.messages]
    conversation = _canonical_bytes(messages)
    prompt = _canonical_bytes(messages[:-1])
    group_key_hashes = {_digest_bytes(key.encode("utf-8")) for key in record.groupKeys}
    if record.familyId is not None:
        group_key_hashes.add(_digest_bytes(b"family\0" + record.familyId.encode("utf-8")))
    return _RecordContent(
        record=record,
        messages=messages,
        conversation_bytes=conversation,
        conversation_hash=_digest_bytes(conversation),
        prompt_bytes=prompt,
        prompt_hash=_digest_bytes(prompt),
        group_key_hashes=sorted(group_key_hashes),
    )


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
        if left_root < right_root:
            self._parents[right_root] = left_root
        else:
            self._parents[left_root] = right_root


def _components(records: list[_RecordContent]) -> list[list[_RecordContent]]:
    union = _UnionFind([item.record.id for item in records])
    owners: dict[tuple[str, str], str] = {}
    for item in records:
        keys = [("group", key) for key in item.record.groupKeys]
        if item.record.familyId is not None:
            keys.append(("family", item.record.familyId))
        keys.append(("conversation", item.conversation_hash))
        for key in keys:
            owner = owners.get(key)
            if owner is None:
                owners[key] = item.record.id
            else:
                union.union(owner, item.record.id)
    grouped: dict[str, list[_RecordContent]] = defaultdict(list)
    for item in records:
        grouped[union.find(item.record.id)].append(item)
    return [sorted(component, key=lambda item: item.record.id) for component in grouped.values()]


def _expected_assignments(
    records: list[_RecordContent], config: ResolvedDatasetConfig
) -> dict[str, RecordAssignment]:
    components = _components(records)
    frozen = config.frozenFamilies
    component_splits: dict[str, str] = {}
    if frozen is None:
        if len(components) < 4:
            raise _failure("retained dataset has fewer than four components")
        components.sort(
            key=lambda component: _digest_bytes(
                str(config.seed).encode("utf-8") + b"\0" + component[0].record.id.encode("utf-8")
            )
        )
        component_count = len(components)
        sizes = {
            "validation": max(1, int(component_count * config.validationFraction)),
            "calibration": max(1, int(component_count * config.calibrationFraction)),
            "test": max(1, int(component_count * config.testFraction)),
        }
        sizes["train"] = (
            component_count - sizes["validation"] - sizes["calibration"] - sizes["test"]
        )
        if sizes["train"] <= 0:
            raise _failure("retained dataset split settings leave no training component")
        offset = 0
        for split in ("validation", "calibration", "test", "train"):
            for component in components[offset : offset + sizes[split]]:
                component_splits[component[0].record.id] = split
            offset += sizes[split]
    else:
        split_counts: Counter[str] = Counter()
        for component in components:
            splits = {
                frozen[item.record.familyId].split
                for item in component
                if item.record.familyId is not None
            }
            if len(splits) != 1:
                raise _failure("retained component spans frozen family splits")
            split = next(iter(splits))
            component_splits[component[0].record.id] = split
            split_counts[split] += 1
        if len(components) < 4:
            raise _failure("retained dataset has fewer than four components")
        if any(split_counts[split] == 0 for split in _SPLITS):
            raise _failure("retained frozen dataset has an empty split")
    assignments: dict[str, RecordAssignment] = {}
    for component in components:
        split = component_splits[component[0].record.id]
        component_id = _canonical_digest([item.record.id for item in component])
        for item in component:
            group_ids = {
                _digest_bytes(b"group\0" + key.encode("utf-8")) for key in item.record.groupKeys
            }
            if item.record.familyId is not None:
                group_ids.add(_digest_bytes(b"family\0" + item.record.familyId.encode("utf-8")))
            group_ids.add(_digest_bytes(b"conversation\0" + item.conversation_bytes))
            group_ids.add(_digest_bytes(b"prompt\0" + item.prompt_bytes))
            assignments[item.record.id] = RecordAssignment.model_validate(
                {
                    "split": split,
                    "componentId": component_id,
                    "groupIds": sorted(group_ids),
                }
            )
    return {key: assignments[key] for key in sorted(assignments)}


def _validate_frozen_families(records: list[_RecordContent], config: ResolvedDatasetConfig) -> None:
    frozen = config.frozenFamilies
    if frozen is None:
        if any(item.record.familyId is not None for item in records):
            raise _failure("records with familyId require frozenFamilies")
        return
    represented: set[str] = set()
    by_id = {item.record.id: item.record for item in records}
    for item in records:
        record = item.record
        if record.familyId is None or record.familyId not in frozen:
            raise _failure("every retained record must reference a frozen family")
        represented.add(record.familyId)
        generation = record.generation
        if generation is None:
            continue
        for parent_id in generation.parentRecordIds:
            parent = by_id.get(parent_id)
            if parent is None:
                raise _failure("generation parent is absent from retained records")
            if parent.familyId != record.familyId:
                raise _failure("generation parent belongs to another family")
    if represented != set(frozen):
        raise _failure("frozen family map does not exactly cover retained records")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str) -> None:
        if record_id in visited:
            return
        if record_id in visiting:
            raise _failure("generation lineage contains a cycle")
        visiting.add(record_id)
        generation = by_id[record_id].generation
        if generation is not None:
            for parent_id in generation.parentRecordIds:
                visit(parent_id)
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in sorted(by_id):
        visit(record_id)


def _partition_counts(
    records: list[_RecordContent], assignments: Mapping[str, RecordAssignment]
) -> PartitionCounts:
    languages = Counter(item.record.language for item in records)
    sources = Counter(item.record.sourceId for item in records)
    origins = Counter(item.record.origin for item in records)
    return PartitionCounts.model_validate(
        {
            "records": len(records),
            "components": len({assignments[item.record.id].componentId for item in records}),
            "languages": {key: languages[key] for key in sorted(languages)},
            "sources": {key: sources[key] for key in sorted(sources)},
            "origins": {
                "human": origins["human"],
                "synthetic": origins["synthetic"],
                "teacher": origins["teacher"],
            },
        }
    )


def _same_model(left: object, right: object) -> bool:
    return left == right


def verify_dataset_contents(
    details: DatasetDetails,
    source_rights: list[SourceRight],
    files: Mapping[str, bytes],
) -> None:
    """Reconstruct and validate every semantic dataset identity from retained bytes."""

    records_by_split: dict[str, list[_RecordContent]] = {}
    all_records: list[_RecordContent] = []
    declared_sources = {source.id for source in details.datasetConfig.sources}
    for split in _SPLITS:
        record_ref = getattr(details.recordFiles, split)
        chat_ref = getattr(details.chatFiles, split)
        record_data = files[record_ref.path]
        chat_data = files[chat_ref.path]
        if (
            _digest_bytes(record_data) != record_ref.sha256
            or _digest_bytes(chat_data) != chat_ref.sha256
        ):
            raise _failure("dataset file reference digest does not match retained bytes")
        record_values = _jsonl_rows(record_data, f"{split} record file")
        chat_values = _jsonl_rows(chat_data, f"{split} chat file")
        if len(record_values) != record_ref.recordCount or len(chat_values) != chat_ref.recordCount:
            raise _failure("dataset partition file count does not match its reference")
        records = [_record_content(value) for value in record_values]
        ids = [item.record.id for item in records]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise _failure("dataset partition records must be sorted and unique by ID")
        if any(item.record.sourceId not in declared_sources for item in records):
            raise _failure("dataset record refers to an undeclared source")
        expected_chats = [{"messages": item.messages} for item in records]
        if chat_values != expected_chats:
            raise _failure("dataset chat projection does not match retained full records")
        records_by_split[split] = records
        all_records.extend(records)

    all_ids = [item.record.id for item in all_records]
    if len(all_ids) != len(set(all_ids)) or len(all_ids) > details.datasetConfig.maxRecords:
        raise _failure("dataset record IDs are duplicated or exceed maxRecords")
    conversation_hashes = [item.conversation_hash for item in all_records]
    if len(conversation_hashes) != len(set(conversation_hashes)):
        raise _failure("dataset contains duplicate normalized conversations")
    ordered_records = sorted(all_records, key=lambda item: item.record.id)
    _validate_frozen_families(ordered_records, details.datasetConfig)
    expected_assignments = _expected_assignments(ordered_records, details.datasetConfig)
    if not _same_model(details.assignments, expected_assignments):
        raise _failure("dataset assignments do not match deterministic component splitting")
    for split in _SPLITS:
        records = records_by_split[split]
        if any(expected_assignments[item.record.id].split != split for item in records):
            raise _failure("dataset record is stored in the wrong partition")
        expected_counts = _partition_counts(records, expected_assignments)
        if getattr(details.partitions, split) != expected_counts:
            raise _failure("dataset partition counts do not match retained records")

    source_counts = Counter(item.record.sourceId for item in all_records)
    if {summary.sourceId: summary.recordCount for summary in details.sourceFiles} != dict(
        source_counts
    ):
        raise _failure("dataset source record counts do not match retained records")
    expected_diagnostic = any(
        item.record.origin != "human" or not item.record.reviewed for item in all_records
    )
    if details.diagnostic != expected_diagnostic:
        raise _failure("dataset diagnostic flag does not match retained records")

    leakage_ref = details.leakageIndex.file
    leakage_data = files[leakage_ref.path]
    if _digest_bytes(leakage_data) != leakage_ref.sha256:
        raise _failure("dataset leakage digest does not match retained bytes")
    leakage_values = _jsonl_rows(leakage_data, "dataset leakage file")
    try:
        leakage = [LeakageEntry.model_validate(value, strict=True) for value in leakage_values]
    except ValidationError as error:
        raise _failure("dataset leakage row does not satisfy its contract") from error
    expected_leakage = [
        LeakageEntry(
            recordId=item.record.id,
            componentId=expected_assignments[item.record.id].componentId,
            groupKeyHashes=item.group_key_hashes,
            conversationHash=item.conversation_hash,
            promptHash=item.prompt_hash,
        )
        for item in ordered_records
    ]
    if leakage != expected_leakage or len(leakage) != leakage_ref.recordCount:
        raise _failure("dataset leakage index does not match retained records")

    assignments_payload = {
        key: value.model_dump(mode="json") for key, value in expected_assignments.items()
    }
    content_payload = {
        "normalizationVersion": details.normalizationVersion,
        "splitSettings": details.splitSettings.model_dump(mode="json"),
        "sourceRights": [right.model_dump(mode="json") for right in source_rights],
        "assignments": assignments_payload,
        "chatFileDigests": {split: getattr(details.chatFiles, split).sha256 for split in _SPLITS},
        "recordFileDigests": {
            split: getattr(details.recordFiles, split).sha256 for split in _SPLITS
        },
    }
    if details.datasetConfig.frozenFamilies is not None:
        content_payload["frozenFamilies"] = {
            key: value.model_dump(mode="json")
            for key, value in details.datasetConfig.frozenFamilies.items()
        }
    content_id = _canonical_digest(content_payload)
    if details.datasetContentId != content_id:
        raise _failure("dataset content identity does not match retained semantic content")

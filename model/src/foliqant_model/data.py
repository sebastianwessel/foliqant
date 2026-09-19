"""Deterministic preparation of local JSONL data into dataset artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .artifacts import (
    ArtifactTransaction,
    create_manifest,
    producer_identity,
    write_private_bytes,
)
from .configuration import load_config, parse_json
from .contracts.artifacts import ArtifactManifest
from .contracts.base import ConfigIdentity, InventoryFileRef, LeakageEntry, SourceRight
from .contracts.details import PartitionCounts, RecordAssignment, SourceFileSummary
from .contracts.inputs import (
    ChatMessage,
    DataRecord,
    DatasetConfig,
    ResolvedDatasetConfig,
    ResolvedSourceDeclaration,
    SourceDeclaration,
)
from .errors import ModelError

_SPLITS = ("train", "validation", "calibration", "test")


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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _PreparedRecord:
    record: DataRecord
    conversation_bytes: bytes
    conversation_hash: str
    prompt_hash: str
    group_key_hashes: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedInput:
    config: ResolvedDatasetConfig
    config_digest: str
    records: tuple[_PreparedRecord, ...]
    source_files: tuple[SourceFileSummary, ...]
    source_rights: tuple[SourceRight, ...]


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self._parent[right_root] = left_root
        else:
            self._parent[left_root] = right_root


def _normalize_record(record: DataRecord) -> DataRecord:
    messages = [
        ChatMessage(
            role=message.role,
            content=unicodedata.normalize(
                "NFC", message.content.replace("\r\n", "\n").replace("\r", "\n")
            ),
        )
        for message in record.messages
    ]
    values: dict[str, object] = {
        "schemaVersion": record.schemaVersion,
        "id": record.id,
        "sourceId": record.sourceId,
        "language": record.language,
        "groupKeys": list(record.groupKeys),
        "messages": messages,
        "tags": list(record.tags),
        "origin": record.origin,
        "reviewed": record.reviewed,
    }
    if record.familyId is not None:
        values["familyId"] = record.familyId
    if record.generation is not None:
        values["generation"] = record.generation
    return DataRecord.model_validate(values, strict=True)


def _prepare_record(value: object, expected_source: str) -> _PreparedRecord:
    try:
        record = DataRecord.model_validate(value, strict=True)
    except ValidationError as error:
        raise ModelError("DATA_RECORD_INVALID", "Data record does not match its schema") from error
    if record.sourceId != expected_source:
        raise ModelError("DATA_RECORD_INVALID", "Data record source does not match its declaration")
    record = _normalize_record(record)
    messages = [message.model_dump(mode="json") for message in record.messages]
    conversation = _canonical_bytes(messages)
    prompt = _canonical_bytes(messages[:-1])
    group_key_hashes = {_digest_bytes(key.encode("utf-8")) for key in record.groupKeys}
    if record.familyId is not None:
        group_key_hashes.add(_digest_bytes(b"family\0" + record.familyId.encode("utf-8")))
    return _PreparedRecord(
        record=record,
        conversation_bytes=conversation,
        conversation_hash=_digest_bytes(conversation),
        prompt_hash=_digest_bytes(prompt),
        group_key_hashes=tuple(sorted(group_key_hashes)),
    )


def _read_source(
    path: Path,
    *,
    source_id: str,
    max_record_bytes: int,
    remaining_records: int,
) -> tuple[list[_PreparedRecord], SourceFileSummary]:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Data source must be a regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        records: list[_PreparedRecord] = []
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ModelError("UNSAFE_ARTIFACT_PATH", "Data source changed before it was opened")
            while raw := stream.readline(max_record_bytes + 2):
                digest.update(raw)
                size += len(raw)
                if raw.endswith(b"\n"):
                    raw = raw[:-1]
                    if raw.endswith(b"\r"):
                        raw = raw[:-1]
                if len(raw) > max_record_bytes:
                    raise ModelError(
                        "DATA_RECORD_INVALID", "Data record exceeds the configured size limit"
                    )
                if not raw:
                    raise ModelError("DATA_RECORD_INVALID", "Data source contains an empty record")
                if len(records) >= remaining_records:
                    raise ModelError("DATA_PARTITION_INVALID", "Dataset exceeds maxRecords")
                try:
                    value = parse_json(raw.decode("utf-8", errors="strict"))
                except (UnicodeError, ModelError) as error:
                    raise ModelError(
                        "DATA_RECORD_INVALID", "Data source contains invalid JSON"
                    ) from error
                records.append(_prepare_record(value, source_id))
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino) != (
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_dev,
                opened.st_ino,
            ):
                raise ModelError("INTEGRITY_FAILED", "Data source changed while it was read")
    except ModelError:
        raise
    except FileNotFoundError as error:
        raise ModelError("INPUT_NOT_FOUND", "Data source does not exist") from error
    except OSError as error:
        raise ModelError("IO_FAILED", "Data source could not be read") from error
    if not records:
        raise ModelError("DATA_PARTITION_INVALID", "Every declared source must contain records")
    return records, SourceFileSummary(
        sourceId=source_id,
        sha256=digest.hexdigest(),
        size=size,
        recordCount=len(records),
    )


def _resolved_source(source: SourceDeclaration) -> ResolvedSourceDeclaration:
    payload = source.model_dump(mode="python", exclude={"path"})
    payload["restrictions"] = sorted(payload["restrictions"])
    return ResolvedSourceDeclaration.model_validate(payload)


def _source_right(source: ResolvedSourceDeclaration) -> SourceRight:
    payload = source.model_dump(mode="python")
    payload["sourceId"] = payload.pop("id")
    return SourceRight.model_validate(payload)


def _load_inputs(config_path: Path) -> _PreparedInput:
    config = load_config(config_path, DatasetConfig)
    resolved_sources = tuple(
        sorted((_resolved_source(source) for source in config.sources), key=lambda item: item.id)
    )
    resolved = ResolvedDatasetConfig.model_validate(
        {
            **config.model_dump(mode="python", exclude={"sources"}),
            "sources": [source.model_dump(mode="python") for source in resolved_sources],
        }
    )
    all_records: list[_PreparedRecord] = []
    summaries: list[SourceFileSummary] = []
    for source in sorted(config.sources, key=lambda item: item.id):
        source_path = Path(source.path)
        if not source_path.is_absolute():
            source_path = config_path.parent / source_path
        records, summary = _read_source(
            source_path,
            source_id=source.id,
            max_record_bytes=config.maxRecordBytes,
            remaining_records=config.maxRecords - len(all_records),
        )
        all_records.extend(records)
        summaries.append(summary)
    ids = [item.record.id for item in all_records]
    if len(ids) != len(set(ids)):
        raise ModelError("DATA_DUPLICATE_ID", "Dataset contains duplicate record IDs")
    conversations = [item.conversation_hash for item in all_records]
    if len(conversations) != len(set(conversations)):
        raise ModelError("DATA_DUPLICATE_CONVERSATION", "Dataset contains duplicate conversations")
    _validate_frozen_families(all_records, resolved)
    ordered_records = tuple(sorted(all_records, key=lambda item: item.record.id))
    rights = tuple(_source_right(source) for source in resolved_sources)
    config_payload = resolved.model_dump(mode="json")
    return _PreparedInput(
        config=resolved,
        config_digest=_canonical_digest(config_payload),
        records=ordered_records,
        source_files=tuple(summaries),
        source_rights=rights,
    )


def _validate_frozen_families(
    records: list[_PreparedRecord], config: ResolvedDatasetConfig
) -> None:
    frozen = config.frozenFamilies
    if frozen is None:
        if any(item.record.familyId is not None for item in records):
            raise ModelError(
                "DATA_PARTITION_INVALID", "Records with familyId require frozenFamilies"
            )
        return
    represented: set[str] = set()
    by_id = {item.record.id: item.record for item in records}
    for item in records:
        record = item.record
        if record.familyId is None or record.familyId not in frozen:
            raise ModelError(
                "DATA_PARTITION_INVALID", "Every record must reference a frozen family"
            )
        represented.add(record.familyId)
        generation = record.generation
        if generation is None:
            continue
        for parent_id in generation.parentRecordIds:
            parent = by_id.get(parent_id)
            if parent is None:
                raise ModelError(
                    "DATA_RECORD_INVALID", "Generation parent does not exist in the dataset"
                )
            if parent.familyId != record.familyId:
                raise ModelError(
                    "DATA_RECORD_INVALID", "Generation parent belongs to a different family"
                )
    if represented != set(frozen):
        raise ModelError("DATA_PARTITION_INVALID", "Every frozen family must be represented")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str) -> None:
        if record_id in visited:
            return
        if record_id in visiting:
            raise ModelError("DATA_RECORD_INVALID", "Generation lineage contains a cycle")
        visiting.add(record_id)
        generation = by_id[record_id].generation
        if generation is not None:
            for parent_id in generation.parentRecordIds:
                visit(parent_id)
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in sorted(by_id):
        visit(record_id)


def _components(records: tuple[_PreparedRecord, ...]) -> list[list[_PreparedRecord]]:
    union = _UnionFind([item.record.id for item in records])
    owners: dict[tuple[str, str], str] = {}
    for item in records:
        keys = [("group", key) for key in item.record.groupKeys]
        if item.record.familyId is not None:
            keys.append(("family", item.record.familyId))
        keys.append(("conversation", item.conversation_hash))
        for key in keys:
            previous = owners.get(key)
            if previous is None:
                owners[key] = item.record.id
            else:
                union.union(previous, item.record.id)
    grouped: dict[str, list[_PreparedRecord]] = defaultdict(list)
    for item in records:
        grouped[union.find(item.record.id)].append(item)
    return [sorted(component, key=lambda item: item.record.id) for component in grouped.values()]


def _component_id(component: list[_PreparedRecord]) -> str:
    return _canonical_digest([item.record.id for item in component])


def _assign(prepared: _PreparedInput) -> dict[str, RecordAssignment]:
    components = _components(prepared.records)
    frozen = prepared.config.frozenFamilies
    component_splits: dict[str, str] = {}
    if frozen is None:
        if len(components) < 4:
            raise ModelError("DATA_PARTITION_INVALID", "Dataset requires at least four components")
        components.sort(
            key=lambda component: _digest_bytes(
                str(prepared.config.seed).encode("utf-8")
                + b"\0"
                + component[0].record.id.encode("utf-8")
            )
        )
        count = len(components)
        sizes = {
            "validation": max(1, int(count * prepared.config.validationFraction)),
            "calibration": max(1, int(count * prepared.config.calibrationFraction)),
            "test": max(1, int(count * prepared.config.testFraction)),
        }
        sizes["train"] = count - sizes["validation"] - sizes["calibration"] - sizes["test"]
        if sizes["train"] <= 0:
            raise ModelError("DATA_PARTITION_INVALID", "Split settings leave no training component")
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
                raise ModelError(
                    "DATA_PARTITION_INVALID",
                    "A connected component spans frozen family splits",
                )
            split = next(iter(splits))
            component_splits[component[0].record.id] = split
            split_counts[split] += 1
        if len(components) < 4:
            raise ModelError("DATA_PARTITION_INVALID", "Dataset requires at least four components")
        if any(split_counts[split] == 0 for split in _SPLITS):
            raise ModelError("DATA_PARTITION_INVALID", "Every frozen split must be nonempty")

    assignments: dict[str, RecordAssignment] = {}
    for component in components:
        split = component_splits[component[0].record.id]
        identity = _component_id(component)
        for item in component:
            group_ids = {
                _digest_bytes(b"group\0" + key.encode("utf-8")) for key in item.record.groupKeys
            }
            if item.record.familyId is not None:
                group_ids.add(_digest_bytes(b"family\0" + item.record.familyId.encode("utf-8")))
            group_ids.add(_digest_bytes(b"conversation\0" + item.conversation_bytes))
            prompt_bytes = _canonical_bytes(
                [message.model_dump(mode="json") for message in item.record.messages[:-1]]
            )
            group_ids.add(_digest_bytes(b"prompt\0" + prompt_bytes))
            assignments[item.record.id] = RecordAssignment.model_validate(
                {
                    "split": split,
                    "componentId": identity,
                    "groupIds": sorted(group_ids),
                }
            )
    return {key: assignments[key] for key in sorted(assignments)}


def _jsonl(rows: Iterable[object]) -> bytes:
    return b"".join(_canonical_bytes(row) + b"\n" for row in rows)


def _partition_counts(records: list[_PreparedRecord], component_ids: set[str]) -> PartitionCounts:
    languages = Counter(item.record.language for item in records)
    sources = Counter(item.record.sourceId for item in records)
    origins = Counter(item.record.origin for item in records)
    return PartitionCounts.model_validate(
        {
            "records": len(records),
            "components": len(component_ids),
            "languages": {key: languages[key] for key in sorted(languages)},
            "sources": {key: sources[key] for key in sorted(sources)},
            "origins": {
                "human": origins["human"],
                "synthetic": origins["synthetic"],
                "teacher": origins["teacher"],
            },
        }
    )


def _file_ref(path: str, data: bytes, count: int) -> InventoryFileRef:
    return InventoryFileRef(
        path=path,
        sha256=_digest_bytes(data),
        recordCount=count,
        format="jsonl",
    )


def prepare_dataset(config_path: Path, output: Path) -> ArtifactManifest:
    """Validate, normalize, partition, and atomically publish one dataset artifact."""

    prepared = _load_inputs(config_path)
    assignments = _assign(prepared)
    by_split: dict[str, list[_PreparedRecord]] = {split: [] for split in _SPLITS}
    for item in prepared.records:
        by_split[assignments[item.record.id].split].append(item)

    chat_bytes: dict[str, bytes] = {}
    record_bytes: dict[str, bytes] = {}
    chat_refs: dict[str, InventoryFileRef] = {}
    record_refs: dict[str, InventoryFileRef] = {}
    partitions: dict[str, object] = {}
    for split in _SPLITS:
        records = by_split[split]
        chats = _jsonl(
            [
                {"messages": [message.model_dump(mode="json") for message in item.record.messages]}
                for item in records
            ]
        )
        full_records = _jsonl([item.record.model_dump(mode="json") for item in records])
        chat_path = f"chats/{split}.jsonl"
        record_path = f"records/{split}.jsonl"
        chat_bytes[split] = chats
        record_bytes[split] = full_records
        chat_refs[split] = _file_ref(chat_path, chats, len(records))
        record_refs[split] = _file_ref(record_path, full_records, len(records))
        component_ids = {assignments[item.record.id].componentId for item in records}
        partitions[split] = _partition_counts(records, component_ids).model_dump(mode="json")

    leakage_rows = [
        LeakageEntry(
            recordId=item.record.id,
            componentId=assignments[item.record.id].componentId,
            groupKeyHashes=list(item.group_key_hashes),
            conversationHash=item.conversation_hash,
            promptHash=item.prompt_hash,
        ).model_dump(mode="json")
        for item in prepared.records
    ]
    leakage_bytes = _jsonl(leakage_rows)
    leakage_ref = _file_ref("leakage.jsonl", leakage_bytes, len(leakage_rows))
    split_settings = {
        "seed": prepared.config.seed,
        "validationFraction": prepared.config.validationFraction,
        "calibrationFraction": prepared.config.calibrationFraction,
        "testFraction": prepared.config.testFraction,
    }
    rights_payload = [right.model_dump(mode="json") for right in prepared.source_rights]
    assignments_payload = {
        key: assignment.model_dump(mode="json") for key, assignment in assignments.items()
    }
    content_payload = {
        "normalizationVersion": 1,
        "splitSettings": split_settings,
        "sourceRights": rights_payload,
        "assignments": assignments_payload,
        "chatFileDigests": {split: chat_refs[split].sha256 for split in _SPLITS},
        "recordFileDigests": {split: record_refs[split].sha256 for split in _SPLITS},
    }
    if prepared.config.frozenFamilies is not None:
        content_payload["frozenFamilies"] = {
            key: value.model_dump(mode="json")
            for key, value in prepared.config.frozenFamilies.items()
        }
    dataset_content_id = _canonical_digest(content_payload)
    diagnostic = any(
        item.record.origin != "human" or not item.record.reviewed for item in prepared.records
    )
    fields: dict[str, object] = {
        "schemaVersion": 1,
        "kind": "dataset",
        "name": prepared.config.name,
        "createdAt": _timestamp(),
        "stage": "none",
        "parents": [],
        "producer": producer_identity("prepare").model_dump(mode="json"),
        "sourceRights": rights_payload,
        "details": {
            "datasetConfig": prepared.config.model_dump(mode="json"),
            "datasetConfigSha256": prepared.config_digest,
            "datasetContentId": dataset_content_id,
            "normalizationVersion": 1,
            "splitSettings": split_settings,
            "sourceFiles": [item.model_dump(mode="json") for item in prepared.source_files],
            "assignments": assignments_payload,
            "partitions": partitions,
            "chatFiles": {key: value.model_dump(mode="json") for key, value in chat_refs.items()},
            "recordFiles": {
                key: value.model_dump(mode="json") for key, value in record_refs.items()
            },
            "leakageIndex": {
                "file": leakage_ref.model_dump(mode="json"),
                "splits": ["calibration", "test", "train", "validation"],
                "entryCount": len(leakage_rows),
            },
            "diagnostic": diagnostic,
        },
    }
    transaction = ArtifactTransaction(
        output,
        "prepare",
        configuration=ConfigIdentity(kind="dataset", sha256=prepared.config_digest),
    )
    with transaction:
        chats_directory = transaction.staging_path / "chats"
        records_directory = transaction.staging_path / "records"
        chats_directory.mkdir(mode=0o700)
        records_directory.mkdir(mode=0o700)
        for split in _SPLITS:
            write_private_bytes(chats_directory / f"{split}.jsonl", chat_bytes[split])
            write_private_bytes(records_directory / f"{split}.jsonl", record_bytes[split])
        write_private_bytes(transaction.staging_path / "leakage.jsonl", leakage_bytes)
        manifest = create_manifest(transaction.staging_path, fields)
        transaction.publish(manifest)
    return manifest

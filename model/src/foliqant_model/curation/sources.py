"""Pinned public-source acquisition and deterministic record conversion."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import Field, ValidationError, model_validator

from ..acquisition import download_asset
from ..configuration import parse_json
from ..contracts.base import ContractModel, NonEmptyStr, SchemaVersion, canonical_digest
from ..contracts.inputs import ChatMessage, DataRecord, ResolvedSourceDeclaration
from ..contracts.setup import SetupAsset
from ..errors import ModelError
from .contracts import ImportedRecord, SourceBatch

_SourceId = Literal["banking77", "typed-decisions", "wanli", "multidogo-finance", "tatqa"]
_ConverterId = Literal[
    "banking77-v2",
    "typed-decisions-v2",
    "wanli-semif-v2",
    "multidogo-finance-v3",
    "tatqa-v2",
]
_SPLIT_PRIORITY = {"test": 0, "calibration": 1, "validation": 2, "train": 3, "unspecified": 4}


class _CatalogSource(ContractModel):
    converter: _ConverterId
    revision: NonEmptyStr
    source: ResolvedSourceDeclaration
    assets: Annotated[list[SetupAsset], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_assets(self) -> _CatalogSource:
        paths = [asset.path for asset in self.assets]
        if paths != sorted(set(paths)):
            raise ValueError("source assets must be sorted and unique")
        return self


class _SourceCatalog(ContractModel):
    schemaVersion: SchemaVersion
    sources: dict[_SourceId, _CatalogSource]

    @model_validator(mode="after")
    def validate_sources(self) -> _SourceCatalog:
        if list(self.sources) != sorted(self.sources):
            raise ValueError("catalog sources must be sorted")
        for source_id, entry in self.sources.items():
            if source_id != entry.source.id:
                raise ValueError("catalog source key and declaration must match")
        return self


def _catalog_bytes() -> bytes:
    return files("foliqant_model.curation").joinpath("source-catalog-v1.json").read_bytes()


def _load_catalog() -> _SourceCatalog:
    try:
        raw = _catalog_bytes().decode("utf-8", errors="strict")
        return _SourceCatalog.model_validate(parse_json(raw), strict=True)
    except (UnicodeError, ValidationError) as error:
        raise ModelError("CONFIG_INVALID", "Built-in source catalog is invalid") from error


def source_catalog_digest() -> str:
    """Return the exact SHA-256 identity of the packaged source catalog."""
    return hashlib.sha256(_catalog_bytes()).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _id(source_id: str, namespace: str, value: str) -> str:
    return f"{source_id}.{namespace}.{_hash(source_id, namespace, value)[:32]}"


def _family(source_id: str, value: str) -> str:
    return f"{source_id}.family.{_hash(source_id, 'family', value)[:32]}"


def _record(
    *,
    source_id: str,
    original_id: str,
    family_key: str,
    system: str,
    user: str,
    assistant: str,
    tags: list[str],
    origin: Literal["human", "synthetic", "teacher"],
) -> DataRecord:
    family_id = _family(source_id, family_key)
    return DataRecord(
        schemaVersion=1,
        id=_id(source_id, "record", original_id),
        sourceId=source_id,
        language="en",
        groupKeys=[f"{source_id}:family:{_hash(source_id, family_key)}"],
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
            ChatMessage(role="assistant", content=assistant),
        ],
        tags=tags,
        origin=origin,
        reviewed=False,
        familyId=family_id,
    )


def _read_json(path: Path) -> object:
    try:
        return parse_json(path.read_text(encoding="utf-8"))
    except ModelError as error:
        raise ModelError("DATA_RECORD_INVALID", "Pinned source JSON is invalid") from error
    except (OSError, UnicodeError) as error:
        raise ModelError("IO_FAILED", "Pinned source JSON could not be read") from error


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                value = parse_json(line)
                if not isinstance(value, dict):
                    raise ModelError("DATA_RECORD_INVALID", "Pinned JSONL row is not an object")
                rows.append(value)
    except ModelError:
        raise
    except (OSError, UnicodeError) as error:
        raise ModelError("IO_FAILED", "Pinned source JSONL could not be read") from error
    return rows


def _required_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ModelError("DATA_RECORD_INVALID", "Pinned source field is invalid")
    return value


def _required_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if type(value) is not int:
        raise ModelError("DATA_RECORD_INVALID", "Pinned source field is invalid")
    return value


def _deduplicate(records: list[ImportedRecord]) -> tuple[list[ImportedRecord], int, int]:
    by_conversation: dict[str, list[ImportedRecord]] = defaultdict(list)
    for item in records:
        key = canonical_digest(
            [message.model_dump(mode="json") for message in item.record.messages]
        )
        by_conversation[key].append(item)
    unique: list[ImportedRecord] = []
    collapsed = 0
    cross_family_excluded = 0
    for duplicates in by_conversation.values():
        families = {item.record.familyId for item in duplicates}
        if len(families) > 1:
            # Keeping one copy would discard the other family aliases and could
            # let related held-out records separate from their training aliases.
            cross_family_excluded += len(duplicates)
            continue
        representative = min(
            duplicates,
            key=lambda item: (_SPLIT_PRIORITY[item.originalSplit], item.record.id),
        )
        unique.append(representative)
        collapsed += len(duplicates) - 1
    return sorted(unique, key=lambda item: item.record.id), collapsed, cross_family_excluded


def _duplicate_counts(collapsed: int, cross_family_excluded: int) -> dict[str, int]:
    result: dict[str, int] = {}
    if collapsed:
        result["duplicate_payload"] = collapsed
    if cross_family_excluded:
        result["cross_family_duplicate_payload"] = cross_family_excluded
    return result


def _remove_cross_split_families(
    records: list[ImportedRecord],
) -> tuple[list[ImportedRecord], int]:
    by_family: dict[str, list[ImportedRecord]] = defaultdict(list)
    for item in records:
        if item.record.familyId is None:
            raise ModelError("DATA_RECORD_INVALID", "Imported source record has no family")
        by_family[item.record.familyId].append(item)
    kept: list[ImportedRecord] = []
    excluded = 0
    for family in by_family.values():
        retained_split = min(
            {item.originalSplit for item in family}, key=lambda split: _SPLIT_PRIORITY[split]
        )
        for item in family:
            if item.originalSplit == retained_split:
                kept.append(item)
            else:
                excluded += 1
    return sorted(kept, key=lambda item: item.record.id), excluded


def _grouped_sample(
    records: list[ImportedRecord],
    max_records: int,
    *,
    complete_test_when_possible: bool = False,
) -> list[ImportedRecord]:
    if len(records) <= max_records:
        return sorted(records, key=lambda item: item.record.id)
    groups: dict[str, list[ImportedRecord]] = defaultdict(list)
    for item in records:
        if item.record.familyId is None:
            raise ModelError("DATA_RECORD_INVALID", "Imported source record has no family")
        groups[item.record.familyId].append(item)
    for group in groups.values():
        if len({item.originalSplit for item in group}) != 1:
            raise ModelError("DATA_PARTITION_INVALID", "A source family crosses official splits")
        group.sort(key=lambda item: item.record.id)

    ranked = sorted(groups.values(), key=lambda group: _hash(group[0].record.familyId or ""))
    selected: list[list[ImportedRecord]] = []
    used: set[str] = set()
    remaining = max_records

    test_groups = [group for group in ranked if group[0].originalSplit == "test"]
    non_test_splits = {
        group[0].originalSplit for group in ranked if group[0].originalSplit != "test"
    }
    if complete_test_when_possible:
        test_size = sum(len(group) for group in test_groups)
        if test_size + len(non_test_splits) <= max_records:
            for group in test_groups:
                selected.append(group)
                used.add(group[0].record.familyId or "")
            remaining -= test_size

    represented = {group[0].originalSplit for group in selected}
    split_order = ["test", "calibration", "validation", "train", "unspecified"]
    for split in split_order:
        if split in represented:
            continue
        candidates = [
            group
            for group in ranked
            if group[0].originalSplit == split
            and (group[0].record.familyId or "") not in used
            and len(group) <= remaining
        ]
        if candidates:
            group = candidates[0]
            selected.append(group)
            used.add(group[0].record.familyId or "")
            remaining -= len(group)

    for group in ranked:
        family_id = group[0].record.familyId or ""
        if family_id not in used and len(group) <= remaining:
            selected.append(group)
            used.add(family_id)
            remaining -= len(group)
        if remaining == 0:
            break
    result = sorted((item for group in selected for item in group), key=lambda item: item.record.id)
    if not result:
        raise ModelError("DATA_PARTITION_INVALID", "Record cap cannot contain a complete family")
    return result


def _banking77(paths: dict[str, Path]) -> tuple[list[ImportedRecord], dict[str, int]]:
    categories_value = _read_json(paths["banking77/categories.json"])
    if not isinstance(categories_value, list) or not all(
        isinstance(category, str) and category for category in categories_value
    ):
        raise ModelError("DATA_RECORD_INVALID", "BANKING77 categories are invalid")
    categories = cast(list[str], categories_value)
    records: list[ImportedRecord] = []
    for split, filename in (("train", "banking77/train.csv"), ("test", "banking77/test.csv")):
        try:
            with paths[filename].open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != ["text", "category"]:
                    raise ModelError("DATA_RECORD_INVALID", "BANKING77 columns are invalid")
                for index, row in enumerate(reader):
                    text = row.get("text", "")
                    category = row.get("category", "")
                    if not text or category not in categories:
                        raise ModelError("DATA_RECORD_INVALID", "BANKING77 row is invalid")
                    original_id = f"{split}:{index}"
                    data = _record(
                        source_id="banking77",
                        original_id=original_id,
                        family_key="query:" + _hash(text.strip().casefold()),
                        system=(
                            "Classify the online-banking request using the supplied "
                            "BANKING77 label set. Return only a JSON object with exactly one "
                            'field, {"intent":"<one supplied label>"}.'
                        ),
                        user=_json({"labels": categories, "request": text}),
                        assistant=_json({"intent": category}),
                        tags=["banking77", "intent-classification", f"source-split:{split}"],
                        origin="human",
                    )
                    records.append(
                        ImportedRecord(
                            record=data,
                            originalSplit=cast(Any, split),
                            task="classification",
                            originalId=original_id,
                        )
                    )
        except ModelError:
            raise
        except (OSError, UnicodeError, csv.Error) as error:
            raise ModelError("IO_FAILED", "BANKING77 CSV could not be read") from error
    unique, duplicates, cross_family = _deduplicate(records)
    return unique, _duplicate_counts(duplicates, cross_family)


def _typed_decisions(paths: dict[str, Path]) -> tuple[list[ImportedRecord], dict[str, int]]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as error:
        raise ModelError("DEPENDENCY_MISSING", "Parquet support is unavailable") from error
    records: list[ImportedRecord] = []
    expected = [
        "id",
        "workflow",
        "split",
        "state",
        "questions",
        "gold",
        "factors",
        "label_agreement",
        "n_questions",
    ]
    for split, filename in (
        ("train", "typed-decisions/train.parquet"),
        ("test", "typed-decisions/test.parquet"),
    ):
        try:
            table = pq.read_table(paths[filename])
            if table.column_names != expected:
                raise ModelError("DATA_RECORD_INVALID", "Typed-decisions columns are invalid")
            rows = cast(list[dict[str, object]], table.to_pylist())
        except ModelError:
            raise
        except Exception as error:
            raise ModelError("DATA_RECORD_INVALID", "Typed-decisions parquet is invalid") from error
        for row in rows:
            row_id = _required_str(row, "id")
            workflow = _required_str(row, "workflow")
            row_split = _required_str(row, "split")
            if row_split != split or workflow not in {
                "agent_trace_observability",
                "customer_service",
                "invoice_processing",
                "security_incidents",
            }:
                raise ModelError("DATA_RECORD_INVALID", "Typed-decisions identity is invalid")
            state = parse_json(_required_str(row, "state"))
            questions = parse_json(_required_str(row, "questions"))
            gold = parse_json(_required_str(row, "gold"))
            if (
                not isinstance(state, dict)
                or not isinstance(questions, dict)
                or not isinstance(gold, dict)
            ):
                raise ModelError("DATA_RECORD_INVALID", "Typed-decisions JSON fields are invalid")
            original_id = f"{workflow}:{row_id}"
            data = _record(
                source_id="typed-decisions",
                original_id=original_id,
                family_key=original_id,
                system=(
                    "Predict the teacher distribution for every typed decision question over the "
                    "shared state. Return only one JSON object keyed by every question ID. Each "
                    "value must contain type, label, confidence, and probabilities; noul questions "
                    "also contain noul, and score questions also contain score. Do not claim human "
                    "adjudication."
                ),
                user=_json({"questions": questions, "state": state}),
                assistant=_json(gold),
                tags=[
                    "synthetic-state",
                    "teacher-reference",
                    "typed-decisions",
                    "augmentation-ineligible:soft-target",
                    f"workflow:{workflow}",
                    f"source-split:{split}",
                ],
                origin="teacher",
            )
            records.append(
                ImportedRecord(
                    record=data,
                    originalSplit=cast(Any, split),
                    task="decision",
                    originalId=original_id,
                )
            )
    unique, duplicates, cross_family = _deduplicate(records)
    return unique, _duplicate_counts(duplicates, cross_family)


def _wanli(paths: dict[str, Path]) -> tuple[list[ImportedRecord], dict[str, int]]:
    train_rows = _read_jsonl(paths["wanli/train.jsonl"])
    test_rows = _read_jsonl(paths["wanli/test.jsonl"])
    test_by_id = {str(_required_int(row, "id")): row for row in test_rows}
    selection = _read_jsonl(paths["wanli/semif/source-selection.jsonl"])
    selected_items = [item for item in selection if item.get("source") == "wanli"]
    if len(selected_items) != 256:
        raise ModelError("DATA_RECORD_INVALID", "SemIf WANLI selection must contain 256 rows")
    selected_rows: list[tuple[dict[str, object], list[str]]] = []
    for item in selected_items:
        upstream = item.get("upstream")
        option_ids = item.get("option_ids")
        if (
            not isinstance(upstream, dict)
            or not isinstance(option_ids, list)
            or not all(isinstance(option, str) for option in option_ids)
        ):
            raise ModelError("DATA_RECORD_INVALID", "SemIf WANLI selection row is invalid")
        if upstream.get("revision") != "61c95318fd71c55b6ba355d76253254615f387ec":
            raise ModelError("DATA_RECORD_INVALID", "SemIf WANLI revision is invalid")
        source_id = upstream.get("source_id")
        row = test_by_id.get(str(source_id))
        if row is None:
            raise ModelError("DATA_RECORD_INVALID", "SemIf WANLI source row is missing")
        selected_rows.append((row, cast(list[str], option_ids)))
    selected_pair_ids = {_required_str(row, "pairID") for row, _ in selected_rows}

    descriptions = {
        "supported": "The evidence establishes the claim",
        "insufficient": "The evidence does not establish either",
        "contradicted": "The evidence establishes the opposite",
    }
    labels = {"entailment": "supported", "neutral": "insufficient", "contradiction": "contradicted"}
    overlap_excluded = 0
    records: list[ImportedRecord] = []

    def append_row(row: dict[str, object], split: str, option_ids: list[str], semif: bool) -> None:
        row_id = _required_int(row, "id")
        premise = _required_str(row, "premise")
        hypothesis = _required_str(row, "hypothesis")
        gold = _required_str(row, "gold")
        pair_id = _required_str(row, "pairID")
        genre = _required_str(row, "genre")
        if gold not in labels or sorted(option_ids) != sorted(descriptions):
            raise ModelError("DATA_RECORD_INVALID", "WANLI label data is invalid")
        original_id = f"{split}:{row_id}"
        data = _record(
            source_id="wanli",
            original_id=original_id,
            family_key="seed:" + pair_id,
            system=(
                "Assess the claim using only the supplied evidence. Return only a JSON object "
                'with exactly one field, {"label":"<one supplied option id>"}.'
            ),
            user=_json(
                {
                    "claim": hypothesis,
                    "evidence": premise,
                    "options": [
                        {"id": option, "description": descriptions[option]} for option in option_ids
                    ],
                }
            ),
            assistant=_json({"label": labels[gold]}),
            tags=[
                "natural-language-inference",
                "wanli",
                f"genre:{genre}",
                f"source-split:{split}",
                *(["semif-selection"] if semif else []),
            ],
            origin="synthetic",
        )
        records.append(
            ImportedRecord(
                record=data,
                originalSplit=cast(Any, split),
                task="entailment",
                originalId=original_id,
            )
        )

    for row in train_rows:
        pair_id = _required_str(row, "pairID")
        if pair_id in selected_pair_ids:
            overlap_excluded += 1
            continue
        row_id = _required_int(row, "id")
        ordered = sorted(descriptions, key=lambda option: _hash(str(row_id), option))
        append_row(row, "train", ordered, False)
    for row, option_ids in selected_rows:
        append_row(row, "test", option_ids, True)
    unique, duplicates, cross_family = _deduplicate(records)
    excluded = {"train_seed_family_overlap_with_semif_test": overlap_excluded}
    excluded.update(_duplicate_counts(duplicates, cross_family))
    return unique, excluded


_SENSITIVE_SLOTS = {
    "account_number",
    "address",
    "card_number",
    "name",
    "ssn",
    "target_account_number",
}


def _multidogo(paths: dict[str, Path]) -> tuple[list[ImportedRecord], dict[str, int]]:
    records: list[ImportedRecord] = []
    invalid_alignment = 0
    for split, original_split, filename in (
        ("train", "train", "multidogo-finance/train.tsv"),
        ("dev", "validation", "multidogo-finance/dev.tsv"),
        ("test", "test", "multidogo-finance/test.tsv"),
    ):
        try:
            with paths[filename].open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                expected = [
                    "conversationId",
                    "turnNumber",
                    "utteranceId",
                    "utterance",
                    "slot-labels",
                    "intent",
                ]
                if reader.fieldnames != expected:
                    raise ModelError("DATA_RECORD_INVALID", "MultiDoGO columns are invalid")
                for row in reader:
                    conversation = row.get("conversationId", "")
                    utterance_id = row.get("utteranceId", "")
                    utterance = row.get("utterance", "")
                    slot_labels = row.get("slot-labels", "").split()
                    tokens = utterance.split()
                    intents = row.get("intent", "").split("<div>")
                    if not conversation or not utterance_id or not all(intents):
                        raise ModelError("DATA_RECORD_INVALID", "MultiDoGO row identity is invalid")
                    if len(tokens) != len(slot_labels):
                        invalid_alignment += 1
                        continue
                    # Keep token alignment without putting the target slot type in the input.
                    redacted_tokens = [
                        "<redacted>" if label in _SENSITIVE_SLOTS else token
                        for token, label in zip(tokens, slot_labels, strict=True)
                    ]
                    original_id = f"{split}:{utterance_id}"
                    data = _record(
                        source_id="multidogo-finance",
                        original_id=original_id,
                        family_key="conversation:" + conversation,
                        system=(
                            "Classify every active intent in this finance customer turn and return "
                            "only a JSON object with exactly the fields intents and slotLabels. "
                            "intents is an array of active intent strings; slotLabels is an array "
                            "of one slot-label string per input token, in token order."
                        ),
                        user=_json({"utterance": " ".join(redacted_tokens)}),
                        assistant=_json({"intents": intents, "slotLabels": slot_labels}),
                        tags=[
                            "finance-dialogue",
                            "intent-classification",
                            "sensitive-slots-redacted",
                            "augmentation-ineligible:token-aligned",
                            f"source-split:{split}",
                        ],
                        origin="human",
                    )
                    records.append(
                        ImportedRecord(
                            record=data,
                            originalSplit=cast(Any, original_split),
                            task="classification",
                            originalId=original_id,
                        )
                    )
        except ModelError:
            raise
        except (OSError, UnicodeError, csv.Error) as error:
            raise ModelError("IO_FAILED", "MultiDoGO TSV could not be read") from error
    unique, duplicates, cross_family = _deduplicate(records)
    excluded: dict[str, int] = {}
    if invalid_alignment:
        excluded["invalid_slot_alignment"] = invalid_alignment
    excluded.update(_duplicate_counts(duplicates, cross_family))
    return unique, excluded


def _tatqa(paths: dict[str, Path]) -> tuple[list[ImportedRecord], dict[str, int]]:
    records: list[ImportedRecord] = []
    for split, original_split, filename in (
        ("train", "train", "tatqa/train.json"),
        ("dev", "validation", "tatqa/dev.json"),
        ("test", "test", "tatqa/test-gold.json"),
    ):
        value = _read_json(paths[filename])
        if not isinstance(value, list):
            raise ModelError("DATA_RECORD_INVALID", "TAT-QA split is invalid")
        for context in value:
            if not isinstance(context, dict):
                raise ModelError("DATA_RECORD_INVALID", "TAT-QA context is invalid")
            table = context.get("table")
            paragraphs = context.get("paragraphs")
            questions = context.get("questions")
            if (
                not isinstance(table, dict)
                or not isinstance(paragraphs, list)
                or not isinstance(questions, list)
            ):
                raise ModelError("DATA_RECORD_INVALID", "TAT-QA context fields are invalid")
            context_id = _required_str(cast(dict[str, object], table), "uid")
            table_rows = table.get("table")
            if not isinstance(table_rows, list):
                raise ModelError("DATA_RECORD_INVALID", "TAT-QA table is invalid")
            for question in questions:
                if not isinstance(question, dict):
                    raise ModelError("DATA_RECORD_INVALID", "TAT-QA question is invalid")
                question_id = _required_str(question, "uid")
                prompt = _required_str(question, "question")
                required = ("answer", "derivation", "answer_type", "answer_from", "scale")
                if any(key not in question for key in required):
                    raise ModelError("DATA_RECORD_INVALID", "TAT-QA labeled question is incomplete")
                original_id = f"{split}:{question_id}"
                data = _record(
                    source_id="tatqa",
                    original_id=original_id,
                    family_key="context:" + context_id,
                    system=(
                        "Answer the financial-report question from the supplied table and "
                        "paragraphs. Return only a JSON object with exactly the fields answer, "
                        "answerFrom, answerType, derivation, and scale. Derive every value from "
                        "the supplied material."
                    ),
                    user=_json({"paragraphs": paragraphs, "question": prompt, "table": table_rows}),
                    assistant=_json(
                        {
                            "answer": question["answer"],
                            "answerFrom": question["answer_from"],
                            "answerType": question["answer_type"],
                            "derivation": question["derivation"],
                            "scale": question["scale"],
                        }
                    ),
                    tags=[
                        "financial-report",
                        "hybrid-table-text",
                        "question-answering",
                        f"answer-type:{question['answer_type']}",
                        f"source-split:{split}",
                    ],
                    origin="human",
                )
                records.append(
                    ImportedRecord(
                        record=data,
                        originalSplit=cast(Any, original_split),
                        task="question-answering",
                        originalId=original_id,
                    )
                )
    unique, duplicates, cross_family = _deduplicate(records)
    return unique, _duplicate_counts(duplicates, cross_family)


_Converter = Callable[[dict[str, Path]], tuple[list[ImportedRecord], dict[str, int]]]
_CONVERTERS: dict[_ConverterId, _Converter] = {
    "banking77-v2": _banking77,
    "typed-decisions-v2": _typed_decisions,
    "wanli-semif-v2": _wanli,
    "multidogo-finance-v3": _multidogo,
    "tatqa-v2": _tatqa,
}


def acquire_source(
    source_id: str,
    cache: Path,
    *,
    offline: bool,
    max_records: int,
) -> SourceBatch:
    """Acquire exact source bytes and convert a deterministic, family-safe bounded batch."""
    if type(max_records) is not int or not 4 <= max_records <= 100_000:
        raise ModelError("ARGUMENT_INVALID", "Source max_records must be between 4 and 100000")
    catalog = _load_catalog()
    if source_id not in catalog.sources:
        raise ModelError("ARGUMENT_INVALID", "Unknown built-in curation source")
    entry = catalog.sources[source_id]
    paths: dict[str, Path] = {}
    for asset in entry.assets:
        destination = cache / asset.path
        download_asset(asset, destination, offline=offline, timeout=600.0)
        paths[asset.path] = destination
    records, excluded = _CONVERTERS[entry.converter](paths)
    records, cross_split_excluded = _remove_cross_split_families(records)
    if cross_split_excluded:
        excluded["cross_split_family_overlap"] = cross_split_excluded
    total_available = len(records)
    complete_test = source_id in {"typed-decisions", "wanli"}
    selected = _grouped_sample(
        records,
        max_records,
        complete_test_when_possible=complete_test,
    )
    omitted = total_available - len(selected)
    if omitted:
        excluded["max_records_cap"] = omitted
    return SourceBatch(
        source=entry.source,
        revision=entry.revision,
        assets=entry.assets,
        records=selected,
        totalAvailable=total_available,
        excludedCounts=dict(sorted(excluded.items())),
    )

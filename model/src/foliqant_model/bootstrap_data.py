"""Deterministic Banking77 diagnostic inputs without using its official test set."""

from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from .configuration import parse_json
from .contracts import ChatMessage, DataRecord
from .errors import ModelError


def _read_csv(path: Path, expected_rows: int, categories: set[str]) -> list[tuple[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != ["text", "category"]:
                raise ModelError("DATA_RECORD_INVALID", "Banking77 CSV headers do not match")
            rows: list[tuple[str, str]] = []
            for row in reader:
                text, category = row.get("text"), row.get("category")
                if (
                    len(row) != 2
                    or not isinstance(text, str)
                    or not text
                    or not isinstance(category, str)
                    or category not in categories
                ):
                    raise ModelError("DATA_RECORD_INVALID", "Invalid Banking77 CSV row")
                rows.append((text, category))
                if len(rows) > expected_rows:
                    raise ModelError("DATA_RECORD_INVALID", "Banking77 row count exceeds profile")
    except (OSError, UnicodeError, csv.Error) as error:
        raise ModelError("DATA_RECORD_INVALID", "Cannot read Banking77 CSV") from error
    if len(rows) != expected_rows or {category for _, category in rows} != categories:
        raise ModelError("DATA_RECORD_INVALID", "Banking77 row/category counts do not match")
    if len({text for text, _ in rows}) != len(rows):
        raise ModelError("DATA_DUPLICATE_CONVERSATION", "Banking77 contains duplicate text")
    return rows


def banking77_records(raw_directory: Path) -> tuple[list[DataRecord], list[DataRecord]]:
    """Select disjoint shared/customer pools exclusively from official training rows."""
    try:
        raw_categories = parse_json((raw_directory / "categories.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ModelError("DATA_RECORD_INVALID", "Cannot read Banking77 category catalog") from error
    if (
        not isinstance(raw_categories, list)
        or len(raw_categories) != 77
        or any(not isinstance(value, str) or not value for value in raw_categories)
    ):
        raise ModelError("DATA_RECORD_INVALID", "Banking77 must have 77 nonempty categories")
    categories = set(raw_categories)
    if len(categories) != 77:
        raise ModelError("DATA_RECORD_INVALID", "Banking77 categories must be unique")
    train = _read_csv(raw_directory / "train.csv", 10003, categories)
    test = _read_csv(raw_directory / "test.csv", 3080, categories)
    if any(count != 40 for count in Counter(category for _, category in test).values()):
        raise ModelError("DATA_RECORD_INVALID", "Banking77 test categories must have 40 rows")
    if {text for text, _ in train} & {text for text, _ in test}:
        raise ModelError("LEAKAGE_DETECTED", "Official training and test texts overlap")
    groups: dict[str, list[str]] = defaultdict(list)
    for text, category in train:
        groups[category].append(text)
    prompt = (
        "Classify the banking request. Return only the intent label. Allowed labels: "
        + ", ".join(sorted(categories))
        + "."
    )
    shared: list[DataRecord] = []
    customer: list[DataRecord] = []
    for category in sorted(groups):
        if len(groups[category]) < 4:
            raise ModelError("DATA_PARTITION_INVALID", "Insufficient examples per category")

        def rank(text: str, category: str = category) -> tuple[str, str]:
            payload = f"foliqant/banking77-smoke-v1\0{category}\0{text}".encode()
            return hashlib.sha256(payload).hexdigest(), text

        for index, text in enumerate(sorted(groups[category], key=rank)[:4]):
            record_id = "banking77-" + hashlib.sha256(f"{category}\0{text}".encode()).hexdigest()
            record = DataRecord(
                schemaVersion=1,
                id=record_id,
                sourceId="banking77",
                language="en",
                groupKeys=[record_id],
                tags=["banking77", "lifecycle-smoke"],
                origin="human",
                reviewed=False,
                messages=[
                    ChatMessage(role="system", content=prompt),
                    ChatMessage(role="user", content=text),
                    ChatMessage(role="assistant", content=category),
                ],
            )
            (shared if index < 2 else customer).append(record)
    return sorted(shared, key=lambda record: record.id), sorted(
        customer, key=lambda record: record.id
    )

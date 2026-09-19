"""Pinned source catalog and deterministic converter tests."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from foliqant_model.curation import sources


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def test_catalog_has_exact_public_sources_and_stable_digest() -> None:
    catalog = sources._load_catalog()
    assert list(catalog.sources) == [
        "banking77",
        "multidogo-finance",
        "tatqa",
        "typed-decisions",
        "wanli",
    ]
    expected = hashlib.sha256(sources._catalog_bytes()).hexdigest()
    assert sources.source_catalog_digest() == expected
    assert all(not entry.source.restrictions == [] for entry in catalog.sources.values())
    assert all(not entry.source.privacy == "private" for entry in catalog.sources.values())
    assert all(
        "/resolve/main/" not in asset.url
        for entry in catalog.sources.values()
        for asset in entry.assets
    )


def test_banking77_preserves_official_splits_and_removes_cross_split_family(
    tmp_path: Path,
) -> None:
    categories = ["card_arrival", "cash_withdrawal"]
    _write_json(tmp_path / "categories.json", categories)
    for split, rows in {
        "train": [("same question", "card_arrival"), ("train only", "cash_withdrawal")],
        "test": [("same question", "cash_withdrawal"), ("test only", "card_arrival")],
    }.items():
        with (tmp_path / f"{split}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["text", "category"])
            writer.writerows(rows)
    records, excluded = sources._banking77(
        {
            "banking77/categories.json": tmp_path / "categories.json",
            "banking77/train.csv": tmp_path / "train.csv",
            "banking77/test.csv": tmp_path / "test.csv",
        }
    )
    records, overlap = sources._remove_cross_split_families(records)
    assert overlap == 1
    assert Counter(row.originalSplit for row in records) == {"test": 2, "train": 1}
    assert all(row.record.reviewed is False and row.record.origin == "human" for row in records)
    assert all(
        '{"intent":"<one supplied label>"}' in row.record.messages[0].content for row in records
    )
    assert excluded == {}


def test_cross_family_duplicate_payloads_are_all_excluded_before_planning() -> None:
    duplicate_a = sources.ImportedRecord(
        record=sources._record(
            source_id="banking77",
            original_id="duplicate-a",
            family_key="family-a",
            system="Classify.",
            user="same input",
            assistant="same answer",
            tags=["source-split:train"],
            origin="human",
        ),
        originalSplit="train",
        task="classification",
        originalId="duplicate-a",
    )
    duplicate_b = sources.ImportedRecord(
        record=sources._record(
            source_id="banking77",
            original_id="duplicate-b",
            family_key="family-b",
            system="Classify.",
            user="same input",
            assistant="same answer",
            tags=["source-split:test"],
            origin="human",
        ),
        originalSplit="test",
        task="classification",
        originalId="duplicate-b",
    )
    related_b = sources.ImportedRecord(
        record=sources._record(
            source_id="banking77",
            original_id="related-b",
            family_key="family-b",
            system="Classify.",
            user="different input",
            assistant="same answer",
            tags=["source-split:test"],
            origin="human",
        ),
        originalSplit="test",
        task="classification",
        originalId="related-b",
    )

    records, collapsed, cross_family = sources._deduplicate([duplicate_a, duplicate_b, related_b])

    assert collapsed == 0
    assert cross_family == 2
    assert [row.originalId for row in records] == ["related-b"]
    assert all(row.record.messages[1].content != "same input" for row in records)


def test_typed_decisions_uses_all_workflows_without_latent_factors(tmp_path: Path) -> None:
    workflows = [
        "agent_trace_observability",
        "customer_service",
        "invoice_processing",
        "security_incidents",
    ]
    paths: dict[str, Path] = {}
    for split in ("train", "test"):
        rows = []
        for index, workflow in enumerate(workflows):
            rows.append(
                {
                    "id": f"{split}-{index}",
                    "workflow": workflow,
                    "split": split,
                    "state": json.dumps({"text": f"{split} case {index}"}),
                    "questions": json.dumps({"action": {"type": "choice"}}),
                    "gold": json.dumps(
                        {"action": {"label": "hold", "probabilities": {"hold": 1.0}}}
                    ),
                    "factors": json.dumps({"secret": "must not be model input"}),
                    "label_agreement": json.dumps({"action": 1.0}),
                    "n_questions": 1,
                }
            )
        path = tmp_path / f"{split}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        paths[f"typed-decisions/{split}.parquet"] = path
    records, excluded = sources._typed_decisions(paths)
    assert excluded == {}
    assert Counter(
        tag for row in records for tag in row.record.tags if tag.startswith("workflow:")
    ) == {f"workflow:{workflow}": 2 for workflow in workflows}
    assert all("secret" not in row.record.messages[1].content for row in records)
    assert all(row.record.origin == "teacher" and row.record.reviewed is False for row in records)
    assert all("probabilities" in row.record.messages[0].content for row in records)
    assert all("reference probability" not in row.record.messages[0].content for row in records)
    assert all("augmentation-ineligible:soft-target" in row.record.tags for row in records)
    selected = sources._grouped_sample(records, 6, complete_test_when_possible=True)
    assert Counter(row.originalSplit for row in selected) == {"test": 4, "train": 2}


def test_wanli_retains_semif_test_and_excludes_seed_family_from_training(
    tmp_path: Path,
) -> None:
    revision = "61c95318fd71c55b6ba355d76253254615f387ec"
    test_rows = []
    selections = []
    labels = ["entailment", "neutral", "contradiction"]
    options = ["supported", "insufficient", "contradicted"]
    for index in range(256):
        source_id = 10_000 + index
        test_rows.append(
            {
                "id": source_id,
                "premise": f"evidence {index}",
                "hypothesis": f"claim {index}",
                "gold": labels[index % 3],
                "genre": "generated_revised" if index == 0 else "generated",
                "pairID": f"seed-{index}",
            }
        )
        selections.append(
            {
                "source": "wanli",
                "id": f"selection-{index}",
                "family": "evidence_interpretation",
                "group_id": f"group-{index}",
                "option_ids": options[index % 3 :] + options[: index % 3],
                "upstream": {"revision": revision, "source_id": source_id},
            }
        )
    train_rows = [
        {
            "id": index,
            "premise": f"train evidence {index}",
            "hypothesis": f"train claim {index}",
            "gold": labels[index % 3],
            "genre": "generated",
            "pairID": "seed-0" if index == 0 else f"train-seed-{index}",
        }
        for index in range(5)
    ]
    _write_jsonl(tmp_path / "train.jsonl", train_rows)
    _write_jsonl(tmp_path / "test.jsonl", test_rows)
    _write_jsonl(tmp_path / "selection.jsonl", selections)
    records, excluded = sources._wanli(
        {
            "wanli/train.jsonl": tmp_path / "train.jsonl",
            "wanli/test.jsonl": tmp_path / "test.jsonl",
            "wanli/semif/source-selection.jsonl": tmp_path / "selection.jsonl",
        }
    )
    assert excluded == {"train_seed_family_overlap_with_semif_test": 1}
    assert Counter(row.originalSplit for row in records) == {"test": 256, "train": 4}
    assert all(row.record.origin == "synthetic" and row.record.reviewed is False for row in records)
    assert all(
        '{"label":"<one supplied option id>"}' in row.record.messages[0].content for row in records
    )
    selected = sources._grouped_sample(records, 258, complete_test_when_possible=True)
    assert Counter(row.originalSplit for row in selected) == {"test": 256, "train": 2}


def test_multidogo_redacts_sensitive_slots_and_keeps_conversation_family(
    tmp_path: Path,
) -> None:
    paths: dict[str, Path] = {}
    header = ["conversationId", "turnNumber", "utteranceId", "utterance", "slot-labels", "intent"]
    for split in ("train", "dev", "test"):
        path = tmp_path / f"{split}.tsv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(header)
            writer.writerow(
                [
                    f"conversation-{split}",
                    "0.0",
                    f"{split}-1",
                    f"card {split} 1234",
                    "O O card_number",
                    "reportlostcard",
                ]
            )
            writer.writerow(
                [f"conversation-{split}", "1.0", f"{split}-2", "bad alignment", "O", "contentonly"]
            )
        paths[f"multidogo-finance/{split}.tsv"] = path
    records, excluded = sources._multidogo(paths)
    assert len(records) == 3
    assert excluded == {"invalid_slot_alignment": 3}
    assert all("1234" not in row.record.messages[1].content for row in records)
    assert all("<redacted>" in row.record.messages[1].content for row in records)
    assert all(
        sensitive not in row.record.messages[1].content
        for row in records
        for sensitive in sources._SENSITIVE_SLOTS
    )
    assert all("intents and slotLabels" in row.record.messages[0].content for row in records)
    assert all("augmentation-ineligible:token-aligned" in row.record.tags for row in records)
    assert Counter(row.originalSplit for row in records) == {"train": 1, "validation": 1, "test": 1}


def test_tatqa_maps_questions_to_context_families_and_official_splits(tmp_path: Path) -> None:
    paths: dict[str, Path] = {}
    for split, filename in (
        ("train", "train.json"),
        ("dev", "dev.json"),
        ("test", "test-gold.json"),
    ):
        value = [
            {
                "table": {"uid": f"context-{split}", "table": [["year", "value"], ["2025", "4"]]},
                "paragraphs": [{"uid": f"p-{split}", "order": 1, "text": "The value is four."}],
                "questions": [
                    {
                        "uid": f"q-{split}-1",
                        "order": 1,
                        "question": "What is the value?",
                        "answer": ["4"],
                        "derivation": "4",
                        "answer_type": "span",
                        "answer_from": "table",
                        "rel_paragraphs": [],
                        "req_comparison": False,
                        "scale": "",
                    },
                    {
                        "uid": f"q-{split}-2",
                        "order": 2,
                        "question": "What is twice the value?",
                        "answer": 8,
                        "derivation": "4 * 2",
                        "answer_type": "arithmetic",
                        "answer_from": "table-text",
                        "rel_paragraphs": ["1"],
                        "req_comparison": False,
                        "scale": "",
                    },
                ],
            }
        ]
        _write_json(tmp_path / filename, value)
        paths[f"tatqa/{filename}"] = tmp_path / filename
    records, excluded = sources._tatqa(paths)
    assert excluded == {}
    assert len(records) == 6
    assert len({row.record.familyId for row in records}) == 3
    assert Counter(row.originalSplit for row in records) == {"train": 2, "validation": 2, "test": 2}
    assert all(row.record.origin == "human" and row.record.reviewed is False for row in records)
    assert all(
        "answerFrom, answerType, derivation, and scale" in row.record.messages[0].content
        for row in records
    )
    assert all("reference answer" not in row.record.messages[0].content for row in records)

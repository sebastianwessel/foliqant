"""Focused tests for shared lineage and leakage-index helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from foliqant_model.contracts import LeakageEntry
from foliqant_model.errors import ModelError
from foliqant_model.lineage import (
    merge_leakage_entries,
    read_leakage_index,
    write_leakage_index,
)


def _entry(identifier: str, *, prompt_hash: str = "d") -> LeakageEntry:
    return LeakageEntry(
        recordId=identifier,
        componentId="a" * 64,
        groupKeyHashes=["b" * 64],
        conversationHash="c" * 64,
        promptHash=prompt_hash * 64,
    )


def test_leakage_index_round_trip_is_canonical_and_sorted(tmp_path: Path) -> None:
    entries = [_entry("a"), _entry("b")]
    path = tmp_path / "leakage.jsonl"

    reference = write_leakage_index(path, entries)

    assert reference.path == "leakage.jsonl"
    assert reference.recordCount == 2
    assert read_leakage_index(path, reference) == entries
    assert path.read_bytes().endswith(b"\n")


def test_leakage_union_deduplicates_identical_rows_and_rejects_conflicts() -> None:
    original = _entry("a")
    assert merge_leakage_entries([[original], [original]]) == [original]

    with pytest.raises(ModelError) as captured:
        merge_leakage_entries([[original], [_entry("a", prompt_hash="e")]])
    assert captured.value.code == "LINEAGE_MISMATCH"


def test_leakage_index_rejects_tampered_bytes(tmp_path: Path) -> None:
    path = tmp_path / "leakage.jsonl"
    reference = write_leakage_index(path, [_entry("a")])
    path.write_bytes(path.read_bytes() + b"{}\n")

    with pytest.raises(ModelError) as captured:
        read_leakage_index(path, reference)
    assert captured.value.code == "INTEGRITY_FAILED"

import csv
import json
from pathlib import Path

import pytest

from foliqant_model.bootstrap_data import banking77_records
from foliqant_model.errors import ModelError


def make_data(path: Path, reverse: bool = False) -> None:
    categories = [f"category-{index}" for index in range(77)]
    (path / "categories.json").write_text(json.dumps(categories))
    for name, count in (("train", 10003), ("test", 3080)):
        rows = [(f"{name} text {index}", categories[index % 77]) for index in range(count)]
        if reverse:
            rows.reverse()
        with (path / f"{name}.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["text", "category"])
            writer.writerows(rows)


def test_conversion_is_deterministic_disjoint_and_train_only(tmp_path: Path) -> None:
    make_data(tmp_path)
    shared, customer = banking77_records(tmp_path)
    assert len(shared) == len(customer) == 154
    assert not {item.id for item in shared} & {item.id for item in customer}
    assert all(item.messages[1].content.startswith("train text ") for item in shared + customer)
    assert all(item.reviewed is False for item in shared + customer)
    make_data(tmp_path, reverse=True)
    assert banking77_records(tmp_path) == (shared, customer)


def test_test_overlap_is_rejected(tmp_path: Path) -> None:
    make_data(tmp_path)
    path = tmp_path / "test.csv"
    path.write_text(path.read_text().replace("test text 0,", "train text 0,", 1))
    with pytest.raises(ModelError) as failure:
        banking77_records(tmp_path)
    assert failure.value.code == "LEAKAGE_DETECTED"


def test_duplicate_categories_are_rejected(tmp_path: Path) -> None:
    make_data(tmp_path)
    (tmp_path / "categories.json").write_text(json.dumps(["same"] * 77))
    with pytest.raises(ModelError):
        banking77_records(tmp_path)

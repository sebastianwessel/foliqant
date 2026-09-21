"""Committed public schemas match their canonical Pydantic boundary types."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from foliqant.contracts.schemas import decision_schemas, runtime_schemas


def test_generated_library_schema_sets_are_valid_and_current() -> None:
    root = Path(__file__).parents[1] / "schemas" / "foliqant"
    schema_sets = {
        "runtime": runtime_schemas(),
        "decisions": decision_schemas(),
    }

    for scope, expected_schemas in schema_sets.items():
        directory = root / scope
        paths = sorted(directory.glob("*.schema.json"))
        assert {path.name for path in paths} == set(expected_schemas)
        for path in paths:
            expected = expected_schemas[path.name]
            expected["$id"] = f"https://foliqant.local/schemas/foliqant/{scope}/{path.name}"
            actual = json.loads(path.read_text(encoding="utf-8"))
            assert actual == expected
            Draft202012Validator.check_schema(actual)

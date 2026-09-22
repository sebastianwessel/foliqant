"""Packaged editor/tooling schemas match the canonical Python boundary types."""

import json
from importlib.resources import files

from jsonschema import Draft202012Validator

from foliqant.contracts.schemas import decision_schemas, runtime_schemas


def test_packaged_schema_sets_are_valid_and_current() -> None:
    expected_schemas = runtime_schemas()
    decisions = decision_schemas()
    assert expected_schemas.keys().isdisjoint(decisions)
    expected_schemas.update(decisions)
    directory = files("foliqant").joinpath("schemas")
    paths = [path for path in directory.iterdir() if path.name.endswith(".schema.json")]
    assert {path.name for path in paths} == set(expected_schemas)
    for path in paths:
        expected = expected_schemas[path.name]
        expected["$id"] = f"urn:foliqant:schema:{path.name}"
        actual = json.loads(path.read_text(encoding="utf-8"))
        assert actual == expected
        Draft202012Validator.check_schema(actual)

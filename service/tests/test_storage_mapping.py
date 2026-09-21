"""Golden persistence representations shared by present and future adapters."""

import json

import pytest

from foliqant.adapters.storage.postgres.mapping import canonical


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            '{"z":1,"a":{"float":1.0,"integer":1,"negative_zero":-0.0}}',
            '{"a":{"float":1.0,"integer":1,"negative_zero":-0.0},"z":1}',
        ),
        (
            '{"text":"Grüße 🌍","control":"\\u0000","absent":null}',
            '{"absent":null,"control":"\\u0000","text":"Gr\\u00fc\\u00dfe \\ud83c\\udf0d"}',
        ),
        (
            '{"small":1e-7,"large":1e20,"array":[true,false,null]}',
            '{"array":[true,false,null],"large":1e+20,"small":1e-07}',
        ),
    ],
)
def test_digest_v1_golden_encoding(source: str, expected: str) -> None:
    assert canonical(json.loads(source)).encode("utf-8") == expected.encode("ascii")


@pytest.mark.parametrize("number", [float("inf"), float("-inf"), float("nan")])
def test_digest_never_serializes_non_json_numbers(number: float) -> None:
    with pytest.raises(ValueError):
        canonical({"number": number})

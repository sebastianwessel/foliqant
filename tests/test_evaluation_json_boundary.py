"""Evaluation inputs reject overflow even when its syntax is valid JSON."""

import pytest

from foliqant.evaluation.dataset import read_json


@pytest.mark.parametrize("value", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("wrapper", ["{}", '{{"nested": [{}]}}'])
def test_nonfinite_values_rejected_at_shared_file_boundary(tmp_path, value, wrapper):
    path = tmp_path / "values.json"
    path.write_text(wrapper.format(value))
    with pytest.raises(ValueError, match="nonfinite JSON number"):
        read_json(path)


def test_finite_numeric_types_remain_distinct(tmp_path):
    path = tmp_path / "values.json"
    path.write_text("[1, 1.0, true, 1e308, -1e308]")
    actual = read_json(path)
    assert isinstance(actual, list)
    assert [type(item) for item in actual] == [int, float, bool, float, float]
    assert actual[-2:] == [1e308, -1e308]

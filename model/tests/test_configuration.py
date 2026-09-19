from pathlib import Path

import pytest

from foliqant_model.configuration import load_config, parse_json, read_document
from foliqant_model.contracts import TrainConfig
from foliqant_model.errors import ModelError


@pytest.mark.parametrize(
    "text",
    [
        '{"x":1,"x":2}',
        '{"nested":{"a":0,"a":1}}',
    ],
)
def test_duplicate_json_keys(text: str) -> None:
    with pytest.raises(ModelError) as failure:
        parse_json(text)
    assert failure.value.code == "CONFIG_DUPLICATE_KEY"


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "1e999", '"\\ud800"'])
def test_invalid_json_values(text: str) -> None:
    with pytest.raises(ModelError):
        parse_json(text)


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2\n",
        "a: {b: 1, b: 2}",
        "a: &x [1]\nb: *x",
        "a: !!str 1",
        "a: !custom 1",
        "? [a, b]\n: 2",
        "---\na: 1\n---\nb: 2",
    ],
)
def test_unsafe_yaml(tmp_path: Path, text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ModelError):
        read_document(path)


def test_json_scalar_semantics(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("a: yes\nb: on\nc: 2026-01-01\nd: true\ne: 1e-4\nf: 012\n")
    assert read_document(path) == {
        "a": "yes",
        "b": "on",
        "c": "2026-01-01",
        "d": True,
        "e": 0.0001,
        "f": "012",
    }


def test_defaults_and_strict_validation(tmp_path: Path) -> None:
    path = tmp_path / "train.yaml"
    path.write_text("schemaVersion: 1\nname: local\n")
    assert load_config(path, TrainConfig).steps == 100
    for extra in ["steps: '12'", "steps: true", "extra: private-secret"]:
        path.write_text("schemaVersion: 1\nname: local\n" + extra)
        with pytest.raises(ModelError) as failure:
            load_config(path, TrainConfig)
        assert "private-secret" not in failure.value.as_failure("train").model_dump_json()


def test_read_bounds_and_version(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b" " * 20)
    with pytest.raises(ModelError) as failure:
        read_document(path, max_bytes=10)
    assert failure.value.code == "CONFIG_TOO_LARGE"
    path.write_text('{"schemaVersion": true,"name":"local"}')
    with pytest.raises(ModelError) as failure:
        load_config(path, TrainConfig)
    assert failure.value.code == "CONFIG_UNSUPPORTED_VERSION"


def test_error_envelope() -> None:
    failure = ModelError("INTEGRITY_FAILED", "File digest mismatch").as_failure("verify")
    assert failure.exitCode == 5
    assert failure.error.category == "integrity"
    assert failure.ok is False


def test_named_pipe_config_is_rejected_without_opening(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    pipe = tmp_path / "config.yaml"
    os.mkfifo(pipe)
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from foliqant_model.configuration import read_document; "
            "from foliqant_model.errors import ModelError; import sys; "
            "\ntry: read_document(Path(sys.argv[1]))"
            "\nexcept ModelError as error: print(error.code)",
            str(pipe),
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    assert process.stdout.strip() == "CONFIG_INVALID"

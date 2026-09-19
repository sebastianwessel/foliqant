import json
from pathlib import Path

import numpy as np
import pytest
from safetensors.numpy import save_file

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.errors import ModelError
from foliqant_model.snapshots import checkpoint_from_snapshot, inspect_snapshot


def snapshot(path: Path) -> None:
    path.mkdir()
    (path / "config.json").write_text(json.dumps({"model_type": "llama"}))
    (path / "tokenizer_config.json").write_text(json.dumps({"chat_template": "{{ messages }}"}))
    (path / "tokenizer.json").write_text("{}")
    save_file({"weight": np.zeros((2, 2), dtype=np.float32)}, path / "model.safetensors")


def test_real_tensor_header_and_immutable_checkpoint(tmp_path: Path) -> None:
    source, output = tmp_path / "source", tmp_path / "checkpoint"
    snapshot(source)
    inspection = inspect_snapshot(source)
    assert inspection.tensor_count == 1
    assert inspection.weight_precision == "F32"
    manifest = checkpoint_from_snapshot(
        source, output, repo="example/model", revision="a" * 40, license_ref="test-only"
    )
    assert load_verified_artifact(output) == manifest
    assert manifest.root.kind == "checkpoint"
    with pytest.raises(ModelError) as failure:
        checkpoint_from_snapshot(
            source, output, repo="example/model", revision="a" * 40, license_ref="test-only"
        )
    assert failure.value.code == "OUTPUT_EXISTS"


@pytest.mark.parametrize("issue", ["python", "auto-map", "missing-template", "bad-index"])
def test_unsupported_snapshots_fail_before_publication(tmp_path: Path, issue: str) -> None:
    source = tmp_path / "source"
    snapshot(source)
    if issue == "python":
        (source / "model.py").write_text("raise AssertionError('must never execute')")
    elif issue == "auto-map":
        (source / "config.json").write_text(
            json.dumps(
                {
                    "model_type": "llama",
                    "auto_map": {"AutoModel": "model.Custom"},
                }
            )
        )
    elif issue == "missing-template":
        (source / "tokenizer_config.json").write_text("{}")
    else:
        (source / "model.safetensors.index.json").write_text(
            json.dumps(
                {
                    "weight_map": {"not-a-tensor": "model.safetensors"},
                }
            )
        )
    with pytest.raises(ModelError):
        inspect_snapshot(source)

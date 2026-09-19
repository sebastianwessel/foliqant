"""Worker-boundary and completion-loss tests for the lazy MLX backend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foliqant_model import backend
from foliqant_model.contracts import WorkerRequest, WorkerResult


def _doctor_request() -> dict[str, object]:
    return {"schemaVersion": 1, "requestId": "doctor-probe", "operation": "doctor"}


def _generate_request() -> WorkerRequest:
    return WorkerRequest.model_validate(
        {
            "schemaVersion": 1,
            "requestId": "generation-probe",
            "operation": "generate",
            "modelPath": "/missing/model",
            "adapterPath": None,
            "messages": [{"role": "user", "content": "hello"}],
            "seed": 7,
            "maxTokens": 2,
            "temperature": 0,
        }
    )


def test_import_does_not_import_gpu_libraries() -> None:
    script = (
        "import sys; import foliqant_model.backend; "
        "assert 'mlx.core' not in sys.modules; assert 'mlx_lm' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.integration
def test_entrypoint_writes_typed_doctor_result_without_stdout(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(_doctor_request()), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "foliqant_model.backend",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    result = WorkerResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    assert result.root.requestId == "doctor-probe"
    assert result.root.operation == "doctor"
    assert result.root.ok is True
    assert result_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.integration
def test_real_doctor_reports_pinned_mlx_and_metal() -> None:
    result = backend.execute_request(WorkerRequest.model_validate(_doctor_request()))
    payload = result.model_dump(mode="python")
    assert isinstance(payload, dict)
    doctor = payload.get("result")
    assert isinstance(doctor, dict)
    assert doctor["mlxImportAvailable"] is True
    assert doctor["metalAvailable"] is True
    assert doctor["mlxVersion"] == "0.32.2"
    assert doctor["mlxLmVersion"] == "0.31.3"


@pytest.mark.integration
def test_real_one_step_training_uses_custom_loss(tmp_path: Path) -> None:
    model_path = os.environ.get("FOLIQANT_TEST_MODEL")
    if model_path is None:
        pytest.skip("FOLIQANT_TEST_MODEL is not set")
    train_path = tmp_path / "train.jsonl"
    validation_path = tmp_path / "validation.jsonl"
    row = {
        "messages": [
            {"role": "user", "content": "Reply with OK."},
            {"role": "assistant", "content": "OK"},
        ]
    }
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    train_path.write_text(encoded, encoding="utf-8")
    validation_path.write_text(encoded, encoding="utf-8")
    adapter_path = tmp_path / "adapter"
    namespace = {
        "model": model_path,
        "data": str(tmp_path),
        "adapter_path": str(adapter_path),
        "resume_adapter_file": None,
        "train": True,
        "test": False,
        "fine_tune_type": "lora",
        "optimizer": "adamw",
        "optimizer_config": {"adamw": {"weight_decay": 0}},
        "seed": 7,
        "num_layers": 1,
        "batch_size": 1,
        "iters": 1,
        "val_batches": 1,
        "learning_rate": 0.0001,
        "steps_per_report": 1,
        "steps_per_eval": 1,
        "save_every": 1,
        "test_batches": 500,
        "max_seq_length": 64,
        "config": None,
        "grad_checkpoint": False,
        "grad_accumulation_steps": 1,
        "clear_cache_threshold": 0,
        "lr_schedule": None,
        "report_to": None,
        "project_name": None,
        "lora_parameters": {"rank": 4, "scale": 8.0, "dropout": 0.0},
        "mask_prompt": True,
    }
    request = WorkerRequest.model_validate(
        {
            "schemaVersion": 1,
            "requestId": "one-step-train",
            "operation": "train",
            "namespace": namespace,
            "trainPath": str(train_path),
            "validationPath": str(validation_path),
            "outputPath": str(adapter_path),
        }
    )
    result = backend.execute_request(request)
    payload = result.model_dump(mode="python")
    assert isinstance(payload, dict)
    assert payload["ok"] is True, payload
    train_result = payload.get("result")
    assert isinstance(train_result, dict)
    assert train_result["finalLoss"] >= 0
    assert (adapter_path / "adapters.safetensors").is_file()
    assert (adapter_path / "adapter_config.json").is_file()


def test_duplicate_request_key_is_rejected_without_result(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        '{"schemaVersion":1,"requestId":"a","requestId":"b","operation":"doctor"}',
        encoding="utf-8",
    )
    assert backend.run_request_file(request_path, result_path) == 2
    assert not result_path.exists()


@pytest.mark.integration
def test_existing_result_is_never_overwritten(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(_doctor_request()), encoding="utf-8")
    result_path.write_text("sentinel", encoding="utf-8")
    assert backend.run_request_file(request_path, result_path) == 3
    assert result_path.read_text(encoding="utf-8") == "sentinel"


def test_unexpected_generation_error_becomes_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_request: object) -> None:
        raise RuntimeError("sensitive implementation detail")

    monkeypatch.setattr(backend, "_generate", fail)
    result = backend.execute_request(_generate_request())
    assert result.root.ok is False
    assert result.root.operation == "generate"
    assert result.root.error.code == "GENERATION_FAILED"
    assert "sensitive" not in result.root.error.message


def test_model_preflight_rejects_custom_code(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "llama", "auto_map": {"AutoModel": "model.py"}}),
        encoding="utf-8",
    )
    (tmp_path / "model.py").write_text("raise RuntimeError", encoding="utf-8")
    with pytest.raises(Exception, match="unsafe file"):
        backend._validate_local_model_dir(tmp_path)


@pytest.mark.integration
def test_completion_mask_and_loss_use_real_mlx_tensors() -> None:
    mx = pytest.importorskip("mlx.core")
    prompt_offsets = mx.array([[2], [3]])
    sequence_lengths = mx.array([[5], [4]])
    mask = backend.completion_token_mask(prompt_offsets, sequence_lengths, 5)
    assert mask.tolist() == [
        [False, True, True, True, False],
        [False, False, True, False, False],
    ]

    batch = mx.array(
        [
            [1, 2, 3, 4, 5, 0],
            [1, 2, 3, 4, 0, 0],
        ]
    )
    lengths = mx.array([[2, 5], [3, 4]])

    def zero_logits(inputs: object) -> object:
        return mx.zeros((inputs.shape[0], inputs.shape[1], 10))  # type: ignore[attr-defined]

    loss, token_count = backend.completion_loss(zero_logits, batch, lengths)
    mx.eval(loss, token_count)
    assert token_count.item() == 4
    assert loss.item() == pytest.approx(2.3025851, rel=1e-5)


def test_worker_environment_hardening_is_child_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "secret")
    backend._harden_worker_environment()
    assert "HF_TOKEN" not in os.environ
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"

"""Opt-in public-CLI acceptance for the complete local model lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts import ArtifactManifest, CliSuccess


def _run_cli(*arguments: str | Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "foliqant_model", *(str(value) for value in arguments)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    envelope = CliSuccess.model_validate_json(completed.stdout)
    assert envelope.root.command == arguments[0]
    return cast(dict[str, object], envelope.root.result.model_dump(mode="json"))


def _parent_ids(manifest: ArtifactManifest) -> list[str]:
    return [parent.artifactId for parent in manifest.root.parents]


def _weight_digests(manifest: ArtifactManifest) -> set[str]:
    return {entry.sha256 for entry in manifest.root.files if entry.path.endswith(".safetensors")}


@pytest.mark.integration
def test_public_cli_complete_local_lifecycle(tmp_path: Path) -> None:
    configured = os.environ.get("FOLIQANT_TEST_SETUP")
    if configured is None:
        pytest.skip("FOLIQANT_TEST_SETUP is not set")
    setup = Path(configured).expanduser().resolve(strict=True)
    upstream_path = setup / "artifacts/upstream"
    shared_recipe = setup / "config/shared.json"
    customer_recipe = setup / "config/customer.json"
    train_config = setup / "config/train.json"
    evaluation_config = setup / "config/evaluation.json"
    for required in (
        upstream_path / "manifest.json",
        shared_recipe,
        customer_recipe,
        train_config,
        evaluation_config,
    ):
        assert required.is_file()

    shared_data_path = tmp_path / "shared-data"
    customer_data_path = tmp_path / "customer-data"
    shared_prepare = _run_cli("prepare", "--config", shared_recipe, "--output", shared_data_path)
    customer_prepare = _run_cli(
        "prepare", "--config", customer_recipe, "--output", customer_data_path
    )
    assert shared_prepare["partitionRecords"] == customer_prepare["partitionRecords"]
    assert shared_prepare["diagnostic"] is True
    assert customer_prepare["diagnostic"] is True

    shared_adapter_path = tmp_path / "shared-adapter"
    shared_train = _run_cli(
        "train",
        "--config",
        train_config,
        "--model",
        upstream_path,
        "--dataset",
        shared_data_path,
        "--output",
        shared_adapter_path,
    )
    warm_adapter_path = tmp_path / "shared-warm-adapter"
    warm_train = _run_cli(
        "train",
        "--config",
        train_config,
        "--model",
        upstream_path,
        "--dataset",
        shared_data_path,
        "--warm-start",
        shared_adapter_path,
        "--output",
        warm_adapter_path,
    )
    assert shared_train["artifact"] != warm_train["artifact"]

    shared_merged_path = tmp_path / "shared-merged"
    _run_cli(
        "merge",
        "--model",
        upstream_path,
        "--adapter",
        warm_adapter_path,
        "--output",
        shared_merged_path,
        "--timeout-seconds",
        "60",
    )
    quantized_shared_path = tmp_path / "shared-quantized"
    _run_cli(
        "quantize",
        "--model",
        shared_merged_path,
        "--output",
        quantized_shared_path,
        "--bits",
        "4",
        "--group-size",
        "64",
        "--timeout-seconds",
        "60",
    )

    customer_adapter_path = tmp_path / "customer-adapter"
    customize = _run_cli(
        "customize",
        "--config",
        train_config,
        "--model",
        quantized_shared_path,
        "--dataset",
        customer_data_path,
        "--customer",
        "acceptance-customer",
        "--output",
        customer_adapter_path,
    )
    customize_artifact = cast(dict[str, object], customize["artifact"])
    assert customize_artifact["customer"] == "acceptance-customer"
    customer_merged_path = tmp_path / "customer-merged"
    _run_cli(
        "merge",
        "--model",
        quantized_shared_path,
        "--adapter",
        customer_adapter_path,
        "--output",
        customer_merged_path,
        "--timeout-seconds",
        "60",
    )

    checkpoint_export_path = tmp_path / "checkpoint-export"
    gguf_export_path = tmp_path / "gguf-export"
    for format_, output in (("checkpoint", checkpoint_export_path), ("gguf", gguf_export_path)):
        exported = _run_cli(
            "export",
            "--model",
            customer_merged_path,
            "--format",
            format_,
            "--output",
            output,
            "--timeout-seconds",
            "60",
        )
        assert exported["compatibilityStatus"] == "unverified"

    calibration_path = tmp_path / "calibration-evaluation"
    test_path = tmp_path / "test-evaluation"
    for split, output in (("calibration", calibration_path), ("test", test_path)):
        evaluated = _run_cli(
            "evaluate",
            "--config",
            evaluation_config,
            "--model",
            customer_merged_path,
            "--dataset",
            customer_data_path,
            "--split",
            split,
            "--output",
            output,
        )
        assert evaluated["split"] == split
        assert evaluated["exampleCount"] == 4

    policy_path = tmp_path / "policy"
    _run_cli(
        "calibrate",
        "--evaluation",
        calibration_path,
        "--output",
        policy_path,
        "--max-error",
        "0.99",
        "--min-accepted",
        "1",
    )
    audit_path = tmp_path / "audit"
    audit_result = _run_cli(
        "audit",
        "--evaluation",
        test_path,
        "--policy",
        policy_path,
        "--output",
        audit_path,
    )
    assert audit_result["status"] in {"insufficient", "meets-bound", "fails-bound"}

    upstream = load_verified_artifact(upstream_path)
    shared_data = load_verified_artifact(shared_data_path)
    customer_data = load_verified_artifact(customer_data_path)
    shared_adapter = load_verified_artifact(shared_adapter_path)
    warm_adapter = load_verified_artifact(warm_adapter_path)
    shared_merged = load_verified_artifact(shared_merged_path)
    quantized_shared = load_verified_artifact(quantized_shared_path)
    customer_adapter = load_verified_artifact(customer_adapter_path)
    customer_merged = load_verified_artifact(customer_merged_path)
    checkpoint_export = load_verified_artifact(checkpoint_export_path)
    gguf_export = load_verified_artifact(gguf_export_path)
    calibration = load_verified_artifact(calibration_path)
    test_evaluation = load_verified_artifact(test_path)
    policy = load_verified_artifact(policy_path)
    audit = load_verified_artifact(audit_path)

    assert _parent_ids(shared_adapter) == [
        upstream.root.artifactId,
        shared_data.root.artifactId,
    ]
    assert _parent_ids(warm_adapter) == [
        upstream.root.artifactId,
        shared_data.root.artifactId,
        shared_adapter.root.artifactId,
    ]
    assert _parent_ids(shared_merged) == [upstream.root.artifactId, warm_adapter.root.artifactId]
    assert _parent_ids(quantized_shared) == [shared_merged.root.artifactId]
    assert _parent_ids(customer_adapter) == [
        quantized_shared.root.artifactId,
        customer_data.root.artifactId,
    ]
    assert _parent_ids(customer_merged) == [
        quantized_shared.root.artifactId,
        customer_adapter.root.artifactId,
    ]
    assert _parent_ids(checkpoint_export) == [customer_merged.root.artifactId]
    assert _parent_ids(gguf_export) == [customer_merged.root.artifactId]
    assert _parent_ids(calibration) == [
        customer_merged.root.artifactId,
        customer_data.root.artifactId,
    ]
    assert _parent_ids(test_evaluation) == [
        customer_merged.root.artifactId,
        customer_data.root.artifactId,
    ]
    assert _parent_ids(policy) == [calibration.root.artifactId]
    assert _parent_ids(audit) == [test_evaluation.root.artifactId, policy.root.artifactId]

    assert _weight_digests(shared_adapter) != _weight_digests(warm_adapter)
    assert _weight_digests(upstream) != _weight_digests(shared_merged)
    assert _weight_digests(quantized_shared) != _weight_digests(customer_merged)
    assert _weight_digests(checkpoint_export) == _weight_digests(customer_merged)

    expected_rights = [right.model_dump(mode="json") for right in customer_data.root.sourceRights]
    final_rights = [right.model_dump(mode="json") for right in customer_merged.root.sourceRights]
    assert all(right in final_rights for right in expected_rights)
    assert all(right["commercialUse"] == "allowed" for right in final_rights)
    assert all(right["restrictions"] for right in final_rights)
    assert checkpoint_export.root.sourceRights == customer_merged.root.sourceRights
    assert gguf_export.root.sourceRights == customer_merged.root.sourceRights

    artifacts = (
        shared_data_path,
        customer_data_path,
        shared_adapter_path,
        warm_adapter_path,
        shared_merged_path,
        quantized_shared_path,
        customer_adapter_path,
        customer_merged_path,
        checkpoint_export_path,
        gguf_export_path,
        calibration_path,
        test_path,
        policy_path,
        audit_path,
    )
    for artifact_path in artifacts:
        verified = _run_cli("verify", artifact_path)
        assert verified["valid"] is True
        assert verified["artifactId"] == load_verified_artifact(artifact_path).root.artifactId

import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from safetensors.numpy import save_file

from foliqant_model.artifacts import create_manifest, load_verified_artifact
from foliqant_model.contracts import FileEntry, Prediction, WorkerResult, canonical_digest
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError
from foliqant_model.evaluation import evaluate_model
from foliqant_model.risk import (
    audit_policy,
    audit_threshold,
    calibrate_policy,
    select_threshold,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _producer(command: str) -> dict[str, object]:
    return {
        "name": "foliqant-model",
        "version": "0.1.0",
        "command": command,
        "pythonVersion": "3.12.0",
        "platform": "test",
        "machine": "test",
        "codeRevision": None,
        "codeDirty": None,
        "codeIdentityUnavailableReason": "test fixture",
        "components": [],
    }


def _checkpoint(directory: Path) -> Path:
    directory.mkdir()
    config = b'{"model_type":"fixture"}'
    (directory / "config.json").write_bytes(config)
    (directory / "tokenizer.json").write_bytes(b"{}")
    (directory / "tokenizer_config.json").write_bytes(b'{"chat_template":"template"}')
    save_file(
        {"model.layers.0.weight": np.ones((2, 2), dtype=np.float32)},
        directory / "model.safetensors",
    )
    tokenizer_entries = [
        FileEntry(
            path=name,
            size=(directory / name).stat().st_size,
            sha256=_digest((directory / name).read_bytes()),
        )
        for name in ("tokenizer.json", "tokenizer_config.json")
    ]
    create_manifest(
        directory,
        {
            "schemaVersion": 1,
            "kind": "checkpoint",
            "name": "risk-checkpoint",
            "createdAt": "2026-09-19T00:00:00Z",
            "stage": "upstream",
            "parents": [],
            "producer": _producer("fetch"),
            "sourceRights": [],
            "details": {
                "model": {
                    "architecture": "fixture",
                    "weightFormat": "safetensors",
                    "configSha256": _digest(config),
                    "tokenizerSha256": canonical_digest(
                        [entry.model_dump(mode="json") for entry in tokenizer_entries]
                    ),
                    "chatTemplateSha256": _digest(b"template"),
                    "tokenizerFiles": [entry.path for entry in tokenizer_entries],
                    "chatTemplateSource": {
                        "kind": "tokenizer-config",
                        "value": "tokenizer_config.json#/chat_template",
                    },
                },
                "upstreamRepo": "fixture/model",
                "upstreamRevision": "a" * 40,
                "licenseRef": "fixture",
                "weightPrecision": "F32",
                "compatibility": [],
            },
        },
    )
    return directory


def _dataset(tmp_path: Path, name: str) -> Path:
    records = [
        {
            "schemaVersion": 1,
            "id": f"{name}-{index:02d}",
            "sourceId": "source",
            "language": "en",
            "groupKeys": [f"{name}-group-{index:02d}"],
            "messages": [
                {"role": "user", "content": f"question {name}-{index:02d}"},
                {"role": "assistant", "content": f"answer {name}-{index:02d}"},
            ],
            "tags": [],
            "origin": "human",
            "reviewed": True,
        }
        for index in range(12)
    ]
    source = tmp_path / f"{name}-source.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    config = tmp_path / f"{name}-dataset.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": name,
                "sources": [
                    {
                        "id": "source",
                        "path": source.name,
                        "license": "test",
                        "licenseEvidence": "local fixture",
                        "trainingAllowed": True,
                        "sharedTrainingAllowed": True,
                        "redistributionAllowed": False,
                        "privacy": "public",
                    }
                ],
                "seed": 7,
                "validationFraction": 0.1,
                "calibrationFraction": 0.1,
                "testFraction": 0.1,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / f"{name}-dataset"
    prepare_dataset(config, output)
    return output


def _evaluation_config(path: Path, *, seed: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "seed": seed,
                "maxTokens": 8,
                "maxExamples": 1,
            }
        ),
        encoding="utf-8",
    )


def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    config: Path,
    model: Path,
    dataset: Path,
    output: Path,
    *,
    split: str,
    correct: bool,
    score: float,
):  # type: ignore[no-untyped-def]
    def worker(request, *, workspace: Path, timeout_seconds: int):  # type: ignore[no-untyped-def]
        prompt = request.root.messages[-1].content
        identifier = prompt.removeprefix("question ")
        generated = f"answer {identifier}" if correct else "wrong answer"
        return WorkerResult.model_validate(
            {
                "schemaVersion": 1,
                "requestId": request.root.requestId,
                "operation": "generate",
                "ok": True,
                "result": {
                    "generated": generated,
                    "generatedTokens": 2,
                    "meanTokenLogprob": score,
                    "finishReason": "stop",
                    "elapsedSeconds": 0.01,
                },
            },
            strict=True,
        )

    monkeypatch.setattr("foliqant_model.evaluation.run_worker", worker)
    monkeypatch.setattr("foliqant_model.evaluation._runtime_components", lambda: [])
    return evaluate_model(config, model, dataset, output, split=split)


def prediction(
    index: int, score: float, *, correct: bool = True, valid: bool = True, truncated: bool = False
) -> Prediction:
    return Prediction.model_validate(
        {
            "id": f"row-{index}",
            "sourceId": "test",
            "language": "en",
            "tags": [],
            "componentId": f"{index:064x}",
            "groupIds": [f"{index:064x}"],
            "representative": True,
            "expected": "yes",
            "generated": "yes" if correct else "no",
            "expectedJson": None,
            "generatedJson": None,
            "elapsedSeconds": 0.1,
            "generatedTokens": 1,
            "meanTokenLogprob": score,
            "jsonValid": False,
            "finishReason": "length" if truncated else "stop",
            "schemaValid": None,
            "evidenceValid": None,
            "fieldPresent": {},
            "fieldValid": {},
            "applicableValid": valid,
            "exactCorrect": correct and valid,
        }
    )


def test_tied_scores_cannot_be_split_to_hide_errors() -> None:
    rows = [prediction(0, -0.1), prediction(1, -0.2), prediction(2, -0.2, correct=False)]
    selected = select_threshold(rows, max_error=0.2, min_accepted=1)
    assert selected.threshold == -0.1
    assert selected.counts.acceptedCount == 1
    selected = select_threshold(rows, max_error=0.2, min_accepted=2)
    assert selected.threshold is None
    assert selected.counts.acceptedCount == 0
    assert selected.counts.eligibleCount == 3


def test_selection_can_recover_after_intermediate_threshold_fails() -> None:
    rows = [prediction(0, -0.1, correct=False)] + [prediction(i, -float(i)) for i in range(1, 10)]
    selected = select_threshold(rows, max_error=0.1, min_accepted=1)
    assert selected.threshold == -9.0
    assert selected.counts.acceptedCount == 10
    assert selected.counts.errors == 1


def test_eligibility_does_not_look_at_correctness_labels() -> None:
    rows = [
        prediction(0, -0.1, correct=False),
        prediction(1, -0.1, valid=False),
        prediction(2, -0.1, truncated=True),
    ]
    result = audit_threshold(rows, threshold=-0.2, max_error=0.2, min_accepted=1)
    assert result.counts.representativeCount == 3
    assert result.counts.eligibleCount == 1
    assert result.counts.acceptedCount == 1
    assert result.counts.errors == 1
    assert result.upper_error_bound == 1.0
    assert result.status == "fails-bound"
    assert result.coverage == 1 / 3


def test_exact_bound_matches_closed_form_and_small_samples_fail() -> None:
    rows = [prediction(i, -0.1) for i in range(59)]
    result = audit_threshold(rows, threshold=-0.1, max_error=0.05, min_accepted=1)
    assert result.upper_error_bound == pytest.approx(1 - math.pow(0.05, 1 / 59))
    assert result.status == "meets-bound"
    result = audit_threshold(rows[:58], threshold=-0.1, max_error=0.05, min_accepted=1)
    assert result.status == "fails-bound"
    result = audit_threshold(rows, threshold=None, max_error=0.05, min_accepted=1)
    assert result.status == "insufficient"
    assert result.upper_error_bound is None
    assert result.coverage == 0.0


def test_missing_score_is_not_silently_removed_from_denominator() -> None:
    row = prediction(0, -0.1).model_dump(mode="json")
    row.update(
        generatedTokens=0,
        meanTokenLogprob=None,
        meanTokenLogprobUnavailableReason="no-generated-tokens",
    )
    with pytest.raises(ModelError) as failure:
        select_threshold([Prediction.model_validate(row)], max_error=0.1, min_accepted=1)
    assert failure.value.code == "RISK_SCORE_MISSING"


@pytest.mark.parametrize("value", [0.0, 1.0, float("nan"), float("inf"), True])
def test_invalid_error_target_is_rejected(value: float) -> None:
    with pytest.raises(ModelError) as failure:
        select_threshold([prediction(0, -0.1)], max_error=value, min_accepted=1)
    assert failure.value.code == "ARGUMENT_INVALID"


def test_policy_orchestration_rejects_mismatches_and_keeps_threshold_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _checkpoint(tmp_path / "model")
    dataset = _dataset(tmp_path, "primary")
    other_dataset = _dataset(tmp_path, "other")
    config = tmp_path / "evaluation.json"
    changed_profile_config = tmp_path / "changed-profile.json"
    _evaluation_config(config, seed=11)
    _evaluation_config(changed_profile_config, seed=12)

    calibration_path = tmp_path / "calibration"
    calibration = _evaluate(
        monkeypatch,
        config,
        model,
        dataset,
        calibration_path,
        split="calibration",
        correct=True,
        score=-0.1,
    )
    test_path = tmp_path / "test"
    test_evaluation = _evaluate(
        monkeypatch,
        config,
        model,
        dataset,
        test_path,
        split="test",
        correct=False,
        score=-0.05,
    )
    policy_path = tmp_path / "policy"
    policy = calibrate_policy(calibration_path, policy_path, max_error=0.5, min_accepted=1)
    policy_bytes = (policy_path / "manifest.json").read_bytes()
    assert policy.root.details.threshold == -0.1

    audit_path = tmp_path / "audit"
    audit = audit_policy(test_path, policy_path, audit_path)
    assert audit.root.details.counts.acceptedCount == 1
    assert audit.root.details.counts.errors == 1
    assert audit.root.details.coverage == 1.0
    assert audit.root.details.upperErrorBound == 1.0
    assert audit.root.details.status == "fails-bound"
    assert load_verified_artifact(policy_path).root.artifactId == policy.root.artifactId
    assert (policy_path / "manifest.json").read_bytes() == policy_bytes

    with pytest.raises(ModelError) as wrong_split:
        calibrate_policy(test_path, tmp_path / "wrong-split-policy", max_error=0.5, min_accepted=1)
    assert wrong_split.value.code == "ARGUMENT_INVALID"

    changed_profile_path = tmp_path / "changed-profile-test"
    _evaluate(
        monkeypatch,
        changed_profile_config,
        model,
        dataset,
        changed_profile_path,
        split="test",
        correct=True,
        score=-0.1,
    )
    with pytest.raises(ModelError) as profile_mismatch:
        audit_policy(changed_profile_path, policy_path, tmp_path / "profile-audit")
    assert profile_mismatch.value.code == "PROFILE_MISMATCH"

    changed_dataset_path = tmp_path / "changed-dataset-test"
    changed_dataset_evaluation = _evaluate(
        monkeypatch,
        config,
        model,
        other_dataset,
        changed_dataset_path,
        split="test",
        correct=True,
        score=-0.1,
    )
    assert (
        changed_dataset_evaluation.root.details.deploymentProfileId
        == calibration.root.details.deploymentProfileId
    )
    with pytest.raises(ModelError) as dataset_mismatch:
        audit_policy(changed_dataset_path, policy_path, tmp_path / "dataset-audit")
    assert dataset_mismatch.value.code == "LINEAGE_MISMATCH"

    # A verified dataset cannot assign one record or connected group to both
    # calibration and test. The manifest contract rejects such an overlap before
    # audit_policy's defense-in-depth SAMPLE_OVERLAP check can be reached.
    overlapping = tmp_path / "overlapping-test"
    overlapping.mkdir()
    shutil.copyfile(calibration_path / "predictions.jsonl", overlapping / "predictions.jsonl")
    overlapping_details = calibration.root.details.model_dump(mode="json")
    overlapping_details["split"] = "test"
    with pytest.raises(ModelError) as overlap:
        create_manifest(
            overlapping,
            {
                "schemaVersion": 1,
                "kind": "evaluation",
                "name": "overlapping-test",
                "createdAt": "2026-09-19T00:00:01Z",
                "stage": calibration.root.stage,
                "parents": [parent.model_dump(mode="json") for parent in calibration.root.parents],
                "sourceRights": [
                    right.model_dump(mode="json") for right in calibration.root.sourceRights
                ],
                "producer": calibration.root.producer.model_dump(mode="json"),
                "details": overlapping_details,
            },
        )
    assert overlap.value.code == "OUTPUT_INVALID"
    assert not (overlapping / "manifest.json").exists()
    assert test_evaluation.root.details.datasetArtifactId == policy.root.details.datasetArtifactId

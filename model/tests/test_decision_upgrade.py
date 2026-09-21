"""Offline upgrade preserves valid historical data without synthesizing model labels."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from foliqant.decisions import DecisionInput, DecisionOutput
from foliqant_model.artifacts import load_verified_artifact
from foliqant_model.contracts.base import canonical_digest
from foliqant_model.contracts.inputs import (
    DataRecord,
    FrozenFamilyAssignment,
    ResolvedSourceDeclaration,
)
from foliqant_model.curation.decision_contracts import DecisionDataSettings
from foliqant_model.curation.decision_seeds import (
    _SYSTEM,
    build_authored_seeds,
)
from foliqant_model.curation.decision_upgrade import (
    _KNOWN_ISSUE_CRITERIA,
    upgrade_decision_data,
    upgrade_record,
)
from foliqant_model.curation.legacy_decision_prompt import LEGACY_SYSTEM
from foliqant_model.curation.migration import _inventory
from foliqant_model.curation.runner import _publish
from foliqant_model.data import prepare_dataset
from foliqant_model.errors import ModelError
from foliqant_model.native_data import validate_native_record


def _legacy(record: DataRecord) -> DataRecord:
    payload = record.model_dump(mode="json")
    payload["messages"][0]["content"] = payload["messages"][0]["content"].replace(
        _SYSTEM, LEGACY_SYSTEM
    )
    for message in payload["messages"][1:]:
        value = json.loads(message["content"])
        value["schemaVersion"] = 1
        for result in value.get("results", []):
            result["answerability"]["issues"] = [
                "no_matching_option" if issue == "no_supported_answer" else issue
                for issue in result["answerability"]["issues"]
            ]
        message["content"] = json.dumps(value)
    return DataRecord.model_validate(payload, strict=True)


def _seed_records() -> list[DataRecord]:
    seeds = build_authored_seeds(DecisionDataSettings(), seed=17, languages=["en"])
    return list({seed.parent.familyId: seed.parent for seed in seeds}.values())[:4]


def test_upgrade_merges_only_issue_codes_and_preserves_source_explanation_and_labels() -> None:
    record = _legacy(_seed_records()[0])
    data = record.model_dump(mode="json")
    task = json.loads(data["messages"][1]["content"])
    task["state"]["sources"][0]["text"] += " Literal missing_information no_matching_option."
    task["questions"][0]["criteria"].append(_KNOWN_ISSUE_CRITERIA[0])
    answer = json.loads(data["messages"][2]["content"])
    answer["results"][0]["answerability"]["issues"] = [
        "missing_information",
        "conflicting_information",
        "no_matching_option",
        "multiple_valid_options",
    ]
    answer["results"][0]["explanation"]["summary"] = "Literal missing_information is preserved."
    data["messages"][1]["content"] = json.dumps(task)
    data["messages"][2]["content"] = json.dumps(answer)
    old = DataRecord.model_validate(data, strict=True)
    new = upgrade_record(old)
    new_task = DecisionInput.model_validate_json(new.messages[1].content, strict=True)
    new_answer = DecisionOutput.model_validate_json(new.messages[2].content, strict=True)
    assert new_task.state.model_dump(mode="json") == task["state"]
    assert new_task.questions[0].criteria[-1] == _KNOWN_ISSUE_CRITERIA[0].replace(
        "missing_information", "no_supported_answer"
    )
    assert new_answer.results[0].answerability.issues == [
        "no_supported_answer",
        "conflicting_information",
        "multiple_valid_options",
    ]
    for old_result, new_result in zip(
        answer["results"], new_answer.model_dump(mode="json")["results"], strict=True
    ):
        assert old_result["answer"] == new_result["answer"]
        assert old_result["explanation"] == new_result["explanation"]
    assert old.model_dump(exclude={"messages"}) == new.model_dump(exclude={"messages"})
    assert old.messages[1].content == data["messages"][1]["content"]


@pytest.mark.parametrize(
    "defect",
    [
        "new-code",
        "duplicates",
        "version",
        "extra",
        "unknown-option",
        "criteria-string",
        "criteria-dict",
    ],
)
def test_invalid_legacy_records_are_rejected_before_relabeling(defect: str) -> None:
    record = _legacy(_seed_records()[0])
    data = record.model_dump(mode="json")
    answer = json.loads(data["messages"][2]["content"])
    if defect == "new-code":
        answer["results"][0]["answerability"]["issues"] = ["no_supported_answer"]
    elif defect == "duplicates":
        answer["results"][0]["answerability"]["issues"] = ["missing_information"] * 2
    elif defect == "version":
        answer["schemaVersion"] = True
    elif defect == "extra":
        answer["invented"] = "data"
    elif defect.startswith("criteria-"):
        task = json.loads(data["messages"][1]["content"])
        task["questions"][0]["criteria"] = "abc" if defect == "criteria-string" else {"fact": "x"}
        data["messages"][1]["content"] = json.dumps(task)
    else:
        answer["results"][0]["answer"] = {"optionId": "invented-label"}
    data["messages"][2]["content"] = json.dumps(answer)
    with pytest.raises(ModelError, match="cannot be upgraded safely"):
        upgrade_record(DataRecord.model_validate(data, strict=True))


def test_normal_consumption_rejects_legacy_and_retains_general_chat_support() -> None:
    record = _legacy(_seed_records()[0])
    with pytest.raises(ModelError, match="current decision contract"):
        validate_native_record(record)
    validate_native_record(upgrade_record(record))
    ordinary = record.model_dump(mode="json")
    ordinary["tags"] = []
    ordinary["messages"][1]["content"] = "Please summarize this ordinary message."
    ordinary["messages"][2]["content"] = "An ordinary summary."
    validate_native_record(DataRecord.model_validate(ordinary, strict=True))


def test_dataset_upgrade_is_offline_immutable_split_preserving_and_verifies_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [_legacy(record) for record in _seed_records()]
    families = {
        record.familyId: FrozenFamilyAssignment(split=split, sourceSplits=["authored"])
        for index, (record, split) in enumerate(
            zip(records, ("train", "validation", "calibration", "test"), strict=True)
        )
    }
    families = dict(sorted(families.items()))
    # Materialize a historical fixture through the unchanged outer dataset contract.
    with monkeypatch.context() as fixture:
        fixture.setattr("foliqant_model.data.validate_native_record", lambda record: None)
        parent_manifest = _publish(
            tmp_path / "parent",
            "native-decisions",
            records,
            [
                ResolvedSourceDeclaration(
                    id="foliqant-decisions",
                    license="Test-1.0",
                    licenseEvidence="Fixture terms",
                    trainingAllowed=True,
                    sharedTrainingAllowed=True,
                    redistributionAllowed=True,
                    privacy="public",
                    attribution="Fixture",
                    commercialUse="allowed",
                    restrictions=["Retain notice"],
                )
            ],
            families,
            17,
        )
    parent = tmp_path / "parent/datasets/native-decisions"
    before = _inventory(parent)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline upgrade attempted network")

    monkeypatch.setattr(socket, "socket", no_network)
    result = upgrade_decision_data(parent, tmp_path / "upgraded")
    assert result["records"] == 4
    assert result["parentArtifactId"] == parent_manifest.root.artifactId
    assert _inventory(parent) == before
    assert upgrade_decision_data(parent, tmp_path / "upgraded") == result
    child = load_verified_artifact(Path(str(result["datasetPath"])))
    assert child.root.sourceRights == parent_manifest.root.sourceRights
    assert child.root.kind == parent_manifest.root.kind == "dataset"
    assert child.root.details.datasetConfig.frozenFamilies == families
    assert {key: value.split for key, value in child.root.details.assignments.items()} == {
        key: value.split for key, value in parent_manifest.root.details.assignments.items()
    }
    # Matching hashes cannot make an arbitrary label/explanation edit a schema upgrade.
    binding = json.loads(
        (Path(str(result["datasetPath"])) / "decision-upgrade-provenance.json").read_text()
    )
    forged_inputs = tmp_path / "forged-inputs"
    shutil.copytree(tmp_path / "upgraded/publication-inputs", forged_inputs)
    source_path = forged_inputs / "records/foliqant-decisions.jsonl"
    forged_rows = [json.loads(line) for line in source_path.read_text().splitlines()]
    forged = forged_rows[0]
    changed_answer = json.loads(forged["messages"][-1]["content"])
    changed_answer["results"][0]["explanation"]["summary"] = "Invented replacement explanation."
    forged["messages"][-1]["content"] = json.dumps(changed_answer)
    for row in binding["records"]:
        if row["recordId"] == forged["id"]:
            row["recordSha256"] = canonical_digest(forged)
    source_path.write_text("".join(json.dumps(row) + "\n" for row in forged_rows))
    with pytest.raises(ModelError, match="ancestry is invalid"):
        prepare_dataset(forged_inputs / "dataset.json", tmp_path / "forged", provenance=binding)
    (parent / "records/train.jsonl").write_text("changed\n")
    with pytest.raises(ModelError, match="ancestry is invalid"):
        load_verified_artifact(Path(str(result["datasetPath"])))


def test_current_native_rejects_duplicate_json_keys() -> None:
    record = upgrade_record(_legacy(_seed_records()[0]))
    payload = record.model_dump(mode="json")
    payload["messages"][1]["content"] = payload["messages"][1]["content"].replace(
        '"schemaVersion":2', '"schemaVersion":1,"schemaVersion":2'
    )
    with pytest.raises(ModelError):
        validate_native_record(DataRecord.model_validate(payload, strict=True))


def test_historical_system_variant_retains_unrelated_instructions() -> None:
    record = _legacy(_seed_records()[0])
    data = record.model_dump(mode="json")
    start = data["messages"][0]["content"].index("For a predicate, true requires")
    end = data["messages"][0]["content"].index("Status answerable means")
    system = data["messages"][0]["content"]
    data["messages"][0]["content"] = system[:start] + system[end:]
    new = upgrade_record(DataRecord.model_validate(data, strict=True))
    assert "For a predicate, true requires" not in new.messages[0].content
    assert "missing_information" not in new.messages[0].content
    assert "schemaVersion 2" in new.messages[0].content


def test_unrecognized_issue_instruction_requires_explicit_review() -> None:
    payload = _legacy(_seed_records()[0]).model_dump(mode="json")
    task = json.loads(payload["messages"][1]["content"])
    task["questions"][0]["criteria"].append("Use no_matching_option, not missing_information.")
    payload["messages"][1]["content"] = json.dumps(task)
    with pytest.raises(ModelError, match="cannot be upgraded safely"):
        upgrade_record(DataRecord.model_validate(payload, strict=True))

"""Explicit, offline V1-to-V2 native decision dataset upgrade.

Legacy-only constraints are checked before current shape and semantic validation.
Source prose, explanations,
rights, record identity, generation ancestry and frozen partitions remain unchanged.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import cast

from foliqant.decisions import DecisionInput, DecisionOutput, validate_decision_output

from ..artifacts import load_verified_artifact, require_disjoint_output
from ..configuration import parse_json
from ..contracts.base import canonical_digest
from ..contracts.inputs import ChatMessage, DataRecord, DatasetConfig
from ..data import prepare_dataset
from ..errors import ModelError
from ..workspace import check_workspace_git_policy
from .legacy_decision_prompt import LEGACY_SYSTEM
from .migration import _inventory
from .storage import (
    canonical_bytes,
    ensure_directory,
    load_object,
    run_lock,
    store_object,
    write_once,
)

UPGRADE_VERSION = "native-decision-contract-v1-to-v2-1"
_ISSUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:missing_information|no_matching_option)(?![A-Za-z0-9_])"
)

# Frozen output instructions keep historical upgrade identities independently verifiable.
_UPGRADED_ISSUE_INSTRUCTIONS = (
    "Report every independently supported issue: no_supported_answer when the allowed "
    "evidence supports no permitted answer, including absent facts, unresolved referents, "
    "and facts outside the catalog; conflicting_information for unresolved incompatible "
    "facts; multiple_valid_options only for multiple positively supported answers beyond "
    "cardinality. List each issue code only once; describe separate missing facts in "
    "missingFacts. Every returned null request category requires no_supported_answer, even"
    " for a conditional branch. Explain the concrete cause in the explanation. "
)

_KNOWN_ISSUE_CRITERIA = (
    "Do not invent exclusive boundaries for overlapping categories. Multiple positively "
    "supported options require not_answerable with multiple_valid_options; missing "
    "distinguishing facts require missing_information.",
    "Erfinde bei überlappenden Kategorien keine exklusive Abgrenzung. Mehrere positiv "
    "gestützte Optionen erfordern not_answerable mit multiple_valid_options; fehlende "
    "Unterscheidungsmerkmale erfordern missing_information.",
)


def _upgrade_instruction(value: str) -> str:
    if not _ISSUE_TOKEN.search(value):
        return value
    if value not in _KNOWN_ISSUE_CRITERIA:
        raise ValueError("unrecognized issue-bearing task instruction requires review")
    return value.replace("missing_information", "no_supported_answer")


def upgrade_record(record: DataRecord) -> DataRecord:
    """Strictly validate a V1 record and change only contract-bearing fields."""
    try:
        if len(record.messages) != 3 or [message.role for message in record.messages] != [
            "system",
            "user",
            "assistant",
        ]:
            raise ValueError("native message layout required")
        old_task = parse_json(record.messages[1].content)
        old_answer = parse_json(record.messages[2].content)
        for payload in (old_task, old_answer):
            if (
                not isinstance(payload, dict)
                or type(payload.get("schemaVersion")) is not int
                or payload["schemaVersion"] != 1
            ):
                raise ValueError("explicit native V1 contract required")
        assert isinstance(old_task, dict) and isinstance(old_answer, dict)
        for result in old_answer["results"]:
            issues = result["answerability"]["issues"]
            if (
                not isinstance(issues, list)
                or any(not isinstance(issue, str) for issue in issues)
                or len(issues) != len(set(issues))
                or not set(issues)
                <= {
                    "missing_information",
                    "no_matching_option",
                    "conflicting_information",
                    "multiple_valid_options",
                }
            ):
                raise ValueError("invalid legacy issue array")
            if (
                result["type"] == "request_units"
                and result["answer"] is not None
                and any(unit["categoryId"] is None for unit in result["answer"]["units"])
                and "no_matching_option" not in issues
            ):
                raise ValueError("invalid legacy null category")
        system = record.messages[0].content
        predicate_paragraph = (
            "For a predicate, true requires evidence for the proposition, false requires evidence "
            "for its negation, and unknown means neither is established. An explicit statement of "
            "absence can support false; absent evidence alone cannot. Predicate unknown must use "
            "not_answerable or undetermined, never answerable. A choice asking for the relation "
            "between two texts may be answerable with a neutral option; "
            "this is not predicate unknown. "
        )
        # Historical accepted rows also retain the preceding native prompt revision.
        recognized = (LEGACY_SYSTEM, LEGACY_SYSTEM.replace(predicate_paragraph, ""))
        if not any(system.startswith(prefix) for prefix in recognized):
            raise ValueError("unknown native system prompt")
        old_issue_start = LEGACY_SYSTEM.index("Report every independently supported issue:")
        old_issue_end = LEGACY_SYSTEM.index("Return only explicitly supported relationships.")
        upgraded_system = system.replace(
            "schemaVersion 1 and one result", "schemaVersion 2 and one result"
        ).replace(LEGACY_SYSTEM[old_issue_start:old_issue_end], _UPGRADED_ISSUE_INSTRUCTIONS)
        task = deepcopy(old_task)
        task["schemaVersion"] = 2
        # Validate before iterating criteria: a string/dict must never become a valid list.
        DecisionInput.model_validate(task, strict=True)
        for question in task["questions"]:
            question["prompt"] = _upgrade_instruction(question["prompt"])
            question["criteria"] = [
                _upgrade_instruction(criterion) for criterion in question["criteria"]
            ]
        answer = deepcopy(old_answer)
        answer["schemaVersion"] = 2
        for result in answer["results"]:
            result["answerability"]["issues"] = list(
                dict.fromkeys(
                    "no_supported_answer"
                    if issue in {"missing_information", "no_matching_option"}
                    else issue
                    for issue in result["answerability"]["issues"]
                )
            )
        new_task = DecisionInput.model_validate(task, strict=True)
        new_answer = DecisionOutput.model_validate(answer, strict=True)
        if validate_decision_output(new_task, new_answer):
            raise ValueError("invalid upgraded semantics")
        messages = [
            ChatMessage(role="system", content=upgraded_system),
            ChatMessage(role="user", content=canonical_bytes(task).decode().strip()),
            ChatMessage(role="assistant", content=canonical_bytes(answer).decode().strip()),
        ]
        return DataRecord.model_validate({**record.model_dump(mode="python"), "messages": messages})
    except (ValueError, IndexError, KeyError, TypeError) as error:
        raise ModelError(
            "DATA_RECORD_INVALID", "Native V1 record cannot be upgraded safely"
        ) from error


def upgrade_decision_data(from_dataset: Path, output: Path) -> dict[str, object]:
    """Publish a verified immutable descendant, without inference or label synthesis."""
    parent = from_dataset.expanduser().absolute()
    destination = output.expanduser().absolute()
    require_disjoint_output(destination, [parent])
    check_workspace_git_policy(destination)
    before = _inventory(parent)
    manifest = load_verified_artifact(parent)
    if manifest.root.kind != "dataset":
        raise ModelError("ARGUMENT_INVALID", "Upgrade requires a native dataset artifact")
    config = manifest.root.details.datasetConfig
    if config.frozenFamilies is None:
        raise ModelError("DATA_PARTITION_INVALID", "Native upgrade requires frozen families")
    records: list[DataRecord] = []
    provenance_rows: list[dict[str, object]] = []
    for split in ("train", "validation", "calibration", "test"):
        reference = getattr(manifest.root.details.recordFiles, split)
        for line in (parent / reference.path).read_text(encoding="utf-8").splitlines():
            old = DataRecord.model_validate(parse_json(line), strict=True)
            new = upgrade_record(old)
            records.append(new)
            provenance_rows.append(
                {
                    "recordId": old.id,
                    "split": split,
                    "parentRecordSha256": canonical_digest(old.model_dump(mode="json")),
                    "recordSha256": canonical_digest(new.model_dump(mode="json")),
                }
            )
    binding: dict[str, object] = {
        "schemaVersion": 1,
        "operation": UPGRADE_VERSION,
        "parentDataset": str(parent),
        "parentArtifactId": manifest.root.artifactId,
        "parentFiles": before,
        "parentManifest": manifest.model_dump(mode="json"),
        "records": provenance_rows,
    }
    if _inventory(parent) != before:
        raise ModelError("INTEGRITY_FAILED", "Upgrade parent changed during validation")
    ensure_directory(destination)
    with run_lock(destination):
        completed_path = destination / "upgrade-complete.json"
        if completed_path.exists():
            completed = cast(dict[str, object], load_object(completed_path))
            files = _inventory(destination)
            files.pop("upgrade-complete.json", None)
            if completed.get("files") != files or completed.get(
                "bindingSha256"
            ) != canonical_digest(binding):
                raise ModelError("INTEGRITY_FAILED", "Upgrade output belongs to different inputs")
            load_verified_artifact(destination / "datasets/native-decisions")
            return cast(dict[str, object], completed["result"])
        inputs = destination / "publication-inputs"
        ensure_directory(inputs / "records")
        declarations = []
        for source in config.sources:
            relative = "records/" + source.id + ".jsonl"
            write_once(
                inputs / relative,
                b"".join(
                    canonical_bytes(record.model_dump(mode="json"))
                    for record in sorted(records, key=lambda item: item.id)
                    if record.sourceId == source.id
                ),
            )
            declarations.append({**source.model_dump(mode="json"), "path": relative})
        recipe = DatasetConfig.model_validate(
            {
                **config.model_dump(mode="json"),
                "sources": declarations,
            },
            strict=True,
        )
        recipe_path = inputs / "dataset.json"
        write_once(recipe_path, canonical_bytes(recipe.model_dump(mode="json")))
        dataset_path = destination / "datasets/native-decisions"
        ensure_directory(dataset_path.parent)
        if dataset_path.exists():
            child = load_verified_artifact(dataset_path)
            embedded = parse_json((dataset_path / "decision-upgrade-provenance.json").read_text())
            if embedded != binding:
                raise ModelError("INTEGRITY_FAILED", "Existing upgrade dataset differs")
        else:
            child = prepare_dataset(recipe_path, dataset_path, provenance=binding)
        assert child.root.kind == "dataset"
        if child.root.sourceRights != manifest.root.sourceRights or {
            key: value.split for key, value in child.root.details.assignments.items()
        } != {key: value.split for key, value in manifest.root.details.assignments.items()}:
            raise ModelError("INTEGRITY_FAILED", "Upgrade changed rights or frozen splits")
        if _inventory(parent) != before:
            raise ModelError("INTEGRITY_FAILED", "Upgrade parent changed during publication")
        report_path = destination / "upgrade-report.json"
        result: dict[str, object] = {
            "command": "upgrade-decision-data",
            "datasetPath": str(dataset_path),
            "artifactId": child.root.artifactId,
            "parentArtifactId": manifest.root.artifactId,
            "reportPath": str(report_path),
            "records": len(records),
        }
        store_object(
            report_path,
            {
                **result,
                "operation": UPGRADE_VERSION,
                "modelCalls": 0,
                "partitions": child.root.details.partitions.model_dump(mode="json"),
            },
        )
        store_object(
            completed_path,
            {
                "schemaVersion": 1,
                "bindingSha256": canonical_digest(binding),
                "files": _inventory(destination),
                "result": result,
            },
        )
        return result

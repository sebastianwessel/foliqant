"""Current native decision validation at ordinary dataset consumption boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from foliqant.decisions import DecisionInput, DecisionOutput, validate_decision_output

from .configuration import parse_json
from .contracts.artifacts import ArtifactManifest
from .contracts.inputs import DataRecord
from .errors import ModelError


def validate_native_record(record: DataRecord, *, required: bool = False) -> None:
    """Validate tagged or structurally native records without restricting ordinary chat data."""
    native = required or any(tag.startswith("native-decision") for tag in record.tags)
    try:
        task_value = json.loads(record.messages[-2].content)
    except (ValueError, IndexError):
        task_value = None
    native = native or (
        isinstance(task_value, dict) and "state" in task_value and "questions" in task_value
    )
    if not native:
        return
    try:
        task = DecisionInput.model_validate(parse_json(record.messages[-2].content), strict=True)
        answer = DecisionOutput.model_validate(parse_json(record.messages[-1].content), strict=True)
        if validate_decision_output(task, answer):
            raise ValueError("invalid native result")
    except (ValueError, IndexError) as error:
        raise ModelError(
            "DATA_RECORD_INVALID",
            "Native data requires the current decision contract; use upgrade-decision-data for V1",
        ) from error


def validate_native_dataset(path: Path, manifest: ArtifactManifest) -> None:
    """Reject obsolete native records before training or evaluation starts any backend work."""
    if manifest.root.kind != "dataset":
        return
    for split in ("train", "validation", "calibration", "test"):
        reference = getattr(manifest.root.details.recordFiles, split)
        for line in (path / reference.path).read_text(encoding="utf-8").splitlines():
            validate_native_record(DataRecord.model_validate_json(line, strict=True))


def verify_upgrade_provenance(path: Path, manifest: ArtifactManifest) -> None:
    """Check a schema-upgraded dataset's immutable parent and exact row lineage offline."""
    from .artifacts import load_verified_artifact, sha256_file
    from .configuration import parse_json
    from .contracts.base import canonical_digest
    from .curation.decision_upgrade import UPGRADE_VERSION, upgrade_record

    provenance_path = path / "decision-upgrade-provenance.json"
    if not provenance_path.exists():
        return
    try:
        value = parse_json(provenance_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or manifest.root.kind != "dataset":
            raise ValueError("invalid upgrade provenance")
        if (
            type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1
            or value.get("operation") != UPGRADE_VERSION
        ):
            raise ValueError("unknown upgrade operation")
        parent = Path(value["parentDataset"])
        # The explicit upgrade consumes V1 only; chained upgrade provenance is invalid.
        if parent.resolve() == path.resolve() or (parent / provenance_path.name).exists():
            raise ValueError("cyclic or non-V1 upgrade parent")
        files = {}
        for candidate in sorted(parent.rglob("*")):
            if candidate.is_symlink():
                raise ValueError("unsafe parent file")
            if candidate.is_file() and candidate.name != "run.lock":
                files[str(candidate.relative_to(parent))] = sha256_file(candidate)[1]
        if files != value["parentFiles"]:
            raise ValueError("upgrade parent changed")
        original = load_verified_artifact(parent)
        if (
            original.root.kind != "dataset"
            or original.root.artifactId != value["parentArtifactId"]
            or original.model_dump(mode="json") != value["parentManifest"]
        ):
            raise ValueError("upgrade parent identity mismatch")
        if (
            manifest.root.sourceRights != original.root.sourceRights
            or manifest.root.details.datasetConfig.frozenFamilies
            != original.root.details.datasetConfig.frozenFamilies
        ):
            raise ValueError("upgrade rights or families changed")
        rows = []
        for split in ("train", "validation", "calibration", "test"):
            old_ref = getattr(original.root.details.recordFiles, split)
            new_ref = getattr(manifest.root.details.recordFiles, split)
            old_records = [
                DataRecord.model_validate_json(line, strict=True)
                for line in (parent / old_ref.path).read_text().splitlines()
            ]
            new_records = [
                DataRecord.model_validate_json(line, strict=True)
                for line in (path / new_ref.path).read_text().splitlines()
            ]
            if len(old_records) != len(new_records):
                raise ValueError("upgrade row count changed")
            for old, new in zip(old_records, new_records, strict=True):
                if old.model_dump(exclude={"messages"}) != new.model_dump(exclude={"messages"}):
                    raise ValueError("upgrade record lineage changed")
                if new != upgrade_record(old):
                    raise ValueError("upgrade does not match deterministic translation")
                validate_native_record(new, required=True)
                rows.append(
                    {
                        "recordId": old.id,
                        "split": split,
                        "parentRecordSha256": canonical_digest(old.model_dump(mode="json")),
                        "recordSha256": canonical_digest(new.model_dump(mode="json")),
                    }
                )
        if rows != value["records"]:
            raise ValueError("upgrade row provenance changed")
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise ModelError("INTEGRITY_FAILED", "Decision upgrade ancestry is invalid") from error

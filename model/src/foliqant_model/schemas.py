"""Deterministic public schema export from the canonical contract classes."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pydantic import BaseModel

from .contracts import (
    ArtifactManifest,
    CliFailure,
    CliSuccess,
    DataRecord,
    DatasetConfig,
    EvaluationConfig,
    FrozenFamilyAssignment,
    GenerationProvenance,
    LeakageEntry,
    LockOwner,
    Prediction,
    RunState,
    SetupProfile,
    SetupReceipt,
    TrainConfig,
    WorkerRequest,
    WorkerResult,
)
from .contracts.cli import SchemaResult
from .curation.contracts import CandidateJob, CandidateOutcome, CurationConfig, CurationPlan
from .curation.migration_contracts import MigrationPlan
from .curation.projection_contracts import ProjectionPlan, ProjectionReport
from .errors import ModelError

SCHEMAS: dict[str, type[BaseModel]] = {
    "artifact-manifest.schema.json": ArtifactManifest,
    "candidate-job.schema.json": CandidateJob,
    "candidate-outcome.schema.json": CandidateOutcome,
    "cli-failure.schema.json": CliFailure,
    "cli-success.schema.json": CliSuccess,
    "curation-config.schema.json": CurationConfig,
    "curation-plan.schema.json": CurationPlan,
    "data-record.schema.json": DataRecord,
    "dataset-config.schema.json": DatasetConfig,
    "evaluation-config.schema.json": EvaluationConfig,
    "frozen-family-assignment.schema.json": FrozenFamilyAssignment,
    "generation-provenance.schema.json": GenerationProvenance,
    "leakage-entry.schema.json": LeakageEntry,
    "lock-owner.schema.json": LockOwner,
    "migration-plan.schema.json": MigrationPlan,
    "prediction.schema.json": Prediction,
    "projection-plan.schema.json": ProjectionPlan,
    "projection-report.schema.json": ProjectionReport,
    "run-state.schema.json": RunState,
    "setup-profile.schema.json": SetupProfile,
    "setup-receipt.schema.json": SetupReceipt,
    "train-config.schema.json": TrainConfig,
    "worker-request.schema.json": WorkerRequest,
    "worker-result.schema.json": WorkerResult,
}


def schema_bytes(filename: str, model: type[BaseModel]) -> bytes:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://foliqant.local/schemas/model/{filename}"
    return (
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def export_schemas(directory: Path, *, check: bool = False) -> SchemaResult:
    """Export into a new/empty directory, or compare an existing schema set."""
    directory = directory.absolute()
    if directory.is_symlink():
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Schema directory must not be a symlink")
    if check:
        if not directory.is_dir():
            raise ModelError("SCHEMA_DRIFT", "Schema directory is missing")
        actual = {path.name for path in directory.glob("*.schema.json")}
        if actual != set(SCHEMAS):
            raise ModelError("SCHEMA_DRIFT", "Schema file set differs from the contracts")
        for filename, model in SCHEMAS.items():
            path = directory / filename
            metadata = path.lstat()
            expected = schema_bytes(filename, model)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(expected):
                raise ModelError("SCHEMA_DRIFT", "Schema content differs from the contracts")
            # Use the same no-follow descriptor hashing as immutable artifacts.
            import hashlib

            from .artifacts import sha256_file

            if sha256_file(path) != (len(expected), hashlib.sha256(expected).hexdigest()):
                raise ModelError("SCHEMA_DRIFT", "Schema content differs from the contracts")
    else:
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ModelError("OUTPUT_EXISTS", "Schema output directory must be new or empty")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for filename, model in SCHEMAS.items():
            try:
                descriptor = os.open(
                    directory / filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError as error:
                raise ModelError(
                    "OUTPUT_EXISTS", "Schema output appeared during generation"
                ) from error
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(schema_bytes(filename, model))
                stream.flush()
                os.fsync(stream.fileno())
    return SchemaResult(
        command="schema",
        mode="check" if check else "output",
        directory=str(directory),
        schemaCount=len(SCHEMAS),
        driftCount=0,
    )

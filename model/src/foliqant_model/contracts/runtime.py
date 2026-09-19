"""Run workspace and lock-file contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .base import (
    CliErrorCode,
    Command,
    ConfigIdentity,
    ContractModel,
    Digest,
    LocalPath,
    NonEmptyStr,
    RunId,
    SafePath,
    SchemaVersion,
    Timestamp,
)


class LockOwner(ContractModel):
    runId: RunId
    pid: Annotated[StrictInt, Field(ge=1)]
    workspacePath: LocalPath


class RunFailure(ContractModel):
    code: CliErrorCode
    message: NonEmptyStr


class RunState(ContractModel):
    schemaVersion: SchemaVersion
    runId: RunId
    command: Command
    startedAt: Timestamp
    updatedAt: Timestamp
    status: Literal["running", "completed", "failed", "interrupted"]
    pid: Annotated[StrictInt, Field(ge=1)]
    configuration: ConfigIdentity | None
    parentArtifactIds: list[Digest]
    datasetArtifactId: Digest | None
    failure: RunFailure | None
    logPath: SafePath
    finalizedArtifactId: Digest | None

    @model_validator(mode="after")
    def validate_state(self) -> RunState:
        if len(self.parentArtifactIds) != len(set(self.parentArtifactIds)):
            raise ValueError("parentArtifactIds must be unique")
        started = datetime.fromisoformat(self.startedAt[:-1] + "+00:00")
        updated = datetime.fromisoformat(self.updatedAt[:-1] + "+00:00")
        if updated < started:
            raise ValueError("updatedAt must not precede startedAt")
        if self.status == "running":
            if self.failure is not None or self.finalizedArtifactId is not None:
                raise ValueError("running state has no failure or final artifact")
        elif self.status == "completed":
            if self.failure is not None or self.finalizedArtifactId is None:
                raise ValueError("completed state requires only a final artifact")
        else:
            if self.failure is None or self.finalizedArtifactId is not None:
                raise ValueError("failed/interrupted state requires only a failure")
            if (self.status == "interrupted") != (self.failure.code == "INTERRUPTED"):
                raise ValueError("interrupted status and error code must agree")
        return self

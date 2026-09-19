"""Pinned local setup assets, provenance receipt and CLI result."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator

from .base import (
    Commit,
    ContractModel,
    Digest,
    FileEntry,
    LocalPath,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    RepoId,
    SafePath,
    SchemaVersion,
)


def _https_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("must be a credential-free HTTPS URL without fragments")
    try:
        _ = parts.port
    except ValueError as error:
        raise ValueError("invalid HTTPS port") from error
    return value


HttpsUrl = Annotated[NonEmptyStr, AfterValidator(_https_url)]


class SetupAsset(ContractModel):
    """One exact remote file, verified before entering the local cache."""

    path: SafePath
    url: HttpsUrl
    size: PositiveInt
    sha256: Digest


class SetupProfile(ContractModel):
    """The built-in, immutable diagnostic setup recipe."""

    schemaVersion: SchemaVersion
    name: Literal["smoke-v1"]
    modelRepo: RepoId
    modelRevision: Commit
    modelLicense: NonEmptyStr
    datasetRepo: HttpsUrl
    datasetRevision: Commit
    datasetLicense: NonEmptyStr
    converter: Literal["banking77-smoke-v1"]
    assets: Annotated[list[SetupAsset], Field(min_length=1)]

    @model_validator(mode="after")
    def asset_order(self) -> "SetupProfile":
        paths = [asset.path for asset in self.assets]
        if paths != sorted(set(paths)):
            raise ValueError("asset paths must be sorted and unique")
        return self


class SetupReceipt(ContractModel):
    """Successful acquisition, conversion and prepared artifact identities."""

    schemaVersion: SchemaVersion
    profileName: Literal["smoke-v1"]
    profileSha256: Digest
    assets: list[FileEntry]
    generatedFiles: list[FileEntry]
    modelArtifactId: Digest
    sharedDatasetArtifactId: Digest
    customerDatasetArtifactId: Digest

    @model_validator(mode="after")
    def file_order(self) -> "SetupReceipt":
        for files in (self.assets, self.generatedFiles):
            paths = [item.path for item in files]
            if paths != sorted(set(paths)):
                raise ValueError("receipt paths must be sorted and unique")
        return self


class SetupResult(ContractModel):
    """Locations ready for an explicit training command; setup never trains."""

    command: Literal["setup"]
    workspacePath: LocalPath
    profilePath: LocalPath
    receiptPath: LocalPath
    profileSha256: Digest
    downloadedFiles: NonNegativeInt
    reusedFiles: NonNegativeInt
    modelPath: LocalPath
    sharedDatasetPath: LocalPath
    customerDatasetPath: LocalPath
    trainConfigPath: LocalPath
    evaluationConfigPath: LocalPath

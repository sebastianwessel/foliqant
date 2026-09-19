"""Redacted, typed failures shared by lifecycle commands."""

from __future__ import annotations

from .contracts.base import CliErrorCode, Command
from .contracts.cli import _CATEGORY_EXIT, _ERROR_CATEGORY, CliFailure, ErrorLocation


class ModelError(Exception):
    """A public failure whose message must contain no source data or secrets."""

    def __init__(
        self,
        code: CliErrorCode,
        message: str,
        *,
        location: ErrorLocation | None = None,
        artifact_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.location = location
        self.artifact_id = artifact_id

    @property
    def exit_code(self) -> int:
        """Return the stable process exit code for this error category."""
        return _CATEGORY_EXIT[_ERROR_CATEGORY[self.code]]

    def as_failure(self, command: Command | None) -> CliFailure:
        """Build the canonical CLI envelope; never include exception internals."""
        return CliFailure.model_validate(
            {
                "schemaVersion": 1,
                "ok": False,
                "command": command,
                "exitCode": self.exit_code,
                "error": {
                    "code": self.code,
                    "category": _ERROR_CATEGORY[self.code],
                    "message": self.message,
                    "location": self.location.model_dump(mode="json") if self.location else None,
                    "artifactId": self.artifact_id,
                },
            }
        )

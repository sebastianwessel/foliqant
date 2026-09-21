"""Content-free compiler diagnostics safe for logs and boundary adapters."""

from foliqant.core.plan import SourceLocation


class CompilationError(Exception):
    """A stable reason and source coordinate without authored values or snippets."""

    def __init__(self, reason: str, location: SourceLocation) -> None:
        self.reason = reason
        self.location = location
        super().__init__("The workflow configuration is invalid.")

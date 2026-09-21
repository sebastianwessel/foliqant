"""Shared private model workspace selection and Git exclusion checks."""

import shutil
import subprocess
from pathlib import Path

from .errors import ModelError


def model_workspace(workspace: Path | None = None) -> Path:
    """Select an explicit workspace or .foliqant in the working directory.

    Repository wrappers run from the checkout root. Installed CLI users control
    the working directory or pass --workspace; no home-directory search occurs.
    """
    selected = workspace if workspace is not None else Path.cwd() / ".foliqant"
    selected = selected.expanduser().absolute()
    check_workspace_git_policy(selected)
    return selected


def check_workspace_git_policy(workspace: Path) -> None:
    """Refuse a data workspace in a worktree unless the whole directory is ignored."""
    root = next(
        (parent for parent in (workspace, *workspace.parents) if (parent / ".git").exists()), None
    )
    if root is None:
        return
    executable = shutil.which("git")
    if executable is None:
        raise ModelError("ENVIRONMENT_UNSUPPORTED", "Git is needed to check workspace exclusions")
    relative = workspace.relative_to(root).as_posix()
    if relative == ".":
        raise ModelError("ARGUMENT_INVALID", "A Git worktree root cannot be a data workspace")
    try:
        ignored = subprocess.run(
            [
                executable,
                "-C",
                str(root),
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                relative + "/",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if ignored.returncode == 1:
            raise ModelError("ARGUMENT_INVALID", "The data workspace must be ignored by Git")
        if ignored.returncode != 0:
            raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot verify the Git workspace exclusion")
        tracked = subprocess.run(
            [executable, "-C", str(root), "ls-files", "-z", "--", relative + "/"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if tracked.returncode != 0:
            raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot inspect tracked workspace files")
        if tracked.stdout:
            raise ModelError("ARGUMENT_INVALID", "The data workspace contains tracked files")
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelError("ENVIRONMENT_UNSUPPORTED", "Cannot verify Git workspace safety") from error

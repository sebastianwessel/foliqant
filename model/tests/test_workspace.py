"""Workspace defaults stay local and never bypass Git exclusion checks."""

import subprocess
from pathlib import Path

import pytest

from foliqant_model.errors import ModelError
from foliqant_model.workspace import model_workspace


def test_default_is_current_directory_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert model_workspace() == tmp_path / ".foliqant"
    assert not (tmp_path / ".foliqant").exists()
    assert model_workspace(Path("custom")) == tmp_path / "custom"


def test_default_requires_ignore_and_rejects_force_tracked_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    with pytest.raises(ModelError):
        model_workspace()
    (tmp_path / ".gitignore").write_text("/.foliqant/\n")
    assert model_workspace() == tmp_path / ".foliqant"
    directory = tmp_path / ".foliqant"
    directory.mkdir()
    (directory / "private.txt").write_text("synthetic")
    subprocess.run(["git", "add", "-f", ".foliqant/private.txt"], check=True)
    with pytest.raises(ModelError):
        model_workspace()

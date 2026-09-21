from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from foliqant_model.contracts import canonical_digest
from foliqant_model.contracts.setup import SetupProfile
from foliqant_model.errors import ModelError
from foliqant_model.setup import _write_or_verify, builtin_profile, run_setup


def test_profile_has_only_pinned_assets() -> None:
    profile = builtin_profile()
    assert len(profile.assets) == 14
    assert sum(asset.size for asset in profile.assets) == 273548800
    assert canonical_digest(profile.model_dump(mode="json")) == (
        "2638284b4ec91b0a2368e0a35266b7b0b20a1046678856474ef323fc9aa65594"
    )
    assert all(
        profile.datasetRevision in asset.url or profile.modelRevision in asset.url
        for asset in profile.assets
    )


def test_generated_files_are_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    _write_or_verify(target, b"first")
    _write_or_verify(target, b"first")
    with pytest.raises(ModelError) as failure:
        _write_or_verify(target, b"changed")
    assert failure.value.code == "INTEGRITY_FAILED"
    assert target.read_bytes() == b"first"


def test_missing_offline_setup_leaves_no_success_receipt(tmp_path: Path) -> None:
    with pytest.raises(ModelError) as failure:
        run_setup(tmp_path / "workspace", offline=True)
    assert failure.value.code == "INPUT_NOT_FOUND"
    assert not list(tmp_path.rglob("setup.json"))
    assert not list(tmp_path.rglob("*.lock"))


def test_interrupted_partial_download_never_finalizes_setup_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = b"complete"
    profile = SetupProfile.model_validate(
        {
            "schemaVersion": 1,
            "name": "smoke-v1",
            "modelRepo": "fixture/model",
            "modelRevision": "a" * 40,
            "modelLicense": "fixture",
            "datasetRepo": "https://example.org/data",
            "datasetRevision": "b" * 40,
            "datasetLicense": "fixture",
            "converter": "banking77-smoke-v1",
            "assets": [
                {
                    "path": "model/config.json",
                    "url": "https://example.org/config.json",
                    "size": len(expected),
                    "sha256": hashlib.sha256(expected).hexdigest(),
                }
            ],
        }
    )

    class InterruptedResponse:
        reads = 0

        def __enter__(self) -> InterruptedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://example.org/config.json"

        def read(self, _size: int) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return b"part"
            raise KeyboardInterrupt

    monkeypatch.setattr("foliqant_model.setup.builtin_profile", lambda: profile)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open",
        lambda *_args, **_kwargs: InterruptedResponse(),
    )
    workspace = tmp_path / "workspace"
    with pytest.raises(KeyboardInterrupt):
        run_setup(workspace)

    assert not list(workspace.rglob("setup.json"))
    assert not list(workspace.rglob("*.lock"))
    assert not list(workspace.rglob(".download-*"))
    assert not list((workspace / "setups").glob("*/downloads/model/config.json"))


def test_git_workspace_policy(tmp_path: Path) -> None:
    import subprocess

    from foliqant_model.workspace import check_workspace_git_policy

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("/ignored/\n")
    check_workspace_git_policy(tmp_path / "ignored")
    with pytest.raises(ModelError) as failure:
        check_workspace_git_policy(tmp_path / "unignored")
    assert failure.value.code == "ARGUMENT_INVALID"
    assert not (tmp_path / "unignored").exists()
    with pytest.raises(ModelError):
        check_workspace_git_policy(tmp_path)


@pytest.mark.parametrize("arguments", [["--workspace", "scratch"], ["--workspace=scratch"]])
def test_wrapper_resolves_workspace_against_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    import json
    import os
    import subprocess
    import sys

    commands = tmp_path / "commands"
    commands.mkdir()
    fake_uv = commands / "uv"
    fake_uv.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    script = Path(__file__).resolve().parents[2] / "scripts/setup-model"
    result = subprocess.run(
        [str(script), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    invocation = json.loads(result.stdout.splitlines()[-1])
    assert invocation[-2:] == ["--workspace", str(tmp_path / "scratch")]

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foliqant_model.curation import storage
from foliqant_model.errors import ModelError


def test_store_is_private_immutable_and_reusable(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "item.json"
    payload = {"value": [1, 2, 3]}
    storage.store_object(path, payload)
    inode = path.stat().st_ino
    content = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert storage.load_object(path) == payload

    storage.store_object(path, payload)
    assert path.stat().st_ino == inode
    assert path.read_bytes() == content

    with pytest.raises(ModelError) as changed:
        storage.store_object(path, {"value": [4]})
    assert changed.value.code == "INTEGRITY_FAILED"
    assert path.read_bytes() == content

    path.write_bytes(content.replace(b"[1,2,3]", b"[1,2,4]"))
    path.chmod(0o600)
    with pytest.raises(ModelError) as tampered:
        storage.load_object(path)
    assert tampered.value.code == "INTEGRITY_FAILED"


def test_store_and_load_reject_symlinks_and_fifos(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target")
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ModelError) as symlink_store:
        storage.store_object(link, {"safe": True})
    assert symlink_store.value.code == "UNSAFE_ARTIFACT_PATH"
    with pytest.raises(ModelError) as symlink_load:
        storage.load_object(link)
    assert symlink_load.value.code == "UNSAFE_ARTIFACT_PATH"

    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo, 0o600)
    started = time.monotonic()
    with pytest.raises(ModelError) as fifo_load:
        storage.load_object(fifo)
    assert fifo_load.value.code == "UNSAFE_ARTIFACT_PATH"
    assert time.monotonic() - started < 1

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(ModelError) as directory_link:
        storage.store_object(linked_directory / "record.json", {"safe": True})
    assert directory_link.value.code == "UNSAFE_ARTIFACT_PATH"
    real_record = real_directory / "record.json"
    storage.store_object(real_record, {"safe": True})
    with pytest.raises(ModelError) as linked_load:
        storage.load_object(linked_directory / "record.json")
    assert linked_load.value.code == "UNSAFE_ARTIFACT_PATH"


def test_interrupted_publish_leaves_no_partial_and_resume_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "item.json"
    real_link = os.link
    interrupted = False

    def interrupt_once(source: str | Path, target: str | Path, **kwargs: object) -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        real_link(source, target, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        storage.store_object(path, {"completed": True})
    assert not path.exists()
    assert not list(tmp_path.glob(".curation-*"))

    storage.store_object(path, {"completed": True})
    assert storage.load_object(path) == {"completed": True}
    assert not list(tmp_path.glob(".curation-*"))


def test_lock_rejects_symlink_and_fifo_without_blocking(tmp_path: Path) -> None:
    symlink_directory = tmp_path / "symlink-lock"
    symlink_directory.mkdir()
    target = symlink_directory / "target"
    target.write_text("", encoding="utf-8")
    (symlink_directory / "run.lock").symlink_to(target)
    with pytest.raises(ModelError) as linked:
        with storage.run_lock(symlink_directory):
            pass
    assert linked.value.code == "UNSAFE_ARTIFACT_PATH"

    fifo_directory = tmp_path / "fifo-lock"
    fifo_directory.mkdir()
    os.mkfifo(fifo_directory / "run.lock", 0o600)
    started = time.monotonic()
    with pytest.raises(ModelError) as fifo:
        with storage.run_lock(fifo_directory):
            pass
    assert fifo.value.code == "UNSAFE_ARTIFACT_PATH"
    assert time.monotonic() - started < 1


def test_advisory_lock_is_released_on_process_exit_and_never_unlinked(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    ready = tmp_path / "ready"
    script = (
        "from pathlib import Path\n"
        "import time\n"
        "from foliqant_model.curation.storage import run_lock\n"
        f"with run_lock(Path({str(directory)!r})):\n"
        f"    Path({str(ready)!r}).write_text('ready')\n"
        "    time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file()
        lock_path = directory / "run.lock"
        inode = lock_path.stat().st_ino
        with pytest.raises(ModelError) as held:
            with storage.run_lock(directory):
                pass
        assert held.value.code == "OUTPUT_EXISTS"

        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        with storage.run_lock(directory):
            assert lock_path.exists()
            assert lock_path.stat().st_ino == inode
        assert lock_path.exists()
        assert lock_path.stat().st_mode & 0o777 == 0o600
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

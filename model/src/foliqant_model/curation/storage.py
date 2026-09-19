"""Private immutable work records and crash-released local run locks."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..artifacts import sha256_file
from ..configuration import parse_json
from ..contracts.base import canonical_digest
from ..errors import ModelError

_MAX_OBJECT_BYTES = 128 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def canonical_bytes(value: object) -> bytes:
    """Encode strict canonical JSON without machine-specific formatting."""

    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def ensure_directory(path: Path) -> None:
    """Create private directories while rejecting any symlink in the path."""

    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation directories cannot be symlinks")
        if not parent.exists():
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as error:
                raise ModelError("IO_FAILED", "Cannot create curation directory") from error
        if parent.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation directories cannot be symlinks")
        if not parent.is_dir():
            raise ModelError("IO_FAILED", "Curation path must be a directory")


def _verify_existing(path: Path, expected: tuple[int, str]) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot inspect existing curation output") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation output must be a regular file")
    if metadata.st_mode & 0o077:
        raise ModelError("INTEGRITY_FAILED", "Existing curation output is not private")
    if sha256_file(path) != expected:
        raise ModelError("INTEGRITY_FAILED", "Existing curation output was changed")


def write_once(path: Path, data: bytes) -> None:
    """Atomically publish exact bytes or verify an identical existing record."""

    ensure_directory(path.parent)
    expected = (len(data), hashlib.sha256(data).hexdigest())
    if path.exists() or path.is_symlink():
        _verify_existing(path, expected)
        return
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".curation-", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _verify_existing(path, expected)
            return
        _verify_existing(path, expected)
    except ModelError:
        raise
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot publish curation output") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def store_object(path: Path, payload: object) -> None:
    """Store a hash-bound object; successful records are never rewritten."""

    try:
        envelope = {
            "schemaVersion": 1,
            "sha256": canonical_digest(payload),
            "payload": payload,
        }
        data = canonical_bytes(envelope)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ModelError("ARGUMENT_INVALID", "Curation object is not strict JSON") from error
    write_once(path, data)


def _validate_existing_directory(path: Path) -> None:
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation directories cannot be symlinks")
        if not parent.exists():
            raise ModelError("INPUT_NOT_FOUND", "Curation record directory does not exist")
        if not parent.is_dir():
            raise ModelError("IO_FAILED", "Curation record parent must be a directory")


def _read_private_file(path: Path) -> bytes:
    _validate_existing_directory(path.parent)
    descriptor = -1
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation record must be a regular file")
        if before.st_mode & 0o077:
            raise ModelError("INTEGRITY_FAILED", "Curation record is not private")
        descriptor = os.open(path, os.O_RDONLY | _NONBLOCK | _NOFOLLOW)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ModelError("INTEGRITY_FAILED", "Curation record changed while opening")
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, _MAX_OBJECT_BYTES - received + 1))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > _MAX_OBJECT_BYTES:
                raise ModelError("INTEGRITY_FAILED", "Curation record exceeds the size limit")
        after = os.fstat(descriptor)
        if (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_dev,
            opened.st_ino,
        ):
            raise ModelError("INTEGRITY_FAILED", "Curation record changed while reading")
        return b"".join(chunks)
    except ModelError:
        raise
    except FileNotFoundError as error:
        raise ModelError("INPUT_NOT_FOUND", "Curation record does not exist") from error
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ModelError(
                "UNSAFE_ARTIFACT_PATH", "Curation record cannot be a symlink"
            ) from error
        raise ModelError("IO_FAILED", "Cannot read curation record") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_object(path: Path) -> object:
    """Read and verify one bounded strict JSON record without following links."""

    data = _read_private_file(path)
    try:
        value = parse_json(data.decode("utf-8", errors="strict"))
    except (UnicodeError, ModelError) as error:
        raise ModelError("INTEGRITY_FAILED", "Curation record is not strict JSON") from error
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "sha256", "payload"}:
        raise ModelError("INTEGRITY_FAILED", "Curation record envelope is invalid")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ModelError("INTEGRITY_FAILED", "Curation record version is invalid")
    try:
        actual = canonical_digest(value["payload"])
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ModelError("INTEGRITY_FAILED", "Curation record payload is invalid") from error
    if value["sha256"] != actual:
        raise ModelError("INTEGRITY_FAILED", "Curation record checksum does not match")
    return value["payload"]


@contextmanager
def run_lock(directory: Path) -> Iterator[None]:
    """Use a persistent advisory lock inode; process exit releases it without unlinking."""

    ensure_directory(directory)
    path = directory / "run.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | _NONBLOCK | _NOFOLLOW,
            0o600,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation lock must be one regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ModelError("OUTPUT_EXISTS", "Another process owns this curation run") from error
        # This inode is deliberately never unlinked: replacement could create two locks.
        yield
    except ModelError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Curation lock cannot be a symlink") from error
        raise ModelError("IO_FAILED", "Cannot acquire curation run lock") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

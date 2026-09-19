"""Bounded HTTPS downloads of explicitly pinned, checksummed setup assets."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import urllib.error
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Literal

from .artifacts import sha256_file
from .contracts.setup import SetupAsset, _https_url
from .errors import ModelError


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            _https_url(newurl)
        except ValueError as error:
            raise ModelError("NETWORK_FAILED", "Unsafe download redirect rejected") from error
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _private_parent(path: Path) -> None:
    for parent in reversed((path.parent, *path.parents[1:])):
        if parent.is_symlink():
            raise ModelError("UNSAFE_ARTIFACT_PATH", "Download parent must not be a symlink")
        if not parent.exists():
            parent.mkdir(mode=0o700)
        if not parent.is_dir():
            raise ModelError("IO_FAILED", "Download parent must be a directory")


def _matches(path: Path, asset: SetupAsset) -> None:
    size, digest = sha256_file(path)
    if size != asset.size or digest != asset.sha256:
        raise ModelError("INTEGRITY_FAILED", "Downloaded asset does not match its pinned identity")


def download_asset(
    asset: SetupAsset,
    destination: Path,
    *,
    offline: bool = False,
    timeout: float = 60.0,
) -> Literal["downloaded", "reused"]:
    """Reuse exact bytes or download atomically; never replace a corrupt cache file."""
    if not 0 < timeout <= 604800:
        raise ModelError("ARGUMENT_INVALID", "Download timeout is out of range")
    temporary: Path | None = None
    try:
        _private_parent(destination)
        if destination.exists() or destination.is_symlink():
            _matches(destination, asset)
            return "reused"
        if offline:
            raise ModelError("INPUT_NOT_FOUND", "An asset is missing from the offline cache")
        started = time.monotonic()
        request = urllib.request.Request(
            asset.url,
            headers={
                "User-Agent": "foliqant-model/0.1",
                "Accept-Encoding": "identity",
            },
        )
        opener = urllib.request.build_opener(_HttpsRedirect())
        descriptor, name = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
        temporary = Path(name)
        # mkstemp is exclusive and private; no credentials or remote code are loaded.
        with os.fdopen(descriptor, "wb") as stream:
            with opener.open(request, timeout=min(60.0, timeout)) as response:
                _https_url(response.geturl())
                digest = hashlib.sha256()
                received = 0
                while True:
                    if time.monotonic() - started > timeout:
                        raise ModelError("TIMEOUT", "Asset download exceeded its deadline")
                    chunk = response.read(min(1024 * 1024, asset.size - received + 1))
                    if time.monotonic() - started > timeout:
                        raise ModelError("TIMEOUT", "Asset download exceeded its deadline")
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise ModelError("INTEGRITY_FAILED", "Asset exceeds its pinned size")
                    stream.write(chunk)
                    digest.update(chunk)
                if received != asset.size or digest.hexdigest() != asset.sha256:
                    raise ModelError("INTEGRITY_FAILED", "Asset checksum or size mismatch")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Atomic no-replace file publication, even if another writer wins.
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            _matches(destination, asset)
            return "reused"
        _matches(destination, asset)
        return "downloaded"
    except (TimeoutError, urllib.error.URLError) as error:
        if isinstance(error, TimeoutError):
            raise ModelError("TIMEOUT", "Asset download timed out") from error
        raise ModelError("NETWORK_FAILED", "Cannot download the pinned asset") from error
    except ValueError as error:
        raise ModelError("NETWORK_FAILED", "Invalid download response") from error
    except OSError as error:
        raise ModelError("IO_FAILED", "Cannot store the pinned asset") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

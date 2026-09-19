import hashlib
import io
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from foliqant_model.acquisition import _HttpsRedirect, download_asset
from foliqant_model.contracts.setup import SetupAsset
from foliqant_model.errors import ModelError


def asset(content: bytes = b"verified-data") -> SetupAsset:
    return SetupAsset(
        path="data/source",
        url="https://example.org/pinned",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


class Response(io.BytesIO):
    def geturl(self) -> str:
        return "https://example.org/pinned"


def supply(monkeypatch: pytest.MonkeyPatch, content: bytes) -> None:
    def open_response(*args: Any, **kwargs: Any) -> Response:
        return Response(content)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_response)


def test_download_then_offline_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supply(monkeypatch, b"verified-data")
    path = tmp_path / "nested" / "asset"
    assert download_asset(asset(), path) == "downloaded"
    assert path.read_bytes() == b"verified-data"
    assert path.stat().st_mode & 0o777 == 0o600
    assert download_asset(asset(), path, offline=True) == "reused"
    assert not list(path.parent.glob(".download-*"))


@pytest.mark.parametrize("content", [b"short", b"long" * 100, b"unverified!!!"])
def test_bad_download_is_never_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
) -> None:
    supply(monkeypatch, content)
    path = tmp_path / "asset"
    with pytest.raises(ModelError) as failure:
        download_asset(asset(), path)
    assert failure.value.code == "INTEGRITY_FAILED"
    assert not path.exists()
    assert not list(tmp_path.glob(".download-*"))


def test_corrupt_cache_is_not_repaired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Corrupt existing assets must not trigger network access")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unexpected)
    path = tmp_path / "asset"
    path.write_bytes(b"corrupt")
    with pytest.raises(ModelError) as failure:
        download_asset(asset(), path)
    assert failure.value.code == "INTEGRITY_FAILED"
    assert path.read_bytes() == b"corrupt"


def test_missing_offline_asset_does_not_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Offline must never connect")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unexpected)
    with pytest.raises(ModelError) as failure:
        download_asset(asset(), tmp_path / "missing", offline=True)
    assert failure.value.code == "INPUT_NOT_FOUND"


def test_symlink_cache_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"verified-data")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ModelError):
        download_asset(asset(), link, offline=True)


def test_redirect_cannot_downgrade_https() -> None:
    with pytest.raises(ModelError) as failure:
        _HttpsRedirect().redirect_request(
            Request("https://example.org"), None, 302, "redirect", {}, "http://example.org"
        )
    assert failure.value.code == "NETWORK_FAILED"

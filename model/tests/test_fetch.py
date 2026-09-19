from pathlib import Path
from types import SimpleNamespace

import pytest
from test_snapshots import snapshot

from foliqant_model import fetch
from foliqant_model.errors import ModelError
from foliqant_model.snapshots import MAX_MODEL_METADATA_BYTES


def test_fetch_filters_remote_code_and_copies_cache_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "cache"
    snapshot(source)
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(source / "model.safetensors")
    downloads: list[str] = []

    class Api:
        def __init__(self, *, endpoint: str) -> None:
            assert endpoint == "https://huggingface.co"

        def model_info(
            self,
            repo: str,
            *,
            revision: str,
            timeout: int,
            files_metadata: bool,
        ) -> SimpleNamespace:
            assert files_metadata is True
            return SimpleNamespace(
                sha=revision,
                siblings=[
                    SimpleNamespace(
                        rfilename=name,
                        size=(source / name).stat().st_size if (source / name).is_file() else None,
                    )
                    for name in [
                        *(p.name for p in source.iterdir()),
                        "model.py",
                        "pytorch_model.bin",
                        "../secret",
                        "subdir/config.json",
                    ]
                ],
            )

    def download(*, filename: str, **kwargs: object) -> str:
        downloads.append(filename)
        return str(cache_link if filename == "model.safetensors" else source / filename)

    monkeypatch.setattr(fetch, "HfApi", Api)
    monkeypatch.setattr(fetch, "hf_hub_download", download)
    output = tmp_path / "artifact"
    manifest = fetch.fetch_model(
        repo="example/model", revision="a" * 40, license_ref="research-only", output=output
    )
    assert manifest.root.kind == "checkpoint"
    assert manifest.root.details.licenseRef == "research-only"
    assert not (output / "model.safetensors").is_symlink()
    assert set(downloads) == {p.name for p in source.iterdir()}
    assert (output / "model.safetensors").read_bytes() == (
        source / "model.safetensors"
    ).read_bytes()


def test_mutable_revision_rejected_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(**kwargs: object) -> None:
        pytest.fail("invalid acquisition reached network API")

    monkeypatch.setattr(fetch, "HfApi", unexpected)
    with pytest.raises(ModelError) as error:
        fetch.fetch_model(
            repo="example/model", revision="main", license_ref="test", output=tmp_path / "output"
        )
    assert error.value.code == "ARGUMENT_INVALID"


@pytest.mark.parametrize(
    "remote_names",
    [
        ["config.json", "tokenizer_config.json", "tokenizer.json"],
        ["config.json", "tokenizer_config.json", "model.safetensors"],
        ["tokenizer_config.json", "tokenizer.json", "model.safetensors"],
        ["config.json", "tokenizer_config.json", "tokenizer.json", "weights.safetensors"],
    ],
)
def test_required_remote_categories_are_checked_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_names: list[str],
) -> None:
    downloads: list[str] = []

    class Api:
        def __init__(self, *, endpoint: str) -> None:
            assert endpoint == "https://huggingface.co"

        def model_info(
            self,
            repo: str,
            *,
            revision: str,
            timeout: int,
            files_metadata: bool,
        ) -> SimpleNamespace:
            assert files_metadata is True
            return SimpleNamespace(
                sha=revision,
                siblings=[SimpleNamespace(rfilename=name, size=1) for name in remote_names],
            )

    def download(*, filename: str, **kwargs: object) -> str:
        downloads.append(filename)
        pytest.fail("an incomplete remote inventory reached file download")

    monkeypatch.setattr(fetch, "HfApi", Api)
    monkeypatch.setattr(fetch, "hf_hub_download", download)
    with pytest.raises(ModelError) as error:
        fetch.fetch_model(
            repo="example/model",
            revision="a" * 40,
            license_ref="test",
            output=tmp_path / "output",
        )
    assert error.value.code == "OUTPUT_INVALID"
    assert downloads == []


@pytest.mark.parametrize(
    ("issue", "expected_code"),
    [
        ("auto-map", "ARCHITECTURE_UNSUPPORTED"),
        ("quantized", "ARCHITECTURE_UNSUPPORTED"),
        ("missing-template", "ARCHITECTURE_UNSUPPORTED"),
        ("bad-index", "OUTPUT_INVALID"),
    ],
)
def test_unsafe_metadata_is_rejected_before_weight_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue: str,
    expected_code: str,
) -> None:
    source = tmp_path / "cache"
    snapshot(source)
    if issue == "auto-map":
        (source / "config.json").write_text(
            '{"model_type":"llama","auto_map":{"AutoModel":"model.Custom"}}'
        )
    elif issue == "quantized":
        (source / "config.json").write_text(
            '{"model_type":"llama","quantization_config":{"bits":4}}'
        )
    elif issue == "missing-template":
        (source / "tokenizer_config.json").write_text("{}")
    else:
        (source / "model.safetensors.index.json").write_text(
            '{"weight_map":{"weight":"model-00001-of-00002.safetensors"}}'
        )
    downloads: list[str] = []

    class Api:
        def __init__(self, *, endpoint: str) -> None:
            assert endpoint == "https://huggingface.co"

        def model_info(
            self,
            repo: str,
            *,
            revision: str,
            timeout: int,
            files_metadata: bool,
        ) -> SimpleNamespace:
            assert files_metadata is True
            return SimpleNamespace(
                sha=revision,
                siblings=[
                    SimpleNamespace(rfilename=path.name, size=path.stat().st_size)
                    for path in source.iterdir()
                ],
            )

    def download(*, filename: str, **kwargs: object) -> str:
        downloads.append(filename)
        return str(source / filename)

    monkeypatch.setattr(fetch, "HfApi", Api)
    monkeypatch.setattr(fetch, "hf_hub_download", download)
    with pytest.raises(ModelError) as error:
        fetch.fetch_model(
            repo="example/model",
            revision="a" * 40,
            license_ref="test",
            output=tmp_path / "output",
        )
    assert error.value.code == expected_code
    assert "model.safetensors" not in downloads


def test_declared_oversized_metadata_is_rejected_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads: list[str] = []
    names = ["config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"]

    class Api:
        def __init__(self, *, endpoint: str) -> None:
            assert endpoint == "https://huggingface.co"

        def model_info(
            self,
            repo: str,
            *,
            revision: str,
            timeout: int,
            files_metadata: bool,
        ) -> SimpleNamespace:
            assert files_metadata is True
            return SimpleNamespace(
                sha=revision,
                siblings=[
                    SimpleNamespace(
                        rfilename=name,
                        size=(MAX_MODEL_METADATA_BYTES + 1 if name == "config.json" else 1),
                    )
                    for name in names
                ],
            )

    def download(*, filename: str, **kwargs: object) -> str:
        downloads.append(filename)
        pytest.fail("oversized metadata reached file download")

    monkeypatch.setattr(fetch, "HfApi", Api)
    monkeypatch.setattr(fetch, "hf_hub_download", download)
    with pytest.raises(ModelError) as error:
        fetch.fetch_model(
            repo="example/model",
            revision="a" * 40,
            license_ref="test",
            output=tmp_path / "output",
        )
    assert error.value.code == "OUTPUT_INVALID"
    assert downloads == []

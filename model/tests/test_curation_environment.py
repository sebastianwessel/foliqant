from __future__ import annotations

import pytest

from foliqant_model.curation.contracts import CurationConfig
from foliqant_model.curation.environment import apply_curation_environment
from foliqant_model.errors import ModelError


def test_allowlisted_environment_overrides_are_typed_and_bounded() -> None:
    config = apply_curation_environment(
        CurationConfig(),
        {
            "FOLIQANT_CURATION_ENDPOINT_URL": "http://127.0.0.1:1234/v1",
            "FOLIQANT_CURATION_MODEL": "local-generator",
            "FOLIQANT_CURATION_STRUCTURED_OUTPUT": "prompt",
            "FOLIQANT_CURATION_REASONING_EFFORT": "xhigh",
            "FOLIQANT_CURATION_ALLOW_PRIVATE_NETWORK": "true",
            "FOLIQANT_CURATION_TIMEOUT_SECONDS": "180",
            "FOLIQANT_CURATION_MAX_TOKENS": "4096",
            "FOLIQANT_CURATION_TEMPERATURE": "0.2",
            "FOLIQANT_CURATION_MAX_CANDIDATES": "12",
            "FOLIQANT_CURATION_LANGUAGES": "en,de",
        },
    )

    assert config.endpoint.model == "local-generator"
    assert config.endpoint.structuredOutput == "prompt"
    assert config.endpoint.reasoningEffort == "xhigh"
    assert config.endpoint.allowPrivateNetwork is True
    assert config.endpoint.timeoutSeconds == 180
    assert config.endpoint.maxTokens == 4096
    assert config.endpoint.temperature == 0.2
    assert config.generation.maxCandidates == 12
    assert config.generation.languages == ["en", "de"]


@pytest.mark.parametrize(
    ("environment", "pointer"),
    [
        ({"FOLIQANT_CURATION_MAX_TOKENS": "2.5"}, "/endpoint/maxTokens"),
        ({"FOLIQANT_CURATION_TEMPERATURE": "nan"}, "/endpoint/temperature"),
        ({"FOLIQANT_CURATION_LANGUAGES": "en,"}, "/generation/languages"),
        ({"FOLIQANT_CURATION_STRUCTURED_OUTPUT": "automatic"}, "/endpoint/structuredOutput"),
        ({"FOLIQANT_CURATION_REASONING_EFFORT": "high"}, "/endpoint/reasoningEffort"),
        ({"FOLIQANT_CURATION_ALLOW_PRIVATE_NETWORK": "yes"}, "/endpoint/allowPrivateNetwork"),
    ],
)
def test_invalid_environment_override_is_rejected_without_coercion(
    environment: dict[str, str], pointer: str
) -> None:
    with pytest.raises(ModelError) as raised:
        apply_curation_environment(CurationConfig(), environment)

    assert raised.value.code == "CONFIG_INVALID"
    assert raised.value.location is not None
    assert raised.value.location.value == pointer


def test_cli_uses_shared_dotenv_parser_with_process_precedence(tmp_path, monkeypatch) -> None:
    import os
    from pathlib import Path

    from foliqant_model.curation.environment import load_curation_environment

    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("FOLIQANT_CURATION_"):
            monkeypatch.delenv(key)
    (tmp_path / ".env").write_text(
        'FOLIQANT_CURATION_MODEL="quoted file model"\n'
        "FOLIQANT_CURATION_REASONING_EFFORT=xhigh\n"
        "FOLIQANT_CURATION_ENDPOINT_URL='http://127.0.0.1:1234/v1'\n"
        "FOLIQANT_CURATION_LANGUAGES='${NOT_EXPANDED}'\n"
        "OPENROUTER_API_KEY=private-file-secret\n"
        "UNRELATED=value\n"
    )
    monkeypatch.setenv("FOLIQANT_CURATION_REASONING_EFFORT", "low")
    monkeypatch.setenv("UNRELATED_PROCESS_SECRET", "private-process-secret")
    environment = load_curation_environment(Path.cwd(), os.environ)
    assert environment == {
        "FOLIQANT_CURATION_MODEL": "quoted file model",
        "FOLIQANT_CURATION_REASONING_EFFORT": "low",
        "FOLIQANT_CURATION_ENDPOINT_URL": "http://127.0.0.1:1234/v1",
        "FOLIQANT_CURATION_LANGUAGES": "${NOT_EXPANDED}",
    }
    assert os.environ.get("FOLIQANT_CURATION_MODEL") is None
    assert "private" not in repr(environment)


def test_cli_environment_failure_is_a_safe_model_error(tmp_path, monkeypatch) -> None:
    import os
    from pathlib import Path

    from foliqant_model.curation.environment import load_curation_environment

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_bytes(b"PRIVATE" * (1024 * 1024))
    with pytest.raises(ModelError) as caught:
        load_curation_environment(Path.cwd(), os.environ)
    assert caught.value.code == "CONFIG_INVALID"
    assert "PRIVATE" not in str(caught.value)


def test_library_curation_accepts_explicit_environment_without_reading_dotenv(
    tmp_path, monkeypatch
) -> None:
    from foliqant_model.curation import runner

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FOLIQANT_CURATION_MODEL=must-not-be-loaded\n")
    monkeypatch.setattr(runner, "load_config", lambda *args: CurationConfig())
    observed = []

    def apply(config, environment):
        observed.append(environment)
        raise ModelError("CONFIG_INVALID", "stop after local configuration")

    monkeypatch.setattr(runner, "apply_curation_environment", apply)
    explicit = {"FOLIQANT_CURATION_MODEL": "explicit-model"}
    with pytest.raises(ModelError, match="stop after local configuration"):
        runner.run_curation(tmp_path / "curation.yaml", environment=explicit)
    assert observed == [explicit]
    assert observed[0] is not explicit

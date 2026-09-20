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

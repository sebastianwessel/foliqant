"""Deployment settings must not become arbitrary provider request overrides."""

import pytest
from pydantic import ValidationError

from foliqant.contracts.models import ModelProfiles


def profile(**overrides: object) -> dict[str, object]:
    return {
        "provider": "openai_compatible",
        "model": "configured-local-model",
        "base_url": "http://127.0.0.1:1234/v1",
        "allow_insecure_http": True,
        "output_mode": "native",
        **overrides,
    }


def validate(value: dict[str, object]) -> ModelProfiles:
    return ModelProfiles.model_validate({"models": {"deciding": value}}, strict=True)


def test_explicit_alias_model_and_options_roundtrip_without_secrets() -> None:
    settings = validate(profile(options={"max_tokens": 800, "reasoning_effort": "low"}))
    selected = settings.models["deciding"]
    assert selected.model == "configured-local-model"
    assert selected.options.max_tokens == 800
    assert selected.output_mode == "native"
    assert "primary" not in settings.models
    serialized = settings.model_dump(mode="json")
    assert ModelProfiles.model_validate(serialized, strict=True) == settings


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "unknown"},
        {"model": " "},
        {"output_mode": "prompt"},
        {"api_key": "not-accepted"},
        {"api_key_env": "${KEY}"},
        {"supports_text": False, "supports_json_schema": False},
        {"concurrency": True},
        {"concurrency": 0},
        {"queue_limit": -1},
        {"request_timeout": float("nan")},
        {"request_timeout": float("inf")},
        {"request_timeout": 0},
        {"options": {"max_tokens": True}},
        {"options": {"max_tokens": 0}},
        {"options": {"temperature": float("nan")}},
        {"options": {"store": True}},
        {"options": {"extra_body": {"tools": []}}},
        {"options": {"model": "other-model"}},
        {"options": {"max_retries": 20}},
        {"options": {"reasoning_effort": "invented"}},
    ],
)
def test_closed_profile_rejects_invalid_or_host_owned_overrides(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        validate(profile(**overrides))


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/model.sock",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1?api_key=secret",
        "https://api.example.com/v1#fragment",
        "https://api.example.com:0/v1",
        "https://api.example.com:99999/v1",
        "https://api.\nexample.com/v1",
        "https:///v1",
        "https://example.test/\x7f/v1",
        "https://example.test\\other/v1",
        "https://example.test/prefix/../v1",
        "https://example.test/prefix/%2e%2e/v1",
    ],
)
def test_configured_endpoint_rejects_credentials_ambiguous_urls_and_bad_ports(
    endpoint: str,
) -> None:
    with pytest.raises(ValidationError):
        validate(profile(base_url=endpoint))


def test_plain_http_is_explicit_and_output_mode_is_required() -> None:
    with pytest.raises(ValidationError):
        validate(profile(allow_insecure_http=False))
    value = profile()
    value.pop("output_mode")
    with pytest.raises(ValidationError):
        validate(value)


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_azure_flavors_have_unambiguous_endpoints_and_versions(api: str) -> None:
    base = {
        "provider": "azure_openai",
        "model": "deployment-name",
        "api": api,
        "output_mode": "native",
    }
    validate({**base, "api_flavor": "v1", "endpoint": "https://resource.example/openai/v1"})
    validate(
        {
            **base,
            "api_flavor": "versioned",
            "endpoint": "https://resource.example",
            "api_version": "configured-version",
        }
    )
    for invalid in (
        {"api_flavor": "v1", "endpoint": "https://resource.example"},
        {"api_flavor": "versioned", "endpoint": "https://resource.example"},
        {
            "api_flavor": "v1",
            "endpoint": "https://resource.example/openai/v1",
            "api_version": "configured-version",
        },
    ):
        with pytest.raises(ValidationError):
            validate({**base, **invalid})


def test_thinking_budget_and_sampling_are_not_silently_rewritten() -> None:
    base = {"provider": "anthropic", "model": "configured-model", "output_mode": "native"}
    validate({**base, "options": {"max_tokens": 4096, "thinking_budget": 1024}})
    validate({**base, "options": {"thinking": "adaptive", "effort": "high"}})
    for options in (
        {"max_tokens": 1024, "thinking_budget": 1024},
        {"thinking_budget": 1024, "temperature": 0.2},
        {"thinking_budget": 1024, "top_p": 0.9},
        {"thinking_budget": 1024, "thinking": "adaptive"},
        {"effort": "high"},
    ):
        with pytest.raises(ValidationError):
            validate({**base, "options": options})


def test_unimplemented_bedrock_provider_is_rejected_at_validation() -> None:
    with pytest.raises(ValidationError):
        validate(
            {
                "provider": "bedrock",
                "region": "eu-central-1",
                "model": "configured-model",
                "output_mode": "tool",
            }
        )

"""MCP deployment profiles are closed, bounded, and contain no credentials."""

import pytest
from pydantic import ValidationError

from foliqant.contracts.mcp import (
    McpHttpTransport,
    McpProfiles,
    McpStdioTransport,
)
from foliqant.contracts.workflow import DeclaredToolCatalog


def catalog(*, effect: str = "read") -> dict[str, object]:
    return {
        "tools": {
            "lookup": {
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object"},
                "effect": effect,
            }
        }
    }


def http_profile(**overrides: object) -> dict[str, object]:
    return {
        "transport": {
            "type": "streamable_http",
            "endpoint": "https://tools.example.com/mcp",
        },
        "catalog": catalog(),
        **overrides,
    }


def validate(value: dict[str, object]) -> McpProfiles:
    return McpProfiles.model_validate({"servers": {"policy_tools": value}}, strict=True)


def test_http_profile_roundtrips_reference_only_auth_and_host_policy() -> None:
    settings = validate(
        http_profile(
            auth="operator_oauth",
            identity_meta_key="example.com/foliqant/identity",
            concurrency=3,
            queue_limit=7,
            request_timeout=12.5,
            output_limit_bytes=32_768,
        )
    )
    selected = settings.servers["policy_tools"]
    assert isinstance(selected.transport, McpHttpTransport)
    assert selected.auth == "operator_oauth"
    assert selected.identity_meta_key == "example.com/foliqant/identity"
    assert isinstance(selected.catalog, DeclaredToolCatalog)
    assert selected.catalog.tools["lookup"].effect == "read"
    assert selected.output_limit_bytes == 32_768
    serialized = settings.model_dump(mode="json")
    assert "token" not in str(serialized).lower()
    assert McpProfiles.model_validate(serialized, strict=True) == settings


def test_http_defaults_are_https_bounded_and_unauthenticated_by_choice() -> None:
    selected = validate(http_profile()).servers["policy_tools"]
    assert selected.auth is None
    assert selected.concurrency == 4
    assert selected.queue_limit == 16
    assert selected.request_timeout == 30.0
    assert selected.output_limit_bytes == 1_048_576

    with pytest.raises(ValidationError):
        validate(
            http_profile(
                transport={
                    "type": "streamable_http",
                    "endpoint": "http://127.0.0.1:9090/mcp",
                }
            )
        )
    validate(
        http_profile(
            transport={
                "type": "streamable_http",
                "endpoint": "http://127.0.0.1:9090/mcp",
                "allow_insecure_http": True,
            }
        )
    )


def test_stdio_profile_records_explicit_safe_environment_overlay() -> None:
    settings = validate(
        {
            "transport": {
                "type": "stdio",
                "command": "/opt/foliqant/bin/policy-tools",
                "args": ["serve", "--stdio"],
                "cwd": "/srv/policy-tools",
                "env": {"LANG": "C.UTF-8", "TOOL_MODE": "production"},
            },
            "catalog": catalog(effect="write"),
        }
    )
    selected = settings.servers["policy_tools"]
    assert isinstance(selected.transport, McpStdioTransport)
    assert {key: value.get_secret_value() for key, value in selected.transport.env.items()} == {
        "LANG": "C.UTF-8",
        "TOOL_MODE": "production",
    }
    assert selected.catalog.tools["lookup"].effect == "write"


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/mcp.sock",
        "https://user:secret@tools.example.com/mcp",
        "https://tools.example.com/mcp?token=secret",
        "https://tools.example.com/mcp#fragment",
        "https://tools.example.com:0/mcp",
        "https://tools.example.com:99999/mcp",
        "https://tools.\nexample.com/mcp",
        "https:///mcp",
        "https://tools.example.com/\x7f/mcp",
        "https://tools.example.com\\other/mcp",
        "https://tools.example.com/prefix/../mcp",
        "https://tools.example.com/prefix/%2e%2e/mcp",
    ],
)
def test_http_endpoint_rejects_credentials_and_ambiguous_urls(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        validate(http_profile(transport={"type": "streamable_http", "endpoint": endpoint}))


@pytest.mark.parametrize(
    "identity_meta_key",
    [
        "identity",
        "localhost/identity",
        "example.com",
        "https://example.com/identity",
        "example.com/ identity",
        "example.com/identity\n",
    ],
)
def test_identity_forwarding_key_must_be_domain_qualified(
    identity_meta_key: str,
) -> None:
    with pytest.raises(ValidationError):
        validate(http_profile(identity_meta_key=identity_meta_key))


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth": {"token": "not-accepted"}},
        {"access_token": "not-accepted"},
        {"client_secret": "not-accepted"},
        {"headers": {"Authorization": "not-accepted"}},
        {"concurrency": True},
        {"concurrency": 0},
        {"queue_limit": -1},
        {"request_timeout": float("nan")},
        {"request_timeout": 0},
        {"output_limit_bytes": True},
        {"output_limit_bytes": 0},
        {"output_limit_bytes": 64 * 1024 * 1024 + 1},
        {"catalog": {"tools": {}}},
        {"catalog": catalog(effect="unknown")},
    ],
)
def test_profile_rejects_secrets_unknown_policy_and_invalid_bounds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        validate(http_profile(**overrides))


@pytest.mark.parametrize(
    "transport",
    [
        {"type": "stdio", "command": "tool", "cwd": "relative/path"},
        {"type": "stdio", "command": "tool\nother"},
        {"type": "stdio", "command": "tool", "args": ["bad\x00arg"]},
        {"type": "stdio", "command": "tool", "env": {"BAD-NAME": "value"}},
        {"type": "stdio", "command": "tool", "env": {"SAFE": "bad\nvalue"}},
        {"type": "stdio", "command": "tool", "inherit_environment": True},
        {"type": "stdio", "command": "tool", "shell": True},
    ],
)
def test_stdio_process_settings_are_closed_and_bounded(
    transport: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        validate({"transport": transport, "catalog": catalog()})


def test_stdio_rejects_http_credential_hook() -> None:
    with pytest.raises(ValidationError):
        validate(
            {
                "transport": {"type": "stdio", "command": "trusted-tool"},
                "auth": "operator_oauth",
                "catalog": catalog(),
            }
        )

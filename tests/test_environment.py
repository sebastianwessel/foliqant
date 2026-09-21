"""Deployment references resolve once locally without touching business values."""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from foliqant.bootstrap import RuntimePlugins, open_application, prepare_application
from foliqant.compiler import CompilationError
from foliqant.contracts.mcp import McpStdioTransport
from foliqant.contracts.models import ModelProfiles
from foliqant.contracts.telemetry import TelemetryConfig
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.environment import EnvironmentResolver


def profiles(**overrides: object) -> ModelProfiles:
    return ModelProfiles.model_validate(
        {
            "models": {
                "local": {
                    "provider": "openai_compatible",
                    "model": "$MODEL_NAME",
                    "base_url": "$MODEL_URL",
                    "api_key": "$MODEL_KEY",
                    "output_mode": "native",
                    **overrides,
                }
            }
        },
        strict=True,
    )


def configuration(tmp_path: Path) -> Path:
    bundle = tmp_path / "workflows/demo"
    (bundle / "steps").mkdir(parents=True)
    (bundle / "workflow.yaml").write_text("version: 1\nname: demo\nstart: done\n")
    (bundle / "steps/done.yaml").write_text("type: finish\noutcome: completed\n")
    path = tmp_path / "foliqant.yaml"
    path.write_text(
        "version: 1\nworkflows: {demo: workflows/demo}\n"
        "models:\n  local:\n    provider: openai_compatible\n    model: $MODEL_NAME\n"
        "    base_url: $MODEL_URL\n    api_key: $MODEL_KEY\n    output_mode: native\n"
    )
    return path


@pytest.mark.parametrize(
    "value", ["${NAME}", "$NAME/suffix", "prefix$NAME", "$(command)", "$9X", "$"]
)
@pytest.mark.parametrize("field", ["model", "base_url", "api_key"])
def test_only_full_references_are_accepted(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        profiles(**{field: value})


def test_resolution_is_a_snapshot_and_never_recursively_expands() -> None:
    environment = {
        "MODEL_NAME": "$OTHER_MODEL",
        "MODEL_URL": "https://models.example/v1",
        "MODEL_KEY": "$OTHER_SECRET",
    }
    resolver = EnvironmentResolver(environment)
    environment["MODEL_NAME"] = "changed"
    authored = profiles()
    resolved = resolver.resolve(authored)
    selected = resolved.models["local"]
    assert selected.model == "$OTHER_MODEL"
    assert selected.api_key is not None
    assert selected.api_key.get_secret_value() == "$OTHER_SECRET"
    assert "$OTHER_SECRET" not in repr(resolved)
    assert "$OTHER_SECRET" not in resolved.model_dump_json()
    assert "$MODEL_KEY" in authored.model_dump_json()
    assert "$OTHER_SECRET" not in repr(resolver)
    assert resolver.resolve(resolved) is resolved


def test_escaped_values_remain_literal_and_secret_literals_are_redacted() -> None:
    authored = profiles(
        model="prefix$$LITERAL", api_key="$$SECRET", base_url="https://models.example"
    )
    resolved = EnvironmentResolver({}).resolve(authored)
    assert resolved.models["local"].model == "prefix$LITERAL"
    assert resolved.models["local"].api_key.get_secret_value() == "$SECRET"
    assert "SECRET" not in authored.model_dump_json()
    assert "SECRET" not in resolved.model_dump_json()


@pytest.mark.parametrize("value", [None, "", " ", "\n"])
async def test_missing_or_blank_reference_fails_before_any_factory(
    tmp_path: Path, value: str | None
) -> None:
    prepared = prepare_application(configuration(tmp_path))
    calls = []

    @asynccontextmanager
    async def factory(profiles, *, environment):
        calls.append(profiles)
        yield {}

    environment = {"MODEL_NAME": "some-model", "MODEL_URL": "https://models.example"}
    if value is not None:
        environment["MODEL_KEY"] = value
    with pytest.raises(ServiceError) as caught:
        async with open_application(
            prepared, environment=environment, plugins=RuntimePlugins(model_factory=factory)
        ):
            pytest.fail("invalid references must prevent activation")
    assert caught.value.code == ErrorCode.INVALID_CONFIGURATION
    assert calls == []


@pytest.mark.parametrize(
    "url", ["http://models.example", "https://user:PRIVATE@models.example", "$ANOTHER_URL"]
)
def test_resolved_endpoint_is_validated_without_exposing_value(url: str) -> None:
    with pytest.raises(ServiceError) as caught:
        EnvironmentResolver(
            {"MODEL_NAME": "model", "MODEL_URL": url, "MODEL_KEY": "PRIVATE"}
        ).resolve(profiles())
    assert "PRIVATE" not in str(caught.value)
    assert url not in str(caught.value)


async def test_prepare_is_offline_and_open_reads_adjacent_env_with_process_precedence(
    tmp_path: Path,
) -> None:
    path = configuration(tmp_path)
    prepared = prepare_application(path)
    initial_digest = prepared.configuration_digest
    (tmp_path / ".env").write_text(
        "MODEL_NAME=file-model\nMODEL_URL=https://models.example/v1\nMODEL_KEY=file-secret\n"
    )
    observed = []

    @asynccontextmanager
    async def factory(profiles, *, environment):
        selected = profiles.models["local"]
        observed.append((selected.model, selected.api_key.get_secret_value()))
        # Rejection after the injected factory proves local resolution without
        # needing a provider, SDK, endpoint, or model request.
        yield {}

    with pytest.raises(ServiceError):
        async with open_application(
            prepared,
            environment={"MODEL_NAME": "process-model", "MODEL_KEY": "process-secret"},
            plugins=RuntimePlugins(model_factory=factory),
        ):
            pytest.fail("deliberately empty bindings")
    assert observed == [("process-model", "process-secret")]
    assert prepared.config.models["local"].model == "$MODEL_NAME"
    assert prepare_application(path).configuration_digest == initial_digest
    assert "process-secret" not in repr(prepared)
    assert "process-secret" not in prepared.config.model_dump_json()


def test_invalid_reference_fails_in_prepare_without_environment(tmp_path: Path) -> None:
    path = configuration(tmp_path)
    path.write_text(path.read_text().replace("$MODEL_NAME", "${MODEL_NAME}"))
    with pytest.raises(CompilationError):
        prepare_application(path)


def test_stdio_overlay_and_telemetry_headers_resolve_and_remain_secret() -> None:
    stdio = McpStdioTransport.model_validate(
        {
            "type": "stdio",
            "command": "$COMMAND",
            "args": ["$ARG", "$$LITERAL"],
            "cwd": "$DIRECTORY",
            "env": {"TOKEN": "$TOKEN"},
        },
        strict=True,
    )
    resolver = EnvironmentResolver(
        {
            "COMMAND": "python",
            "ARG": "script.py",
            "DIRECTORY": "/tmp",
            "TOKEN": "private-token",
            "COLLECTOR": "https://collector.example/v1/traces",
        }
    )
    resolved = resolver.resolve(stdio)
    assert resolved.command == "python" and resolved.cwd == "/tmp"
    assert resolved.args == ["script.py", "$LITERAL"]
    assert resolved.env["TOKEN"].get_secret_value() == "private-token"
    assert "private-token" not in resolved.model_dump_json()
    telemetry = TelemetryConfig.model_validate(
        {
            "service_name": "test",
            "traces_endpoint": "$COLLECTOR",
            "traces_headers": {"authorization": "$TOKEN"},
        },
        strict=True,
    )
    resolved_telemetry = resolver.resolve(telemetry)
    assert resolved_telemetry.traces_endpoint == "https://collector.example/v1/traces"
    assert resolved_telemetry.traces_headers["authorization"].get_secret_value() == "private-token"
    assert "private-token" not in repr(resolved_telemetry)
    assert "private-token" not in resolved_telemetry.model_dump_json()


@pytest.mark.parametrize(
    "field, value", [("COMMAND", "\n"), ("DIRECTORY", "relative"), ("TOKEN", "unsafe\nheader")]
)
def test_resolved_stdio_constraints_are_applied(field: str, value: str) -> None:
    stdio = McpStdioTransport.model_validate(
        {"type": "stdio", "command": "$COMMAND", "cwd": "$DIRECTORY", "env": {"TOKEN": "$TOKEN"}}
    )
    environment = {"COMMAND": "python", "DIRECTORY": "/tmp", "TOKEN": "value", field: value}
    with pytest.raises(ServiceError):
        EnvironmentResolver(environment).resolve(stdio)


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_literal_api_keys_must_be_nonblank(value: str) -> None:
    with pytest.raises(ValidationError):
        profiles(api_key=value)


def test_schema_and_catalog_strings_are_never_environment_references() -> None:
    from foliqant.contracts.mcp import McpProfiles

    config = McpProfiles.model_validate(
        {
            "servers": {
                "records": {
                    "transport": {"type": "stdio", "command": "python"},
                    "catalog": {
                        "tools": {
                            "lookup": {
                                "input_schema": {
                                    "type": "object",
                                    "description": "Read $CUSTOMER fields for ${PROMPT}",
                                    "properties": {"value": {"const": "$LITERAL"}},
                                },
                                "output_schema": {"type": "object"},
                                "effect": "read",
                            }
                        }
                    },
                }
            }
        },
        strict=True,
    )
    resolved = EnvironmentResolver({}).resolve(config)
    declaration = resolved.servers["records"].catalog.tools["lookup"]
    assert declaration.input_schema["description"] == "Read $CUSTOMER fields for ${PROMPT}"
    assert declaration.input_schema["properties"]["value"]["const"] == "$LITERAL"


def test_resolved_literal_dollars_survive_mcp_composition() -> None:
    from foliqant.adapters.mcp.runtime import McpRuntime
    from foliqant.adapters.mcp.transport import McpClientSessionFactory
    from foliqant.contracts.mcp import McpProfiles

    config = McpProfiles.model_validate(
        {
            "servers": {
                "records": {
                    "transport": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["$ARGUMENT"],
                        "env": {"TOKEN": "$TOKEN"},
                    },
                    "catalog": {
                        "tools": {
                            "lookup": {
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "effect": "read",
                            }
                        }
                    },
                }
            }
        },
        strict=True,
    )
    resolved = EnvironmentResolver(
        {"ARGUMENT": "literal${ARGUMENT}", "TOKEN": "literal${SECRET}"}
    ).resolve(config)
    # Bootstrap constructs a wrapper from already resolved server profiles.
    wrapped = McpProfiles(servers=resolved.servers)
    factory = McpClientSessionFactory(wrapped, credential_providers={})

    class Authorizer:
        async def authorize(self, *args):
            raise AssertionError("composition must not authorize or call tools")

    runtime = McpRuntime(wrapped, factory, Authorizer())
    assert runtime is not None

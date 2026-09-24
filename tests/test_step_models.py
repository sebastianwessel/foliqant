"""Offline step-model selection, provider settings, privacy, and shared admission."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from foliqant.adapters.models import ModelBinding
from foliqant.adapters.models.providers import _openai_settings, open_model_bindings
from foliqant.bootstrap import RuntimePlugins, open_application, prepare_application
from foliqant.compiler import CompilationError, compile_workflow
from foliqant.contracts.envelope import Envelope
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError


def profile(**overrides):
    return {
        "provider": "openai_compatible",
        "model": "base-model",
        "base_url": "https://unit.example/v1",
        "output_mode": "native",
        "options": {"max_tokens": 900, "temperature": 0.4, "seed": 13},
        **overrides,
    }


def step(selection, *, kind="llm"):
    common = {
        "type": kind,
        "model": selection,
        "instructions": "Keep $PROMPT literal.",
    }
    if kind == "llm":
        return {**common, "input": {"text": {"literal": "$INPUT"}}, "output": "text"}
    return {
        **common,
        "sources": {"text": {"literal": "Synthetic input"}},
        "question": {"type": "predicate", "criteria": ["Is the input synthetic?"]},
    }


def write_config(tmp_path, steps, *, models=None, layout="inline"):
    bundle = tmp_path / "demo"
    bundle.mkdir()
    definitions = []
    for name, authored in steps.items():
        definition = authored
        if layout != "inline":
            directory = bundle / "steps"
            directory.mkdir(exist_ok=True)
            document = dict(authored)
            if layout == "markdown" and document["type"] in {"llm", "decision"}:
                body = document.pop("instructions")
                definition = f"steps/{name}.md"
                (bundle / definition).write_text(f"---\n{json.dumps(document)}\n---\n{body}\n")
            else:
                definition = f"steps/{name}.yaml"
                (bundle / definition).write_text(json.dumps(document))
        definitions.append({"id": name, "definition": definition})
    workflow = {
        "name": "demo",
        "start": "main",
        "flows": {
            "main": {
                "input": {},
                "definition": {"steps": definitions},
                "transition": {"outcome": "completed"},
            }
        },
    }
    (bundle / "workflow.yaml").write_text(json.dumps(workflow))
    path = tmp_path / "foliqant.yaml"
    path.write_text(json.dumps({"workflows": {"demo": "demo"}, "models": models or {}}))
    return path


@pytest.mark.parametrize("layout", ["inline", "files", "markdown"])
@pytest.mark.parametrize("kind", ["llm", "decision"])
def test_override_compiles_through_all_step_layouts_without_environment(tmp_path, layout, kind):
    path = write_config(
        tmp_path,
        {
            "first": step(
                {"profile": "local", "model": "$STEP_MODEL", "options": {"max_tokens": 40}},
                kind=kind,
            )
        },
        models={"local": profile(api_key="$PRIVATE_KEY")},
        layout=layout,
    )
    prepared = prepare_application(path)
    selected = prepared.plans["demo"].flow("main").step("first").model
    effective = prepared._models[selected]
    assert effective.model == "$STEP_MODEL"
    assert effective.options.max_tokens == 40
    assert effective.options.temperature == 0.4
    assert effective.options.seed == 13
    assert prepared.config.models["local"].options.max_tokens == 900
    assert prepared._model_admission_groups[selected] == "local"
    public_plan = repr(asdict(prepared.plans["demo"]))
    assert "PRIVATE_KEY" not in public_plan and "api_key" not in public_plan


@pytest.mark.parametrize(
    "selection",
    [
        {"profile": "missing"},
        {"profile": "local", "provider": "anthropic"},
        {"profile": "local", "model": None},
        {"profile": "local", "options": {"max_tokens": None}},
        {"profile": "local", "options": {"max_tokens": True}},
        {"profile": "local", "options": {"max_tokens": 0}},
        {"profile": "local", "options": {"temperature": 3}},
        {"profile": "local", "options": {"thinking_budget": 1024}},
        {"profile": "local", "options": {"effort": "high"}},
        {"profile": "local", "options": {"extra_body": {"api_key": "private-sentinel"}}},
        {"profile": "local", "options": {"max_retries": 3}},
        {"profile": "local", "options": {"reasoning_effort": "invented"}},
        profile(api_key="private-sentinel"),
    ],
)
def test_invalid_selection_is_rejected_offline_with_safe_diagnostics(tmp_path, selection):
    path = write_config(tmp_path, {"first": step(selection)}, models={"local": profile()})
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert "private-sentinel" not in repr(caught.value)
    assert caught.value.field is not None


def test_anthropic_partial_options_merge_before_cross_field_validation(tmp_path):
    base = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "output_mode": "native",
        "options": {"thinking": "adaptive", "effort": "low"},
    }
    path = write_config(
        tmp_path,
        {"first": step({"profile": "local", "options": {"effort": "high"}})},
        models={"local": base},
    )
    prepared = prepare_application(path)
    assert (
        prepared._models[prepared.plans["demo"].flow("main").step("first").model].options.effort
        == "high"
    )
    document = json.loads((tmp_path / "demo/workflow.yaml").read_text())
    document["flows"]["main"]["definition"]["steps"][0]["definition"]["model"]["options"] = {
        "thinking": None
    }
    (tmp_path / "demo/workflow.yaml").write_text(json.dumps(document))
    with pytest.raises(CompilationError) as caught:
        prepare_application(path)
    assert caught.value.reason == "invalid_model_options"


def test_inline_capabilities_are_checked_even_without_external_profile_registry(tmp_path):
    write_config(tmp_path, {"first": step(profile(supports_text=False))})
    with pytest.raises(CompilationError) as caught:
        compile_workflow(tmp_path / "demo", model_aliases={}, tool_catalogs={}, handler_names=set())
    assert caught.value.reason == "unsupported_model_capability"


async def test_overrides_execute_with_resolved_model_and_only_explicit_option_changes(tmp_path):
    path = write_config(
        tmp_path,
        {
            "first": step(
                {
                    "profile": "local",
                    "model": "$STEP_MODEL",
                    "options": {"max_tokens": 80, "temperature": None},
                }
            )
        },
        models={"local": profile(api_key="$PRIVATE_KEY")},
    )
    opened = []
    requests = []

    async def model(messages, info):
        requests.append((messages, info.model_settings))
        return ModelResponse(parts=[TextPart("offline result")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        opened.append(profiles)
        yield {
            alias: ModelBinding(
                FunctionModel(model),
                _openai_settings(config),
                CapacityLimiter(concurrency=1, queue_limit=1),
                config.output_mode,
            )
            for alias, config in profiles.models.items()
        }

    prepared = prepare_application(path)
    assert not opened
    # Preparation snapshots configuration and workflows; startup cannot reread step settings.
    (tmp_path / "demo/workflow.yaml").unlink()
    environment = {
        "STEP_MODEL": "selected-model",
        "PRIVATE_KEY": "credential-sentinel",
        "PROMPT": "must not expand",
        "INPUT": "must not expand",
    }
    async with open_application(
        prepared, environment=environment, plugins=RuntimePlugins(model_factory=factory)
    ) as app:
        result = await app.run("demo", Envelope(payload={}))
        isolated = await app.run_step("demo", "main", "first", Envelope(payload={"text": "$INPUT"}))
    assert result.execution.status == isolated.execution.status == "completed"
    assert result.flows["main"].steps["first"].result == isolated.payload == "offline result"
    selected = opened[0].models[prepared.plans["demo"].flow("main").step("first").model]
    assert selected.model == "selected-model"
    assert selected.api_key.get_secret_value() == "credential-sentinel"
    assert "credential-sentinel" not in opened[0].model_dump_json()
    assert requests[0][1]["max_tokens"] == 80
    assert requests[0][1]["seed"] == 13
    assert "temperature" not in requests[0][1]
    assert "$PROMPT" in repr(requests[0][0]) and "$INPUT" in repr(requests[0][0])
    assert "credential-sentinel" not in result.model_dump_json()


async def test_profile_and_derived_steps_share_concurrency_and_queue_limits(tmp_path):
    path = write_config(
        tmp_path,
        {
            "first": step("local"),
            "second": step({"profile": "local", "options": {"max_tokens": 50}}),
        },
        models={"local": profile(concurrency=1, queue_limit=0)},
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def model(messages, info):
        calls.append(info.model_settings["max_tokens"])
        entered.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("offline result")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        yield {
            alias: ModelBinding(
                FunctionModel(model),
                _openai_settings(config),
                CapacityLimiter(concurrency=config.concurrency, queue_limit=config.queue_limit),
                config.output_mode,
            )
            for alias, config in profiles.models.items()
        }

    async with open_application(
        prepare_application(path), environment={}, plugins=RuntimePlugins(model_factory=factory)
    ) as app:
        active = asyncio.create_task(
            app.run_step("demo", "main", "first", Envelope(payload={"text": "one"}))
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        try:
            rejected = await app.run_step(
                "demo", "main", "second", Envelope(payload={"text": "two"})
            )
            assert rejected.execution.status == "failed"
            assert rejected.execution.error.code == ErrorCode.CAPACITY_EXCEEDED
            assert rejected.execution.usage.model_requests == 0
            assert calls == [900]
        finally:
            release.set()
            await active
        accepted = await app.run_step("demo", "main", "second", Envelope(payload={"text": "two"}))
        assert accepted.execution.status == "completed"
        assert calls == [900, 50]


@pytest.mark.parametrize(
    "inline",
    [
        {
            "provider": "openai",
            "api": "responses",
            "model": "gpt-4o-mini",
            "output_mode": "native",
            "options": {"max_tokens": 70},
        },
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "output_mode": "native",
            "options": {"max_tokens": 70},
        },
        {
            "provider": "azure_openai",
            "api": "chat",
            "api_flavor": "v1",
            "endpoint": "$AZURE_ENDPOINT",
            "model": "deployment",
            "output_mode": "native",
            "options": {"max_tokens": 70},
        },
        profile(model="$LOCAL_MODEL", base_url="$LOCAL_ENDPOINT", options={"max_tokens": 70}),
    ],
)
async def test_inline_provider_uses_existing_factory_lifecycle_without_requests(tmp_path, inline):
    path = write_config(tmp_path, {"first": step(inline)})
    bindings_seen = []

    @asynccontextmanager
    async def factory(profiles, *, environment):
        async with open_model_bindings(profiles, environment=environment) as bindings:
            bindings_seen.extend(bindings.values())
            assert len(bindings) == 1
            assert next(iter(bindings.values())).settings["max_tokens"] == 70
            yield bindings

    prepared = prepare_application(path)
    environment = {
        "OPENAI_API_KEY": "unit-secret",
        "ANTHROPIC_API_KEY": "unit-secret",
        "AZURE_OPENAI_API_KEY": "unit-secret",
        "AZURE_ENDPOINT": "https://unit.example/openai/v1",
        "LOCAL_ENDPOINT": "https://unit.example/v1",
        "LOCAL_MODEL": "configured-local",
    }
    async with open_application(
        prepared, environment=environment, plugins=RuntimePlugins(model_factory=factory)
    ) as app:
        assert app.ready
    assert all(binding.model.client.is_closed() for binding in bindings_seen)


async def test_missing_inline_environment_fails_before_factory_is_opened(tmp_path):
    path = write_config(tmp_path, {"first": step(profile(api_key="$MISSING_KEY"))})

    @asynccontextmanager
    async def factory(profiles, *, environment):
        raise AssertionError("missing environment must fail before opening clients")
        yield {}

    prepared = prepare_application(path)
    with pytest.raises(ServiceError) as caught:
        async with open_application(
            prepared, environment={}, plugins=RuntimePlugins(model_factory=factory)
        ):
            pass
    assert caught.value.code == ErrorCode.INVALID_CONFIGURATION


def test_effective_model_and_options_change_revisions_but_secrets_do_not(tmp_path):
    path = write_config(
        tmp_path,
        {"first": step({"profile": "local", "options": {"max_tokens": 20}})},
        models={"local": profile(api_key="first-private-secret")},
    )
    first = prepare_application(path)
    config = json.loads(path.read_text())
    config["models"]["local"]["api_key"] = "second-private-secret"
    path.write_text(json.dumps(config))
    secret_changed = prepare_application(path)
    assert first.configuration_digest == secret_changed.configuration_digest
    assert first.plans["demo"].revision == secret_changed.plans["demo"].revision
    workflow_path = tmp_path / "demo/workflow.yaml"
    workflow = json.loads(workflow_path.read_text())
    workflow["flows"]["main"]["definition"]["steps"][0]["definition"]["model"]["options"][
        "max_tokens"
    ] = 30
    workflow_path.write_text(json.dumps(workflow))
    options_changed = prepare_application(path)
    assert options_changed.plans["demo"].revision != first.plans["demo"].revision
    workflow["flows"]["main"]["definition"]["steps"][0]["definition"]["model"]["model"] = (
        "other-model"
    )
    workflow_path.write_text(json.dumps(workflow))
    assert (
        prepare_application(path).plans["demo"].revision != options_changed.plans["demo"].revision
    )


def test_direct_compiler_revision_includes_inherited_options_without_credentials(tmp_path):
    write_config(tmp_path, {"first": step({"profile": "local", "options": {"max_tokens": 20}})})

    def revision(temperature, credential):
        profiles = ModelProfiles.model_validate(
            {"models": {"local": profile(api_key=credential, options={"temperature": temperature})}}
        )
        return compile_workflow(
            tmp_path / "demo",
            model_aliases={"local": "base-model"},
            model_profiles=profiles.models,
            tool_catalogs={},
            handler_names=set(),
        ).revision

    assert revision(0.1, "first-private-secret") == revision(0.1, "second-private-secret")
    assert revision(0.1, "first-private-secret") != revision(0.8, "first-private-secret")


@pytest.mark.parametrize("inline", [False, True])
def test_explain_names_provider_model_and_source_profile_without_credentials(tmp_path, inline):
    from foliqant.cli import _explain

    selection = (
        profile(model="$STEP_MODEL", api_key="$PRIVATE_KEY")
        if inline
        else {"profile": "local", "model": "$STEP_MODEL", "options": {"max_tokens": 20}}
    )
    path = write_config(
        tmp_path,
        {"first": step(selection)},
        models={"local": profile(api_key="private-credential-sentinel")},
    )
    report = _explain(path, "demo", "json")
    model_step = next(
        item for item in report["workflows"][0]["flows"][0]["steps"] if item["id"] == "first"
    )
    expected = {"provider": "openai_compatible", "model": "$STEP_MODEL"}
    if not inline:
        expected["profile"] = "local"
    assert model_step["model_selection"] == expected
    assert model_step["model"].startswith("step_model_")
    public = json.dumps(report)
    assert "private-credential-sentinel" not in public and "PRIVATE_KEY" not in public
    assert "$PROMPT" not in public and "$INPUT" not in public

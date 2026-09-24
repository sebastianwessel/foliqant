"""Offline settings and real composed runner integration with synthetic adapters."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
import yaml
from handler_contracts import declare
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from foliqant.adapters.handlers import HandlerRegistration
from foliqant.adapters.models import ModelBinding
from foliqant.bootstrap import (
    RuntimePlugins,
    load_environment,
    open_application,
    prepare_application,
)
from foliqant.compiler import CompilationError
from foliqant.contracts.envelope import Envelope
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, freeze_json


async def _noop(inputs, context):
    return StepOutcome(None)


def _prepare(path, *, handlers=None):
    registered = {"noop": HandlerRegistration(_noop, schema({}), schema({}))}
    registered.update(handlers or {})
    return prepare_application(declare(path, registered), handlers=registered)


def settings(
    tmp_path: Path, step: str = "type: handler\nhandler: noop\ninput: {}\n", extra: str = ""
) -> Path:
    root = tmp_path / "workflows/demo"
    (root / "steps").mkdir(parents=True)
    authored = yaml.safe_load(step)
    inputs = {name: {"pointer": f"/payload/{name}"} for name in authored.get("input", {})}
    (root / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "start": "main",
                "flows": {
                    "main": {
                        "input": inputs,
                        "transition": {"outcome": "completed"},
                        "definition": {
                            "steps": [{"id": "first", "definition": "steps/first.yaml"}]
                        },
                    }
                },
            }
        )
    )
    (root / "steps/first.yaml").write_text(step)
    config = tmp_path / "foliqant.yaml"
    config.write_text("workflows: {demo: workflows/demo}\n" + extra)
    return config


def schema(value: object) -> FrozenObject:
    return cast(FrozenObject, freeze_json(value))


async def test_preparation_and_execution_share_effective_revision_without_mutable_config(tmp_path):
    path = settings(tmp_path)
    prepared = _prepare(path)
    prepared.config.workflows.clear()
    assert tuple(prepared.plans) == ("demo",)
    assert prepared.config.workflows == {"demo": "workflows/demo"}
    with pytest.raises(TypeError):
        prepared.plans["other"] = prepared.plans["demo"]
    async with open_application(prepared, environment={}) as app:
        assert app.ready and app.workflow_names == ("demo",)
        result = await app.run("demo", Envelope(payload={}), identity=Identity(principal_id="user"))
        assert result.execution.status == "completed"
        assert result.execution.revision == prepared.plans["demo"].revision
        assert result.metadata.principal_id == "user"
        assert result.metadata.tenant_id is None
    assert not app.ready
    with pytest.raises(ServiceError) as rejected:
        await app.run("demo", Envelope(payload={}), identity=Identity())
    assert rejected.value.code == ErrorCode.DEPENDENCY_FAILURE


@pytest.mark.parametrize(
    "metadata",
    ({"tenant_id": "tenant-context"}, {"principal_id": "principal-context"}),
)
async def test_optional_identity_uses_validated_envelope_context(tmp_path, metadata):
    prepared = _prepare(settings(tmp_path))
    async with open_application(prepared, environment={}) as app:
        result = await app.run("demo", Envelope(payload={}, metadata=metadata))
    assert result.metadata.model_dump(exclude_none=True) == metadata


def test_configuration_changes_update_effective_revision(tmp_path):
    path = settings(tmp_path)
    before = _prepare(path)
    path.write_text(path.read_text() + "execution: {concurrency: 2}\n")
    after = _prepare(path)
    assert before.configuration_digest != after.configuration_digest
    assert before.plans["demo"].revision != after.plans["demo"].revision


def test_environment_is_snapshot_process_wins_and_no_interpolation(tmp_path):
    path = settings(tmp_path)
    (tmp_path / ".env").write_text("SECRET=file-value\nOTHER=${SECRET}\n")
    process = {"SECRET": "process-value"}
    resolved = load_environment(path, process)
    assert resolved == {"SECRET": "process-value", "OTHER": "${SECRET}"}
    assert process == {"SECRET": "process-value"}


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "unknown: secret\n",
        "workflows: {demo: ../elsewhere}\n",
    ],
)
def test_closed_deployment_and_confined_paths(tmp_path, invalid):
    path = tmp_path / "foliqant.yaml"
    path.write_text(invalid)
    with pytest.raises(CompilationError) as caught:
        _prepare(path)
    assert "secret" not in str(caught.value)


async def test_composed_model_factory_is_not_called_during_preparation(tmp_path):
    path = settings(
        tmp_path,
        "type: llm\nmodel: local\ninstructions: Summarize briefly.\n"
        "input: {text: {pointer: /payload/text}}\noutput: text\n",
        "models:\n  local:\n    provider: openai_compatible\n    model: test-model\n"
        "    base_url: http://127.0.0.1:1234/v1\n    allow_insecure_http: true\n"
        "    output_mode: native\n",
    )
    events = []

    async def model(messages, info):
        events.append("request")
        return ModelResponse(parts=[TextPart("synthetic answer")])

    @asynccontextmanager
    async def factory(profiles, *, environment):
        events.append("open")
        try:
            yield {
                "local": ModelBinding(
                    FunctionModel(model),
                    {},
                    CapacityLimiter(concurrency=1, queue_limit=0),
                    "native",
                )
            }
        finally:
            events.append("close")

    prepared = _prepare(path)
    assert not events
    async with open_application(
        prepared, environment={}, plugins=RuntimePlugins(model_factory=factory)
    ) as app:
        assert events == ["open"]
        result = await app.run("demo", Envelope(payload={"text": "private"}), identity=Identity())
        assert result.flows["main"].steps["first"].result == "synthetic answer"
        assert result.execution.usage.model_requests == 1
    assert events == ["open", "request", "close"]


async def test_handlers_keep_identity_and_validate_results_without_charging_models(tmp_path):
    path = settings(
        tmp_path, "type: handler\nhandler: echo\ninput: {value: {pointer: /payload/value}}\n"
    )
    observed = []

    async def handle(inputs, context):
        observed.append(context.caller.identity)
        await asyncio.sleep(0)
        return StepOutcome(inputs["value"])

    prepared = _prepare(
        path,
        handlers={
            "echo": HandlerRegistration(
                handle,
                schema({"type": "object", "required": ["value"]}),
                schema({"type": "string"}),
            )
        },
    )
    async with open_application(prepared, environment={}) as app:
        results = await asyncio.gather(
            *(
                app.run(
                    "demo", Envelope(payload={"value": user}), identity=Identity(principal_id=user)
                )
                for user in ("alice", "bob")
            )
        )
        assert [r.flows["main"].steps["first"].result for r in results] == ["alice", "bob"]
        assert all(r.execution.usage.model_requests == 0 for r in results)
        rejected = await app.run("demo", Envelope(payload={"value": 42}), identity=Identity())
        assert rejected.execution.error.code == ErrorCode.INVALID_OUTPUT
    assert observed[:2] == [Identity(principal_id="alice"), Identity(principal_id="bob")]


async def test_shared_admission_and_bounded_cancellation(tmp_path):
    path = settings(
        tmp_path,
        "type: handler\nhandler: wait\ninput: {}\n",
        "execution: {concurrency: 1, queue_limit: 0}\n",
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(inputs, context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return StepOutcome(None)

    prepared = _prepare(
        path, handlers={"wait": HandlerRegistration(handle, schema({}), schema({}))}
    )
    async with open_application(prepared, environment={}) as app:
        task = asyncio.create_task(app.run("demo", Envelope(payload={}), identity=Identity()))
        await started.wait()
        with pytest.raises(ServiceError) as rejected:
            await app.run("demo", Envelope(payload={}), identity=Identity())
        assert rejected.value.code == ErrorCode.CAPACITY_EXCEEDED
        assert await app.aclose(timeout=0)
        assert cancelled.is_set()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_context_does_not_reclassify_caller_exceptions_as_configuration(tmp_path):
    prepared = _prepare(settings(tmp_path))
    with pytest.raises(RuntimeError, match="caller"):
        async with open_application(prepared, environment={}):
            raise RuntimeError("caller")


def test_environment_file_limit_is_enforced(tmp_path):
    path = settings(tmp_path)
    (tmp_path / ".env").write_bytes(b"X" * (1024 * 1024 + 1))
    with pytest.raises(ServiceError) as caught:
        load_environment(path, {})
    assert caught.value.code == ErrorCode.INVALID_CONFIGURATION


def test_synchronous_handler_is_rejected_before_it_can_block_execution():
    def blocking(inputs, context):
        raise AssertionError("must not run")

    with pytest.raises(ServiceError) as caught:
        HandlerRegistration(blocking, schema({}), schema({}))
    assert caught.value.code == ErrorCode.INVALID_CONFIGURATION


@pytest.mark.parametrize("caller_failure", [False, True])
async def test_client_cleanup_failure_does_not_replace_outcome_or_caller_error(
    tmp_path, caplog, caller_failure
):
    path = settings(
        tmp_path,
        extra="models:\n  local:\n    provider: openai_compatible\n"
        "    model: synthetic\n    base_url: https://model.example/v1\n"
        "    output_mode: native\n",
    )

    async def model(messages, info):
        raise AssertionError("no inference")

    @asynccontextmanager
    async def factory(profiles, *, environment):
        try:
            yield {
                "local": ModelBinding(
                    model=FunctionModel(function=model),
                    settings={},
                    admission=CapacityLimiter(concurrency=1, queue_limit=0),
                    output_mode="native",
                    supports_text=True,
                    supports_json_schema=True,
                    supports_tools=False,
                )
            }
        finally:
            raise RuntimeError("private cleanup error")

    async def run():
        async with open_application(
            _prepare(path), environment={}, plugins=RuntimePlugins(model_factory=factory)
        ) as app:
            result = await app.run("demo", Envelope(payload={}), identity=Identity())
            assert result.execution.status == "completed"
            if caller_failure:
                raise ValueError("caller error")

    if caller_failure:
        with pytest.raises(ValueError, match="caller error"):
            await run()
    else:
        await run()
    assert "private cleanup error" not in caplog.text


def test_write_handler_cannot_be_activated_in_read_only_pipeline(tmp_path):
    async def write(inputs, context):
        raise AssertionError("writes cannot execute")

    path = settings(tmp_path, "type: handler\nhandler: write\ninput: {}\n")
    with pytest.raises(CompilationError):
        _prepare(
            path,
            handlers={"write": HandlerRegistration(write, schema({}), schema({}), effect="write")},
        )


@pytest.mark.parametrize("version", [True, False, 1.0, "1", None, 0, 2])
def test_deployment_version_requires_exact_integer_one(version):
    from pydantic import ValidationError

    from foliqant.contracts.deployment import DeploymentConfig

    with pytest.raises(ValidationError):
        DeploymentConfig.model_validate(
            {"version": version, "workflows": {"demo": "demo"}}, strict=True
        )

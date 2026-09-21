"""Explicit composition of compiled workflows and owned async adapter lifespans."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from foliqant.adapters.handlers import HandlerExecutor
from foliqant.adapters.models import ModelBinding, ModelExecutor
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.adapters.telemetry.logging import LogEvent, emit_event
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.contracts.models import ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject
from foliqant.core.plan import HandlerStepPlan, McpStepPlan
from foliqant.core.runner import WorkflowRunner
from foliqant.environment import EnvironmentResolver
from foliqant.ports.execution import OperationStep, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver, TraceContext
from foliqant.settings import PreparedApplication, load_environment, prepare_application

if TYPE_CHECKING:
    from foliqant.adapters.mcp.auth import McpCredentialProvider
    from foliqant.adapters.mcp.runtime import ToolAuthorizer
    from foliqant.adapters.telemetry.models import ModelTelemetry

_LOGGER = logging.getLogger("foliqant.runtime")


class ModelFactory(Protocol):
    def __call__(
        self, profiles: ModelProfiles, *, environment: Mapping[str, str]
    ) -> AbstractAsyncContextManager[Mapping[str, ModelBinding]]: ...


@dataclass(frozen=True, slots=True)
class RuntimePlugins:
    """Trusted Python extension points; configuration never imports executable code."""

    model_factory: ModelFactory = open_model_bindings
    mcp_credentials: Mapping[str, McpCredentialProvider] = field(default_factory=dict)
    tool_authorizer: ToolAuthorizer | None = None


class _RoutedExecutor:
    def __init__(
        self, model: StepExecutor | None, mcp: StepExecutor | None, handler: StepExecutor
    ) -> None:
        self._model, self._mcp, self._handler = model, mcp, handler

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        selected = (
            self._handler
            if isinstance(step, HandlerStepPlan)
            else self._mcp
            if isinstance(step, McpStepPlan)
            else self._model
        )
        if selected is None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        return await selected.execute(step, inputs, context)


class _DeclaredReadAuthorizer:
    def __init__(self, prepared: PreparedApplication) -> None:
        self._plans = prepared.plans
        self._catalogs = {name: profile.catalog for name, profile in prepared.config.mcp.items()}

    async def authorize(
        self, server: str, tool: str, arguments: FrozenObject, context: StepContext
    ) -> None:
        # The host-declared workflow and step bound the available tools. A host can
        # replace this policy for resource-specific business permissions.
        from foliqant.core.plan import LlmStepPlan

        plan = self._plans.get(context.workflow)
        catalog = self._catalogs.get(server)
        if plan is None or catalog is None or tool not in catalog.tools:
            raise ServiceError(ErrorCode.FORBIDDEN)
        declaration = catalog.tools[tool]
        if declaration.effect != "read":
            raise ServiceError(ErrorCode.FORBIDDEN)
        try:
            step = plan.step(context.step_id)
        except KeyError:
            raise ServiceError(ErrorCode.FORBIDDEN) from None
        if isinstance(step, McpStepPlan) and (step.server, step.tool) == (server, tool):
            return
        if isinstance(step, LlmStepPlan) and step.tools is not None:
            if step.tools.server == server and tool in step.tools.allow:
                return
        raise ServiceError(ErrorCode.FORBIDDEN)


class WorkflowApplication:
    """Shared admission and immutable plans with independent per-call execution state."""

    def __init__(self, runners: Mapping[str, WorkflowRunner]) -> None:
        self._runners = dict(runners)
        self._ready = True
        self._active: dict[asyncio.Task[object], int] = {}

    @property
    def workflow_names(self) -> tuple[str, ...]:
        return tuple(self._runners)

    @property
    def ready(self) -> bool:
        return self._ready

    async def run(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity | None = None,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        """Execute with optional caller-provided identity context."""
        return await self._invoke(
            workflow, envelope, identity=identity, transport_trace=transport_trace
        )

    async def run_step(
        self,
        workflow: str,
        step_id: str,
        envelope: Envelope,
        *,
        identity: Identity | None = None,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        """Execute one configured step with its resolved inputs in ``payload``.

        Use this to evaluate a step independently of upstream model errors.
        Input keys match the step's input/source/argument binding names. All
        adapter validation and limits apply; configured routes are not followed.
        """
        if not isinstance(step_id, str) or not step_id:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        return await self._invoke(
            workflow,
            envelope,
            identity=identity,
            transport_trace=transport_trace,
            step_id=step_id,
        )

    async def _invoke(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity | None,
        transport_trace: TraceContext | None,
        step_id: str | None = None,
    ) -> ExecutionResult:
        if not self._ready:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        runner = self._runners.get(workflow)
        if runner is None:
            raise ServiceError(ErrorCode.NOT_FOUND)
        selected_identity = identity or Identity(
            tenant_id=envelope.metadata.tenant_id,
            principal_id=envelope.metadata.principal_id,
        )
        accepted = accept_envelope(envelope, selected_identity)
        task = asyncio.current_task()
        if task is None:  # Defensive; this async method normally has a caller task.
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        self._active[task] = self._active.get(task, 0) + 1
        try:
            if step_id is not None:
                return to_execution_result(
                    await runner.run_step(
                        step_id,
                        accepted,
                        identity=selected_identity,
                        transport_trace=transport_trace,
                    )
                )
            return to_execution_result(
                await runner.run(
                    accepted,
                    identity=selected_identity,
                    transport_trace=transport_trace,
                )
            )
        finally:
            remaining = self._active[task] - 1
            if remaining:
                self._active[task] = remaining
            else:
                del self._active[task]

    async def aclose(self, *, timeout: float = 10.0) -> bool:
        """Stop new runs, drain callers and cancel remaining cooperative invocations."""
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 <= timeout <= 60
        ):
            raise ValueError("invalid shutdown timeout")
        self._ready = False
        tasks = set(self._active) - {asyncio.current_task()}
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=2.0)
        return not pending


def _report_incomplete() -> None:
    emit_event(
        _LOGGER,
        LogEvent.DEPENDENCY_REJECTED,
        level=logging.WARNING,
        error_code=ErrorCode.DEPENDENCY_FAILURE,
    )


@asynccontextmanager
async def _owned_models(
    factory: ModelFactory, profiles: ModelProfiles, environment: Mapping[str, str]
) -> AsyncIterator[Mapping[str, ModelBinding]]:
    """Report shutdown failures without replacing a completed run or caller error."""
    entered = False
    body_error: BaseException | None = None
    try:
        async with factory(profiles, environment=environment) as bindings:
            entered = True
            try:
                yield bindings
            except BaseException as error:
                body_error = error
                raise
    except Exception:
        if body_error is not None:
            raise body_error from None
        if not entered:
            raise
        _report_incomplete()


@asynccontextmanager
async def open_application(
    prepared: PreparedApplication,
    *,
    environment: Mapping[str, str],
    plugins: RuntimePlugins | None = None,
    install_global_telemetry: bool = False,
) -> AsyncIterator[WorkflowApplication]:
    """Own the in-memory runners, clients and optional observations."""
    from foliqant.lifecycle import drain_before_close

    selected = plugins or RuntimePlugins()
    credentials = load_environment(prepared.source, environment)
    config = EnvironmentResolver(credentials).resolve(prepared.config)
    observer: ExecutionObserver | None = None
    model_observation: ModelTelemetry | None = None
    telemetry = None
    application: WorkflowApplication | None = None
    try:
        async with AsyncExitStack() as stack:
            if config.telemetry is not None:
                from foliqant.adapters.telemetry.models import ModelTelemetry
                from foliqant.adapters.telemetry.observation import WorkflowTelemetry
                from foliqant.adapters.telemetry.privacy import TelemetryLabels
                from foliqant.adapters.telemetry.runtime import TelemetryRuntime

                labels = TelemetryLabels(
                    services=frozenset({config.telemetry.service_name}),
                    workflows=frozenset(prepared.plans),
                    steps=frozenset(
                        step.name for plan in prepared.plans.values() for step in plan.steps
                    ),
                    models=frozenset(profile.model for profile in prepared.config.models.values()),
                    providers=frozenset({"openai", "anthropic", "azure", "function"}),
                    tools=frozenset(
                        tool for server in config.mcp.values() for tool in server.catalog.tools
                    ),
                )
                telemetry = TelemetryRuntime.build(
                    config.telemetry, labels=labels, environment=credentials
                )
                if install_global_telemetry:
                    telemetry.install_global()
                if telemetry.startup_failures:
                    _report_incomplete()
                observer = WorkflowTelemetry(
                    telemetry.tracer_provider,
                    labels=labels,
                    meter_provider=telemetry.meter_provider,
                )
                model_observation = ModelTelemetry(
                    telemetry.tracer_provider, telemetry.meter_provider, labels
                )
            models: Mapping[str, ModelBinding] = {}
            if config.models:
                models = await stack.enter_async_context(
                    _owned_models(
                        selected.model_factory, ModelProfiles(models=config.models), credentials
                    )
                )
                if set(models) != set(config.models):
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            mcp_runtime = None
            mcp_executor: StepExecutor | None = None
            if config.mcp:
                from foliqant.adapters.mcp.runtime import McpExecutor, McpRuntime
                from foliqant.adapters.mcp.transport import McpClientSessionFactory
                from foliqant.contracts.mcp import McpProfiles

                profiles = McpProfiles(servers=config.mcp)
                factory = McpClientSessionFactory(
                    profiles, credential_providers=dict(selected.mcp_credentials)
                )
                authorizer = selected.tool_authorizer or _DeclaredReadAuthorizer(prepared)
                if telemetry is not None:
                    from foliqant.adapters.telemetry.observation import trace_carrier

                    mcp_runtime = McpRuntime(
                        profiles, factory, authorizer, trace_carrier=trace_carrier
                    )
                else:
                    mcp_runtime = McpRuntime(profiles, factory, authorizer)
                mcp_executor = McpExecutor(mcp_runtime)
            admission = CapacityLimiter(
                concurrency=config.execution.concurrency, queue_limit=config.execution.queue_limit
            )
            handler = HandlerExecutor(prepared.handlers)
            runners = {}
            for name, plan in prepared.plans.items():
                schemas = WorkflowSchemas(plan)
                model = (
                    ModelExecutor(models, schemas, tools=mcp_runtime, telemetry=model_observation)
                    if models
                    else None
                )
                runners[name] = WorkflowRunner(
                    plan,
                    executor=_RoutedExecutor(model, mcp_executor, handler),
                    validator=schemas,
                    admission=admission,
                    limits=config.execution.limits(),
                    observer=observer,
                )
            application = WorkflowApplication(runners)
            try:
                yield application
            finally:
                await drain_before_close(application)
    except (ImportError, ValueError, RuntimeError):
        if application is None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        raise
    finally:
        if telemetry is not None and not await telemetry.aclose():
            _report_incomplete()


__all__ = [
    "PreparedApplication",
    "RuntimePlugins",
    "WorkflowApplication",
    "load_environment",
    "open_application",
    "prepare_application",
]

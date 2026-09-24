"""Explicit composition of compiled workflows and owned async adapter lifespans."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import Field

from foliqant.adapters.handlers import HandlerExecutor
from foliqant.adapters.models import ModelBinding, ModelExecutor
from foliqant.adapters.models.providers import open_model_bindings
from foliqant.adapters.telemetry.logging import LogEvent, emit_event
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.contracts.envelope import Envelope, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.contracts.identifiers import Id
from foliqant.contracts.models import ModelConfig, ModelProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.conditions import describe_condition
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.execution import StepOutcome
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject
from foliqant.core.plan import HandlerStepPlan, MatchRoutingPlan, McpStepPlan
from foliqant.core.runner import WorkflowRunner
from foliqant.environment import EnvironmentResolver
from foliqant.ports.execution import OperationStep, StepContext, StepExecutor
from foliqant.ports.observation import ExecutionObserver, TraceContext
from foliqant.settings import (
    PreparedApplication,
    load_environment,
    prepare_application,
    require_handler_registrations,
    require_no_warnings,
)

if TYPE_CHECKING:
    from opentelemetry.trace import TracerProvider

    from foliqant.adapters.mcp.auth import McpCredentialProvider
    from foliqant.adapters.mcp.runtime import ToolAuthorizer
    from foliqant.adapters.telemetry.models import ModelTelemetry

_LOGGER = logging.getLogger("foliqant.runtime")


class _ApplicationModelProfiles(ModelProfiles):
    """Expanded runtime profiles; deployment aliases retain their authored limit."""

    models: Annotated[dict[Id, ModelConfig], Field(min_length=1)]


class ModelFactory(Protocol):
    def __call__(
        self, profiles: ModelProfiles, *, environment: Mapping[str, str]
    ) -> AbstractAsyncContextManager[Mapping[str, ModelBinding]]: ...


@dataclass(frozen=True, slots=True)
class RuntimePlugins:
    """Trusted Python extension points; configuration never imports executable code.

    ``tracer_provider`` is a host-owned OpenTelemetry tracer provider. With it,
    runtime spans join the host's provider: the runtime creates no provider or
    exporter of its own, never installs globals and never shuts the host
    provider down.
    """

    model_factory: ModelFactory = open_model_bindings
    mcp_credentials: Mapping[str, McpCredentialProvider] = field(default_factory=dict)
    tool_authorizer: ToolAuthorizer | None = None
    tracer_provider: TracerProvider | None = None


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
            step = plan.flow(context.flow_id).step(context.step_id)
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

    async def run_flow(
        self,
        workflow: str,
        flow_id: str,
        envelope: Envelope,
        *,
        identity: Identity | None = None,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        """Execute one flow against its resolved input, without boundary routing."""
        if not isinstance(flow_id, str) or not flow_id:
            raise ServiceError(ErrorCode.INVALID_INPUT)
        return await self._invoke(
            workflow,
            envelope,
            identity=identity,
            transport_trace=transport_trace,
            flow_id=flow_id,
        )

    async def run_step(
        self,
        workflow: str,
        flow_id: str,
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
        if (
            not isinstance(flow_id, str)
            or not flow_id
            or not isinstance(step_id, str)
            or not step_id
        ):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        return await self._invoke(
            workflow,
            envelope,
            identity=identity,
            transport_trace=transport_trace,
            flow_id=flow_id,
            step_id=step_id,
        )

    async def _invoke(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity | None,
        transport_trace: TraceContext | None,
        flow_id: str | None = None,
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
            if step_id is not None and flow_id is not None:
                return to_execution_result(
                    await runner.run_step(
                        flow_id,
                        step_id,
                        accepted,
                        identity=selected_identity,
                        transport_trace=transport_trace,
                    )
                )
            if flow_id is not None:
                return to_execution_result(
                    await runner.run_flow(
                        flow_id,
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
    # A strict preparation stays strict: warnings added later still refuse activation.
    require_no_warnings(prepared)
    require_handler_registrations(prepared)
    credentials = load_environment(prepared.source, environment)
    resolver = EnvironmentResolver(credentials)
    config = resolver.resolve(prepared.config)
    effective_models = (
        resolver.resolve(_ApplicationModelProfiles(models=dict(prepared._models)))
        if prepared._models
        else None
    )
    observer: ExecutionObserver | None = None
    model_observation: ModelTelemetry | None = None
    telemetry = None
    tracer_provider: TracerProvider | None = None
    tool_labels = None
    application: WorkflowApplication | None = None
    host_provider = selected.tracer_provider
    if host_provider is not None and install_global_telemetry:
        # A host that owns its provider also owns the process-global installation.
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    try:
        async with AsyncExitStack() as stack:
            if config.telemetry is not None or host_provider is not None:
                from opentelemetry.metrics import MeterProvider, NoOpMeterProvider

                from foliqant.adapters.telemetry.models import ModelTelemetry
                from foliqant.adapters.telemetry.observation import WorkflowTelemetry
                from foliqant.adapters.telemetry.privacy import TelemetryLabels
                from foliqant.adapters.telemetry.runtime import TelemetryRuntime

                routes = [
                    route
                    for plan in prepared.plans.values()
                    for flow in plan.flows
                    for route in (flow.transition, flow.on_unresolved)
                ]
                # Labels come from resolved configuration: an environment reference
                # such as `$MODEL_ID` is never a label; its resolved value is.
                labels, dropped = TelemetryLabels.accepted(
                    services=(
                        {config.telemetry.service_name} if config.telemetry is not None else set()
                    ),
                    workflows=prepared.plans,
                    flows=(flow.name for plan in prepared.plans.values() for flow in plan.flows),
                    steps=(
                        step.name
                        for plan in prepared.plans.values()
                        for flow in plan.flows
                        for step in flow.steps
                    ),
                    models=(
                        profile.model
                        for profile in (
                            effective_models.models.values() if effective_models else ()
                        )
                    ),
                    providers={"openai", "anthropic", "azure", "google", "bedrock", "function"},
                    tools=(tool for server in config.mcp.values() for tool in server.catalog.tools),
                    cases=(
                        key
                        for route in routes
                        if isinstance(route, MatchRoutingPlan)
                        for key, _ in route.cases
                    ),
                    conditions=(
                        describe_condition(step.when)
                        for plan in prepared.plans.values()
                        for flow in plan.flows
                        for step in flow.steps
                        if step.when is not None
                    ),
                )
                if dropped:
                    emit_event(
                        _LOGGER,
                        LogEvent.TELEMETRY_LABELS_DROPPED,
                        level=logging.WARNING,
                        count=dropped,
                    )
                meter_provider: MeterProvider = NoOpMeterProvider()
                if host_provider is not None:
                    # Spans join the host provider; its processors and exporters apply.
                    tracer_provider = host_provider
                else:
                    assert config.telemetry is not None
                    telemetry = TelemetryRuntime.build(
                        config.telemetry, labels=labels, environment=credentials
                    )
                    if install_global_telemetry:
                        telemetry.install_global()
                    if telemetry.startup_failures:
                        _report_incomplete()
                    tracer_provider = telemetry.tracer_provider
                    meter_provider = telemetry.meter_provider
                tool_labels = labels
                observer = WorkflowTelemetry(
                    tracer_provider,
                    labels=labels,
                    meter_provider=meter_provider,
                    conditions=config.telemetry.conditions
                    if config.telemetry is not None
                    else False,
                )
                model_observation = ModelTelemetry(tracer_provider, meter_provider, labels)
            models: Mapping[str, ModelBinding] = {}
            if effective_models is not None:
                models = await stack.enter_async_context(
                    _owned_models(selected.model_factory, effective_models, credentials)
                )
                if set(models) != set(effective_models.models):
                    raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
                model_profiles = effective_models.models
                # Pricing is configuration, applied the same way to any model factory.
                models = {
                    alias: replace(
                        binding,
                        admission=(
                            models[prepared._model_admission_groups[alias]].admission
                            if alias in prepared._model_admission_groups
                            else binding.admission
                        ),
                        pricing=(
                            pricing.plan()
                            if (pricing := model_profiles[alias].pricing) is not None
                            else None
                        ),
                    )
                    for alias, binding in models.items()
                }
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
                if tracer_provider is not None and tool_labels is not None:
                    from foliqant.adapters.telemetry.observation import trace_carrier
                    from foliqant.adapters.telemetry.tools import ToolTelemetry

                    mcp_runtime = McpRuntime(
                        profiles,
                        factory,
                        authorizer,
                        trace_carrier=trace_carrier,
                        telemetry=ToolTelemetry(tracer_provider, labels=tool_labels),
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

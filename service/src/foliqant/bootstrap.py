"""Explicit composition of compiled workflows and owned async adapter lifespans."""

from __future__ import annotations

import asyncio
import logging
import math
import sys
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
from foliqant.ports.auth import AuthenticatedCaller, Authenticator
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
        # Input adapters grant workflow access; the compiled step bounds its tools.
        # A host can replace this policy for resource-specific business permissions.
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
        identity: Identity,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        """Execute as an already authenticated/authorized host caller."""
        if not self._ready:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        runner = self._runners.get(workflow)
        if runner is None:
            raise ServiceError(ErrorCode.NOT_FOUND)
        accepted = accept_envelope(envelope, identity)
        task = asyncio.current_task()
        if task is None:  # Defensive; this async method normally has a caller task.
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        self._active[task] = self._active.get(task, 0) + 1
        try:
            return to_execution_result(
                await runner.run(accepted, identity=identity, transport_trace=transport_trace)
            )
        finally:
            remaining = self._active[task] - 1
            if remaining:
                self._active[task] = remaining
            else:
                del self._active[task]

    async def aclose(self, *, timeout: float = 10.0) -> bool:
        """Stop intake, drain callers and cancel remaining cooperative invocations."""
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


class _HttpRuntime:
    """Protocol-compatible delegate activated only inside the ASGI lifespan."""

    def __init__(self) -> None:
        self._application: WorkflowApplication | None = None
        self._authenticator: Authenticator | None = None

    def activate(self, application: WorkflowApplication, authenticator: Authenticator) -> None:
        if self._application is not None or self._authenticator is not None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        self._application = application
        self._authenticator = authenticator

    def deactivate(self) -> None:
        self._application = None
        self._authenticator = None

    @property
    def workflow_names(self) -> tuple[str, ...]:
        application = self._application
        return () if application is None else application.workflow_names

    @property
    def ready(self) -> bool:
        application = self._application
        return application is not None and application.ready

    async def run(
        self,
        workflow: str,
        envelope: Envelope,
        *,
        identity: Identity,
        transport_trace: TraceContext | None = None,
    ) -> ExecutionResult:
        application = self._application
        if application is None:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        return await application.run(
            workflow,
            envelope,
            identity=identity,
            transport_trace=transport_trace,
        )

    async def authenticate(self, authorization: str | None) -> AuthenticatedCaller:
        authenticator = self._authenticator
        if authenticator is None:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
        return await authenticator.authenticate(authorization)


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
    """Own clients and optional observations; do no model/MCP calls during startup."""
    selected = plugins or RuntimePlugins()
    config = prepared.config
    credentials = dict(environment)
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
                    models=frozenset(profile.model for profile in config.models.values()),
                    providers=frozenset(
                        {"openai", "anthropic", "azure", "aws.bedrock", "function"}
                    ),
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
                if not await application.aclose():
                    _report_incomplete()
    except (ImportError, ValueError, RuntimeError):
        if application is None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        raise
    finally:
        if telemetry is not None and not await telemetry.aclose():
            _report_incomplete()


async def serve_application(
    prepared: PreparedApplication,
    *,
    environment: Mapping[str, str],
    plugins: RuntimePlugins | None = None,
    debug: bool = False,
) -> None:
    """Run HTTP with every runtime resource owned by the standard ASGI lifespan."""
    try:
        import uvicorn

        from foliqant.adapters.auth import (
            BearerAuthenticator,
            DevelopmentAuthenticator,
            JwtAuthenticator,
        )
        from foliqant.adapters.telemetry.logging import LogLabels, configure_logging
        from foliqant.adapters.transports.http import create_http_app
        from foliqant.contracts.auth import BearerAuthConfig, DevelopmentAuthConfig, JwtAuthConfig
    except ImportError:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
    config = prepared.config
    http_config = config.http
    if http_config is None:
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    runtime = _HttpRuntime()
    logging_runtime = configure_logging(
        debug=debug,
        labels=LogLabels(
            services=frozenset({"foliqant"}),
            workflows=frozenset(prepared.plans),
            steps=frozenset(step.name for plan in prepared.plans.values() for step in plan.steps),
        ),
    )
    logging_closed = False

    async def close_logging() -> None:
        nonlocal logging_closed
        if logging_closed:
            return
        clean = await asyncio.to_thread(logging_runtime.close)
        logging_closed = True
        if not clean:
            try:
                sys.stderr.write('{"level": "warning", "event": "logging_shutdown_incomplete"}\n')
                sys.stderr.flush()
            except Exception:
                pass

    @asynccontextmanager
    async def lifespan(_: object) -> AsyncIterator[None]:
        authenticator: Authenticator | None = None
        try:
            auth = http_config.auth
            if isinstance(auth, BearerAuthConfig):
                authenticator = BearerAuthenticator(auth, environment=environment)
            elif isinstance(auth, JwtAuthConfig):
                authenticator = JwtAuthenticator(auth)
            elif isinstance(auth, DevelopmentAuthConfig):
                authenticator = DevelopmentAuthenticator(frozenset(prepared.plans))
            else:
                raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
            async with open_application(
                prepared,
                environment=environment,
                plugins=plugins,
                install_global_telemetry=True,
            ) as application:
                runtime.activate(application, authenticator)
                try:
                    yield
                finally:
                    runtime.deactivate()
        finally:
            if isinstance(authenticator, JwtAuthenticator):
                await authenticator.aclose()
            await close_logging()

    app = create_http_app(
        runtime,
        runtime,
        config=http_config,
        mode=config.mode,
        lifespan=lifespan,
    )
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=http_config.host,
                port=http_config.port,
                log_config=None,
                access_log=False,
                proxy_headers=False,
                server_header=False,
                timeout_graceful_shutdown=10,
            )
        )
        try:
            await server.serve()
        except SystemExit:
            # Uvicorn uses SystemExit for bind failures after asking the ASGI
            # lifespan to shut down. Keep CLI failures inside the safe contract.
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None
        if not server.started:
            # Lifespan startup failures set should_exit without raising back to
            # the caller. Do not report a successful stopped service.
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)
    finally:
        # Handles configuration/server construction and lifespan startup errors.
        # Normal signal shutdown already closes this inside ASGI lifespan before
        # Uvicorn restores and replays the process signal.
        await close_logging()


__all__ = [
    "PreparedApplication",
    "RuntimePlugins",
    "WorkflowApplication",
    "load_environment",
    "open_application",
    "prepare_application",
    "serve_application",
]

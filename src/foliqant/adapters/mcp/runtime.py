"""Per-caller MCP sessions, declared tool enforcement and bounded invocation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol, cast

from mcp import Client, MCPError, types
from pydantic import TypeAdapter, ValidationError

from foliqant.contracts.mcp import McpProfiles
from foliqant.core.errors import TIMEOUT_CODES, ErrorCode, ServiceError, timeout_code
from foliqant.core.execution import StepOutcome
from foliqant.core.json import FrozenJson, FrozenObject, JsonValue, freeze_json, thaw_json
from foliqant.core.plan import McpStepPlan
from foliqant.core.retry import RetryPolicy, retry
from foliqant.environment import EnvironmentResolver
from foliqant.ports.execution import OperationStep, StepContext
from foliqant.ports.tools import ToolInputRequired

from .catalog import ToolCatalog
from .retry import observe_http_attempt

if TYPE_CHECKING:
    from foliqant.adapters.telemetry.tools import ToolTelemetry

    from .transport import McpSessionFactory

_MAX_CATALOG_PAGES = 128
_MAX_DISCOVERED_TOOLS = 1024
_MAX_CATALOG_BYTES = 1_048_576
_TOOL_RESULT: TypeAdapter[types.CallToolResult | types.InputRequiredResult] = TypeAdapter(
    types.CallToolResult | types.InputRequiredResult
)


class ToolAuthorizer(Protocol):
    """Reauthorize every invocation using trusted context and validated arguments."""

    async def authorize(
        self, server: str, tool: str, arguments: FrozenObject, context: StepContext
    ) -> None: ...


class McpTools:
    """One authenticated session's bounded tools; never share between callers."""

    def __init__(
        self,
        *,
        client: Client,
        server: str,
        catalog: ToolCatalog,
        allowed: tuple[str, ...],
        authorizer: ToolAuthorizer,
        context: StepContext,
        timeout: float,
        identity_meta_key: str | None,
        trace_carrier: Callable[[], Mapping[str, str]],
        retry_policy: RetryPolicy,
        telemetry: ToolTelemetry | None = None,
    ) -> None:
        self._client = client
        self._server = server
        self._catalog = catalog
        self.names = allowed
        self._authorizer = authorizer
        self._context = context
        self._timeout = timeout
        self._identity_meta_key = identity_meta_key
        self._trace_carrier = trace_carrier
        self._retry = retry_policy
        self._telemetry = telemetry
        self.successful: set[str] = set()
        self._active = True

    def close(self) -> None:
        """Prevent retained callbacks from using a session after its lifespan."""
        self._active = False

    def input_schema(self, name: str) -> dict[str, JsonValue]:
        if name not in self.names:
            raise ServiceError(ErrorCode.FORBIDDEN)
        return self._catalog.input_schema(name)

    def _metadata(self) -> types.RequestParamsMeta:
        carrier = self._trace_carrier()
        if any(
            key not in {"traceparent", "tracestate"}
            or not isinstance(value, str)
            or len(value) > 512
            for key, value in carrier.items()
        ):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        # Captured per call; the HTTP transport mirrors this request's carrier as
        # W3C headers of the same request.
        meta: dict[str, object] = dict(carrier)
        identity = self._context.caller.identity
        if self._identity_meta_key is not None:
            identifiers = {
                key: value
                for key, value in (
                    ("tenant_id", identity.tenant_id),
                    ("principal_id", identity.principal_id),
                )
                if value is not None
            }
            if identifiers:
                meta[self._identity_meta_key] = identifiers
        return cast(types.RequestParamsMeta, meta)

    async def call(self, name: str, arguments: FrozenObject) -> FrozenJson:
        """Authorize and reserve before one call; validate before recording success."""
        if not self._active or name not in self.names:
            raise ServiceError(ErrorCode.FORBIDDEN)
        frozen = freeze_json(arguments)
        if not isinstance(frozen, Mapping):
            raise ServiceError(ErrorCode.INVALID_INPUT)
        arguments = frozen
        self._catalog.validate_input(name, arguments)
        if self._catalog.effect(name) != "read":
            # Durable effect identity/reconciliation must precede enabling writes.
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        deadline = min(self._context.deadline, asyncio.get_running_loop().time() + self._timeout)

        def expired() -> ServiceError:
            """``run_timeout`` when the run deadline was the bound, else ``request_timeout``."""
            return ServiceError(
                timeout_code(asyncio.get_running_loop().time(), self._context.deadline)
            )

        if deadline <= asyncio.get_running_loop().time():
            raise expired()

        async def call_once(attempt: int) -> FrozenJson:
            if not self._active:
                raise ServiceError(ErrorCode.FORBIDDEN)
            try:
                async with asyncio.timeout_at(deadline):
                    await self._authorizer.authorize(self._server, name, arguments, self._context)
                if asyncio.get_running_loop().time() >= deadline:
                    raise expired()
                await self._context.budget.start_tool_call()
                with (
                    self._telemetry.observe(name, attempt=attempt)
                    if self._telemetry is not None
                    else nullcontext()
                ):
                    try:
                        async with asyncio.timeout_at(deadline):
                            metadata = self._metadata()
                            with observe_http_attempt() as observation:
                                try:
                                    result = await self._client.session.send_request(
                                        types.CallToolRequest(
                                            params=types.CallToolRequestParams(
                                                name=name,
                                                arguments=cast(
                                                    dict[str, object], thaw_json(arguments)
                                                ),
                                                meta=metadata,
                                            )
                                        ),
                                        _TOOL_RESULT,
                                        request_read_timeout_seconds=max(
                                            0.001, deadline - asyncio.get_running_loop().time()
                                        ),
                                    )
                                except MCPError as error:
                                    failure = _call_failure(error, observation.failure, expired)
                                    raise failure from None
                            if asyncio.get_running_loop().time() >= deadline:
                                raise expired()
                            if isinstance(result, types.InputRequiredResult):
                                raise ToolInputRequired()
                            value = self._catalog.validate_result(name, result)
                            self.successful.add(name)
                            return value
                    except TimeoutError:
                        raise expired() from None
                    except ValidationError:
                        raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
            except (asyncio.CancelledError, ServiceError, ToolInputRequired):
                raise
            except TimeoutError:
                raise expired() from None
            except Exception:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None

        try:
            return await retry(call_once, policy=self._retry, deadline=lambda: deadline)
        except ServiceError as error:
            if error.code in TIMEOUT_CODES and not error.retryable:
                raise expired() from None
            raise


#: JSON-RPC errors of a server that rejected a validated call.
_REJECTED_CALL_CODES = frozenset(
    {types.INVALID_PARAMS, types.INVALID_REQUEST, types.METHOD_NOT_FOUND}
)


def _call_failure(
    error: MCPError, http: ServiceError | None, expired: Callable[[], ServiceError]
) -> ServiceError:
    """The canonical failure of one ``tools/call``; never uses the server's error text.

    The HTTP status of the call chooses the code. It grants retry permission
    only when the SDK normalized the response to an internal error, so a peer's
    JSON-RPC error never authorizes a retry.
    """
    if error.code == types.REQUEST_TIMEOUT:
        return expired()
    if http is not None:
        if http.code in TIMEOUT_CODES and not http.retryable:
            return expired()
        if http.retryable and error.code != types.INTERNAL_ERROR:
            return ServiceError(http.code)
        return http
    if error.code in _REJECTED_CALL_CODES:
        return ServiceError(ErrorCode.REQUEST_REJECTED)
    return ServiceError(ErrorCode.DEPENDENCY_FAILURE)


class McpRuntime:
    """Build isolated sessions from frozen profiles and host-owned authorization."""

    def __init__(
        self,
        profiles: McpProfiles,
        factory: McpSessionFactory,
        authorizer: ToolAuthorizer,
        *,
        trace_carrier: Callable[[], Mapping[str, str]] | None = None,
        telemetry: ToolTelemetry | None = None,
    ) -> None:
        try:
            self._profiles = EnvironmentResolver({}).resolve(profiles).model_copy(deep=True)
            self._catalogs = {
                name: ToolCatalog(profile.catalog, max_result_bytes=profile.output_limit_bytes)
                for name, profile in self._profiles.servers.items()
            }
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION) from None
        self._factory = factory
        self._authorizer = authorizer
        self._trace_carrier = trace_carrier or (lambda: {})
        self._telemetry = telemetry

    @staticmethod
    async def _discover(
        client: Client, metadata: Callable[[], types.RequestParamsMeta]
    ) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursors: set[str] = set()
        cursor = None
        size = 0
        for _ in range(_MAX_CATALOG_PAGES):
            page = await client.list_tools(cursor=cursor, cache_mode="bypass", meta=metadata())
            size += len(page.model_dump_json(by_alias=True).encode("utf-8"))
            tools.extend(page.tools)
            if len(tools) > _MAX_DISCOVERED_TOOLS or size > _MAX_CATALOG_BYTES:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in cursors:
                raise ServiceError(ErrorCode.INVALID_OUTPUT)
            cursors.add(cursor)
        raise ServiceError(ErrorCode.INVALID_OUTPUT)

    @asynccontextmanager
    async def open(
        self, server: str, allowed: tuple[str, ...], context: StepContext
    ) -> AsyncIterator[McpTools]:
        """Connect and verify a catalog for this caller without expanding permissions."""
        profile = self._profiles.servers.get(server)
        catalog = self._catalogs.get(server)
        if profile is None or catalog is None or not allowed or len(set(allowed)) != len(allowed):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        if any(name not in catalog.names for name in allowed):
            raise ServiceError(ErrorCode.FORBIDDEN)
        if any(catalog.effect(name) != "read" for name in allowed):
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        timeout = min(profile.request_timeout, context.tool_timeout)
        pending: BaseException | None = None
        try:
            async with self._factory.open(server, context) as client:
                scope: McpTools | None = None
                try:
                    scope = McpTools(
                        client=client,
                        server=server,
                        catalog=catalog,
                        allowed=allowed,
                        authorizer=self._authorizer,
                        context=context,
                        timeout=timeout,
                        identity_meta_key=profile.identity_meta_key,
                        trace_carrier=self._trace_carrier,
                        retry_policy=profile.retry.policy(),
                        telemetry=self._telemetry,
                    )
                    deadline = min(context.deadline, asyncio.get_running_loop().time() + timeout)
                    async with asyncio.timeout_at(deadline):
                        catalog.verify(await self._discover(client, scope._metadata))
                    yield scope
                except BaseException as error:
                    # AnyIO SDK task groups otherwise wrap application control
                    # signals in exception groups during their context exit.
                    pending = error
                finally:
                    if scope is not None:
                        scope.close()
            if pending is not None:
                raise pending
        except (asyncio.CancelledError, ToolInputRequired):
            raise
        except ServiceError as error:
            if error.code in TIMEOUT_CODES and not error.retryable:
                raise ServiceError(
                    timeout_code(asyncio.get_running_loop().time(), context.deadline)
                ) from None
            raise
        except TimeoutError:
            raise ServiceError(
                timeout_code(asyncio.get_running_loop().time(), context.deadline)
            ) from None
        except MCPError as error:
            code = (
                timeout_code(asyncio.get_running_loop().time(), context.deadline)
                if error.code == types.REQUEST_TIMEOUT
                else ErrorCode.DEPENDENCY_FAILURE
            )
            raise ServiceError(code) from None
        except ValidationError:
            raise ServiceError(ErrorCode.INVALID_OUTPUT) from None
        except Exception:
            raise ServiceError(ErrorCode.DEPENDENCY_FAILURE) from None


class McpExecutor:
    """Execute an explicit MCP step through the same runtime used by model tools."""

    def __init__(self, runtime: McpRuntime) -> None:
        self._runtime = runtime

    async def execute(
        self, step: OperationStep, inputs: FrozenObject, context: StepContext
    ) -> StepOutcome:
        if not isinstance(step, McpStepPlan) or context.step_id != step.name:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        profile = self._runtime._profiles.servers.get(step.server)
        if profile is None:
            raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
        # A direct MCP step has one deadline across connect, discovery and call.
        run_deadline = context.deadline
        context = replace(
            context,
            deadline=min(
                context.deadline,
                asyncio.get_running_loop().time()
                + min(context.tool_timeout, profile.request_timeout),
            ),
        )
        try:
            async with self._runtime.open(step.server, (step.tool,), context) as tools:
                return StepOutcome(await tools.call(step.tool, inputs))
        except ToolInputRequired:
            # The server asked for human input (MCP elicitation): a protocol
            # outcome that needs review, not a failure of the call.
            return StepOutcome(None, needs_review=True)
        except ServiceError as error:
            if error.code in TIMEOUT_CODES and not error.retryable:
                # The step deadline is this step's own bound, not the run's.
                now = asyncio.get_running_loop().time()
                raise ServiceError(timeout_code(now, run_deadline)) from None
            raise

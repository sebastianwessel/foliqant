"""Run a compiled workflow against the local synthetic stdio MCP server."""

import asyncio
import json
import sys
from pathlib import Path

from foliqant.adapters.mcp import McpClientSessionFactory
from foliqant.adapters.mcp.runtime import McpExecutor, McpRuntime
from foliqant.adapters.telemetry.logging import configure_logging
from foliqant.adapters.validation import WorkflowSchemas
from foliqant.compiler import CompilationError, compile_workflow
from foliqant.contracts.envelope import Envelope, Metadata, accept_envelope
from foliqant.contracts.execution import ExecutionResult, to_execution_result
from foliqant.contracts.mcp import McpProfiles
from foliqant.core.admission import CapacityLimiter
from foliqant.core.errors import ErrorCode, ServiceError
from foliqant.core.identity import Identity
from foliqant.core.json import FrozenObject, JsonValue
from foliqant.core.runner import ExecutionLimits, WorkflowRunner
from foliqant.ports.execution import StepContext

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
SERVER_PATH = (EXAMPLE_DIRECTORY / "server.py").resolve(strict=True)
IDENTITY_META_KEY = "example.test/foliqant/identity"
DEMO_IDENTITY = Identity(tenant_id="demo_tenant", principal_id="demo_user")

# This is the reviewed offline declaration. MCP discovery must match these
# SDK-generated schemas exactly before the runtime authorizes the tool.
INPUT_SCHEMA: dict[str, JsonValue] = {
    "properties": {"query": {"title": "Query", "type": "string"}},
    "required": ["query"],
    "title": "lookupArguments",
    "type": "object",
}
OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "additionalProperties": False,
    "description": "Structured synthetic record returned by the demo tool.",
    "properties": {
        "classification": {
            "const": "synthetic",
            "title": "Classification",
            "type": "string",
        },
        "identity": {
            "additionalProperties": {"type": "string"},
            "title": "Identity",
            "type": "object",
        },
        "symbol": {"title": "Symbol", "type": "string"},
    },
    "required": ["symbol", "classification", "identity"],
    "title": "LookupResult",
    "type": "object",
}


class DemoAllowAuthorizer:
    """Demo-only allow hook; this is deliberately not authentication."""

    async def authorize(
        self,
        server: str,
        tool: str,
        arguments: FrozenObject,
        context: StepContext,
    ) -> None:
        if server != "synthetic_records" or tool != "lookup":
            raise ServiceError(ErrorCode.FORBIDDEN)


def profiles() -> McpProfiles:
    """Build trusted startup configuration for the bundled local subprocess."""

    # Keep the active virtual-environment executable path intact: resolving its
    # symlink would bypass the environment that owns the installed MCP extra.
    python = Path(sys.executable).absolute()
    if not python.is_file():
        raise ServiceError(ErrorCode.INVALID_CONFIGURATION)
    return McpProfiles.model_validate(
        {
            "servers": {
                "synthetic_records": {
                    "transport": {
                        "type": "stdio",
                        "command": str(python),
                        "args": [str(SERVER_PATH)],
                        "cwd": str(EXAMPLE_DIRECTORY),
                        "env": {"PYTHONUNBUFFERED": "1"},
                    },
                    "identity_meta_key": IDENTITY_META_KEY,
                    "catalog": {
                        "tools": {
                            "lookup": {
                                "input_schema": INPUT_SCHEMA,
                                "output_schema": OUTPUT_SCHEMA,
                                "effect": "read",
                            }
                        }
                    },
                    "concurrency": 1,
                    "queue_limit": 0,
                    "request_timeout": 3.0,
                    "output_limit_bytes": 4096,
                }
            }
        },
        strict=True,
    )


async def run_example(
    payload: dict[str, JsonValue], *, configure_process_logging: bool = True
) -> ExecutionResult:
    """Compile and run one nondurable workflow through a real stdio MCP session."""

    logging_runtime = configure_logging() if configure_process_logging else None
    failed = False
    try:
        selected_profiles = profiles()
        plan = compile_workflow(
            EXAMPLE_DIRECTORY,
            model_aliases={},
            tool_catalogs={
                "synthetic_records": selected_profiles.servers["synthetic_records"].catalog
            },
            handler_names=set(),
        )
        factory = McpClientSessionFactory(selected_profiles, credential_providers={})
        runtime = McpRuntime(selected_profiles, factory, DemoAllowAuthorizer())
        runner = WorkflowRunner(
            plan,
            executor=McpExecutor(runtime),
            validator=WorkflowSchemas(plan),
            admission=CapacityLimiter(concurrency=1, queue_limit=0),
            limits=ExecutionLimits(run_timeout=5.0, tool_timeout=3.0),
        )
        envelope = accept_envelope(
            Envelope(
                payload=payload,
                metadata=Metadata.model_validate({"source": "mcp_stdio_demo"}, strict=True),
            ),
            DEMO_IDENTITY,
        )
        return to_execution_result(await runner.run(envelope, identity=DEMO_IDENTITY))
    except BaseException:
        failed = True
        raise
    finally:
        if logging_runtime is not None:
            clean = await asyncio.to_thread(logging_runtime.close, timeout=1.0)
            if not clean and not failed:
                raise ServiceError(ErrorCode.DEPENDENCY_FAILURE)


def _safe_error(error: ServiceError) -> int:
    print(
        json.dumps({"error": {"code": error.code, "message": str(error)}}),
        file=sys.stderr,
    )
    return 1


def main() -> int:
    try:
        result = asyncio.run(run_example({"query": "alpha"}))
    except KeyboardInterrupt:
        return 130
    except CompilationError:
        return _safe_error(ServiceError(ErrorCode.INVALID_CONFIGURATION))
    except ServiceError as error:
        return _safe_error(error)
    except Exception:
        return _safe_error(ServiceError(ErrorCode.DEPENDENCY_FAILURE))
    if result.execution.status == "failed":
        failure = result.execution.error
        assert failure is not None
        return _safe_error(ServiceError(ErrorCode(failure.code)))
    print(json.dumps(result.model_dump(mode="json", by_alias=True), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

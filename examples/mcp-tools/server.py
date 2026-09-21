"""Local synthetic MCP server used only by the stdio example."""

from typing import Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict

IDENTITY_META_KEY = "example.test/foliqant/identity"


class LookupResult(BaseModel):
    """Structured synthetic record returned by the demo tool."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    classification: Literal["synthetic"]
    identity: dict[str, str]


server = MCPServer(
    "foliqant-synthetic-records",
    description="Local synthetic records for the Foliqant stdio example.",
)


@server.tool(description="Look up a synthetic record by query.", structured_output=True)
async def lookup(query: str, context: Context) -> LookupResult:
    """Return deterministic data and the optional protected identity metadata."""

    metadata = context.request_context.meta or {}
    raw_identity = metadata.get(IDENTITY_META_KEY, {})
    identity = (
        {
            key: value
            for key, value in cast(dict[object, object], raw_identity).items()
            if key in {"tenant_id", "principal_id"} and isinstance(value, str)
        }
        if isinstance(raw_identity, dict)
        else {}
    )
    return LookupResult(
        symbol=f"SYN-{query.upper()}",
        classification="synthetic",
        identity=identity,
    )


if __name__ == "__main__":
    server.run(transport="stdio")

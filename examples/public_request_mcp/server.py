"""Local synthetic records-office MCP server for the example workflow."""

from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict


class RequestStatus(BaseModel):
    """Status of a synthetic public-record request."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    status: Literal["in_review"]
    due_date: str
    assigned_team: str


server = MCPServer(
    "foliqant-records-office",
    description="Synthetic public-record request status for the Foliqant example.",
)


@server.tool(description="Look up a public-record request by reference.", structured_output=True)
async def lookup_request(reference: str, context: Context) -> RequestStatus:
    """Return deterministic synthetic status data."""

    del context
    return RequestStatus(
        reference=reference,
        status="in_review",
        due_date="2026-10-05",
        assigned_team="records_review",
    )


if __name__ == "__main__":
    server.run(transport="stdio")

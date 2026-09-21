"""Local synthetic records-office MCP server for the extraction example."""

from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict


class RequestStatus(BaseModel):
    """Status returned for a synthetic public-record request."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    language: Literal["en", "de"]
    status: Literal["in_review"]
    due_date: str


server = MCPServer(
    "foliqant-extracted-records-office",
    description="Synthetic request status for the extraction-to-MCP example.",
)


@server.tool(description="Look up a public-record request by reference.", structured_output=True)
async def lookup_request(
    reference: str, language: Literal["en", "de"], context: Context
) -> RequestStatus:
    """Return deterministic data using exactly the declared selected arguments."""

    del context
    return RequestStatus(
        reference=reference,
        language=language,
        status="in_review",
        due_date="2026-10-05" if language == "en" else "05.10.2026",
    )


if __name__ == "__main__":
    server.run(transport="stdio")

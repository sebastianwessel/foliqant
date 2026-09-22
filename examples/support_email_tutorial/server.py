"""A local read-only MCP server over synthetic account records."""

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, ConfigDict


class AccountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_reference: str
    plan: str
    renewal_date: str


server = MCPServer("tutorial-account-records", description="Synthetic support records.")


@server.tool(description="Look up a synthetic account.", structured_output=True)
async def lookup_account(account_reference: str, context: Context) -> AccountRecord:
    del context
    records = {
        "A-100": ("Basic", "2026-12-01"),
        "A-200": ("Plus", "2026-11-15"),
    }
    plan, renewal_date = records.get(account_reference, ("Unknown", "unknown"))
    return AccountRecord(
        account_reference=account_reference,
        plan=plan,
        renewal_date=renewal_date,
    )


if __name__ == "__main__":
    server.run(transport="stdio")

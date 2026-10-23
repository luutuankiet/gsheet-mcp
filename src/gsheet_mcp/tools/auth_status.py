"""`auth_status`: who is signed in, and with which scopes."""
from __future__ import annotations

from datetime import UTC, datetime

from fastmcp.tools import ToolResult

from .. import auth, reply
from ..app import READ, tool


@tool(READ)
def auth_status() -> ToolResult:
    """Who is signed in to gsheet-mcp, and with which Google scopes.

    Needs no spreadsheet. Never shows the token itself.
    """
    info = auth.credentials_info()
    if not info["signed_in"]:
        return reply.text("not signed in: re-authenticate this MCP server in your MCP client")
    lines = [f"signed in as {info.get('email') or 'an account with no email claim'}"]
    scopes = info["scopes"]
    lines.append(f"scopes ({len(scopes)}):")
    lines += [f"  {scope}" for scope in scopes]
    if expiry := info.get("token_expiry"):
        when = datetime.fromtimestamp(int(expiry), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"token expires {when}")
    return reply.text("\n".join(lines))

# Copied from google-docs-mcp@2fb4652 src/<package>/app.py; renamed, Docs instructions and refusals dropped.
"""The FastMCP server object and the decorator every tool registers with.

Tools live one per module under `gsheet_mcp/tools/`; each imports `tool`
and a hints constant from here. `server.py` imports the tools package so that
importing the server registers every tool.
"""
from __future__ import annotations

import functools
import inspect
import json
import sys
import time

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, auth, reply

# Some clients show these before any tool is loaded (tool search), so they
# route an agent that has found only one tool.
INSTRUCTIONS = (
    "gsheet-mcp works in a Google Sheet from its URL. Start with `scan_sheet`, then read "
    "with `read_range`. Check who is signed in, and with which scopes, with `auth_status`. "
    "Before a sheet script, raw `batch_update` requests or a pull to DuckDB, load the "
    "gsheet-mcp skill with your own skill loader if it is installed, otherwise call "
    "`install_skill`."
)

mcp = FastMCP("gsheet-mcp", version=__version__, instructions=INSTRUCTIONS)
# fastmcp advertises tools.listChanged; this server's tool list never changes.
mcp._mcp_server.notification_options.tools_changed = False
_fastmcp_tool = mcp.tool


def tool(hints: dict[str, bool]):
    """Register a tool. Keeps the whole docstring as the description (fastmcp
    would drop `Returns:`), sets all four hints (clients treat a tool with no
    hints as destructive), declares no output schema, so a reply never carries
    structuredContent (see reply.py), and turns an expected failure into an
    error reply holding only its message.

    The refusal is caught here, inside the tool, because FastMCP middleware
    never sees the tool's own exception: by then it has logged a traceback
    and wrapped it in ToolError("Error calling tool ...")."""
    def register(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def refusing(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except REFUSED as e:
                    return reply.refusal(str(e))
        else:
            @functools.wraps(fn)
            def refusing(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except REFUSED as e:
                    return reply.refusal(str(e))
        return _fastmcp_tool(description=fn.__doc__, annotations=hints,
                             output_schema=None)(refusing)
    return register


def _hints(read_only: bool, destructive: bool, idempotent: bool) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive,
            "idempotentHint": idempotent, "openWorldHint": True}


READ = _hints(read_only=True, destructive=False, idempotent=True)
ADD = _hints(read_only=False, destructive=False, idempotent=False)
REPLACE = _hints(read_only=False, destructive=True, idempotent=True)
EDIT = _hints(read_only=False, destructive=True, idempotent=False)

# Expected failures an agent can act on; the message is the whole reply, with
# no "Error calling tool" prefix and no traceback in the server log.
REFUSED = (reply.Refusal, auth.NotSignedInError, ValueError)


def log_call(tool_name: str, seconds: float, ok: bool) -> None:
    """One stderr line per tool call, for usage questions. No arguments, no content."""
    line = json.dumps({"tool": tool_name, "ms": round(seconds * 1000), "ok": ok})
    print(f"gsheet-mcp call {line}", file=sys.stderr, flush=True)


class _CallLog(Middleware):
    """One content-free stderr line per tool call: name, duration, outcome."""

    async def on_call_tool(self, context, call_next):
        start = time.monotonic()
        ok = False
        try:
            result = await call_next(context)
            ok = not getattr(result, "is_error", False)
            return result
        finally:
            log_call(context.message.name, time.monotonic() - start, ok)


mcp.add_middleware(_CallLog())


# /health, not /healthz: Cloud Run's front end answers /healthz with its own 404
# and never forwards it to the container.
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness for whoever deployed it; needs no sign-in and touches no store."""
    return JSONResponse({"status": "ok", "version": __version__})

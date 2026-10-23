"""`upload_slot`: a signed PUT for a CLI query result, as CSV."""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import reply, transfer
from ..app import ADD, tool


@tool(ADD)
def upload_slot() -> ToolResult:
    """A signed upload URL for a CSV, so a CLI query result can come back.

    `gsheet push` uses it: PUT the CSV with `Content-Type: text/csv`. Then
    write_cells with `upload=<handle>` and a target plans the landing into an
    empty range; applying the plan writes it.

    Returns:
      The handle, the upload URL (valid 24 hours) and its expiry.
    """
    slot = transfer.new_slot()
    return reply.text(f"upload handle {slot.handle} · PUT with Content-Type: text/csv · "
                      f"valid until {transfer.iso(slot.expires_at)}\nurl {slot.url}")

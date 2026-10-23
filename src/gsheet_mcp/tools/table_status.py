"""`table_status`: has a pulled tab changed? A head-and-tail fingerprint."""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import pull, reply, sheets_api
from ..app import READ, tool


@tool(READ)
def table_status(spreadsheet: str, tab: str, since: str | None = None) -> ToolResult:
    """Fingerprint a tab: its size plus a hash of its first and last 500 rows.

    Cheap next to a pull. Pass the fingerprint from pull_tables (or an
    earlier table_status) as `since`: the reply says `changed` and what, or
    `unchanged at head and tail`. Rows in between are not compared, so an
    edit there is not seen; pull again to be sure. Never pulls on its own.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      tab: the tab's name, or a region alias on it (`tab#2`).
      since: an earlier fingerprint to compare with.

    Returns:
      `tab "<name>" (gid N) · fingerprint <rows>x<cols>:<head>:<tail>`, then
      the comparison when `since` is given.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        return reply.text(pull.status(spreadsheet_id, tab, since))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot check spreadsheet {spreadsheet_id}: {e}") from e

"""`pull_tables`: tabs as CSV behind signed links, with the scan's regions."""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import pull, reply, sheets_api
from ..app import READ, tool


@tool(READ)
def pull_tables(spreadsheet: str, tab: str | None = None, range: str | None = None,  # noqa: A002
                include_hidden: bool = False) -> ToolResult:
    """Pull tables for local SQL: each tab's CSV export behind a signed link.

    For `gsheet pull` (the CLI loads them into DuckDB). The server never
    parses the rows; each tab's whole CSV, from A1, is copied into storage and
    linked, with the scan's regions on it as A1 ranges to cut it by. Table
    regions and native Tables are pulled; a communication block only when
    named with `range`.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      tab: only this tab's tables.
      range: one region to pull, by alias (`tab#2`) or `'tab'!A1:F40`, any kind.
      include_hidden: also pull tables on hidden tabs.

    Returns:
      Per tab: size, CSV size, a fingerprint for table_status, the CSV link
      (valid 15 minutes), then one line per region (range, kind, header rows).
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        return reply.text(pull.pull(spreadsheet_id, tab=tab, range_=range,
                                    include_hidden=include_hidden))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot pull from spreadsheet {spreadsheet_id}: {e}") from e

"""`scan_sheet`: the landscape of a whole spreadsheet in one call."""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import reply, scan, sheets_api
from ..app import READ, tool


@tool(READ)
def scan_sheet(spreadsheet: str, tab: str | None = None,
               include_hidden: bool = False) -> ToolResult:
    """Map a spreadsheet before reading it: every tab, its regions, images and comments.

    Start here. One call covers the whole workbook in about 2k tokens. Per
    tab: size, frozen rows/columns (`frozen 2/1`), hidden rows and columns,
    charts, the basic filter and filter views, floating images with the
    cells they cover, comment threads placed on their cells, and its regions.

    Regions are found by flood fill over filled cells; blocks up to 3 blank
    rows apart with matching columns join. Native Tables and named ranges
    are exact. A table region lists its columns, per-column types (`mixed`
    with shares), total rows and footnotes; any other block is one line with
    its first text and counts of merges, images and checkboxes. Tabs over
    500k cells are read head (2,000 rows) and tail (500 rows) only and marked
    `extent estimated`. At most 20 regions per tab; `tab=` lists one in full.

    Each region gets an alias `<tab>#<n>` that other tools accept for 10
    minutes, while the scan is cached.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      tab: one tab's name, to list all its regions.
      include_hidden: also scan the regions of hidden tabs (always listed).

    Returns:
      Text: spreadsheet line, named ranges, comment counts, then one block
      per tab with `#n` region lines and loose cells.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        return reply.text(scan.scan(spreadsheet_id, tab=tab, include_hidden=include_hidden))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot scan spreadsheet {spreadsheet_id}: {e}") from e

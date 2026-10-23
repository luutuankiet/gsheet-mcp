"""`query_range`: live filter, pick, sort and count on one table region."""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import addressing, notation, query, reply, sheets_api
from ..app import READ, tool


@tool(READ)
def query_range(spreadsheet: str, range: str,  # noqa: A002 - the spec's word
                where: list[str] | None = None, columns: list[str] | None = None,
                sort: str | None = None, count: bool = False,
                include_hidden: bool = False,
                max_chars: int = notation.MAX_CELL_CHARS) -> ToolResult:
    """Answer a small question about one table region, live: filter rows,
    pick columns, sort, count. Anything heavier: pull_tables and the CLI.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      range: one table region: its scan alias (`orders#1`), its A1 range, or
        a whole tab holding a single table region.
      where: conditions, all of which a row must meet, each
        `<column> <op> <value>`: `qty > 50`, `status = done`,
        `item contains lorem`, `id ~ ^ORD-10` (regex, any case),
        `remark is empty`, `remark is not empty`. Columns are header names
        (any case) or column letters. Numbers and ISO dates (`2026-01-31`)
        compare by value; anything else compares as text, ignoring case.
      columns: the columns to show, by header name or letter; all by default.
      sort: column names, comma-separated; `-price` or `price desc` sorts
        descending. Empty cells sort last.
      count: only the count line, no table.
      include_hidden: also query rows hidden by the user or a filter.
      max_chars: per-cell cap (500); 0 lifts it.

    Returns:
      A line `'<tab>'!A1:F31 · Table Orders · 12 of 30 rows match qty > 50 ·
      sorted by price`, lines on rows left out (hidden, totals, read budget),
      then a Markdown table with the real row number in a `row` column, cut
      at the read budget (about 8k tokens).
    """
    if max_chars < 0:
        raise reply.Refusal("max_chars is 0 (no cap) or more")
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    rng = addressing.resolve(spreadsheet_id, range)
    try:
        return reply.text(query.query(spreadsheet_id, rng, where=where or [], columns=columns,
                                      sort=sort, count=count, include_hidden=include_hidden,
                                      max_chars=max_chars))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot query {rng}: {e}") from None

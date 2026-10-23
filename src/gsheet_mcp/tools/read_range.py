"""`read_range`: a range as text, one region at a time."""
from __future__ import annotations

from typing import Literal

from fastmcp.tools import ToolResult

from .. import addressing, notation, reader, reply, sheets_api
from ..app import READ, tool


@tool(READ)
def read_range(spreadsheet: str, range: str | None = None,  # noqa: A002 - the spec's word
               detail: Literal["default", "formats", "formulas"] = "default",
               comments: bool = False, include_hidden: bool = False,
               max_chars: int = notation.MAX_CELL_CHARS,
               cursor: str | None = None) -> ToolResult:
    """Read a range's content, structure and formatting as text, one region at a time.

    Communication blocks are cell-addressed lines under `── row N`; a merge
    shows on its top-left cell as its range (`B1:F1 {s1}: Weekly status`).
    Table regions are Markdown tables with the real row number in a `row`
    column. Cells reference a `styles:` legend (`{s1}`: fill, text colour,
    bold, centred) and a dropdown legend (`On track ▾v1`, `!` when outside
    the list). Rich text is inline Markdown; checkboxes `[x]`/`[ ]`; ratings
    `★★★☆☆ (3)`; chips `@[name](url)`; floating images
    `H3 [image 96x40 over H3:I4, floating]`. Formulas follow the value
    (`G12: 42 ← =SUM(G2:G11)`); notes sit indented under their cell. Hidden
    rows and columns are always announced and their content skipped.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      range: a tab plus an A1 range (`'table for comms'!B2:F8`), a whole tab
        (`'comms'`), or a scan alias (`comms#1`). Left out, the first tab.
      detail: `formats` adds number formats, borders, column widths, row
        heights and conditional formats; `formulas` shows formulas inside
        tables too.
      comments: also show comment threads under their cells.
      include_hidden: show the content of hidden rows and columns.
      max_chars: per-cell cap (500); 0 lifts it.
      cursor: the `next:` cell from a previous page of the same read.

    Returns:
      A header `<range> · cells 1-400 of 1,212 (~9k tokens) · next: B240`
      (about 8k tokens a page, cut between rows), hidden-row lines, legends,
      the regions, and a last line pointing at render_range when charts,
      floating images or fills are in the range.
    """
    if detail not in ("default", "formats", "formulas"):
        raise reply.Refusal(f'detail is "default", "formats" or "formulas", not {detail!r}')
    if max_chars < 0:
        raise reply.Refusal("max_chars is 0 (no cap) or more")
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    rng = addressing.resolve(spreadsheet_id, range)
    try:
        return reply.text(reader.read(spreadsheet_id, rng, detail=detail, comments=comments,
                                      include_hidden=include_hidden, max_chars=max_chars,
                                      cursor=cursor))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot read {rng}: {e}") from None

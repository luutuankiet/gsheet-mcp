"""`write_cells`: so far only lands an uploaded CSV, behind a plan.

Value, formula, rich text and format writes come with the writes build; this
module holds the landing path they will sit beside.
"""
from __future__ import annotations

from fastmcp.tools import ToolResult

from .. import landing, reply, sheets_api
from ..app import EDIT, tool


@tool(EDIT)
def write_cells(spreadsheet: str, range: str | None = None,  # noqa: A002
                upload: str | None = None, plan_id: str | None = None) -> ToolResult:
    """Land a CSV from upload_slot into an empty range, through a plan.

    With `upload` and `range` (the top-left cell, an alias, or a range the
    exact size of the CSV): a dry run that checks the range is empty and
    returns a `plan_id`, valid 1 hour, single use. With `plan_id`: re-reads
    the range and writes only if it is still empty; otherwise nothing is
    written. The guard narrows the race to about one round trip; it is not a
    lock. Numbers land as numbers, everything else as text; styling is kept.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      range: where the CSV lands.
      upload: the handle upload_slot returned.
      plan_id: a plan from an earlier call, to apply.

    Returns:
      The plan with a preview, or the receipt: the cells written (up to 50).
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        if plan_id:
            return reply.text(landing.apply(spreadsheet_id, plan_id))
        if upload and range:
            return reply.text(landing.plan(spreadsheet_id, range, upload))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot write to spreadsheet {spreadsheet_id}: {e}") from e
    raise reply.Refusal("write_cells lands an upload so far: pass upload and range for a "
                        "plan, or plan_id to apply one.")

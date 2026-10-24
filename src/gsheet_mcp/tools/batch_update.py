"""batch_update: raw Sheets `batchUpdate` requests, the escape hatch, always behind a plan."""
from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from .. import reply, sheet_script, sheets_api
from ..app import EDIT, tool
from ..sandbox import process

TOOL = "batch_update"


@tool(EDIT)
def batch_update(spreadsheet: str, requests: Any = None, dry_run: bool = True,
                 plan_id: str | None = None) -> ToolResult:
    """Send raw Sheets `batchUpdate` requests: the escape hatch for anything
    write_cells and the typed tools do not cover (charts, pivots, conditional
    formats). Prefer the typed tools; raw requests skip their checks.

    Always behind a plan: the dry run (default) names the affected ranges and
    returns a `plan_id` (valid 1 hour, single use); call again with `plan_id`
    and dry_run=false to apply. Applying re-reads the affected cells and tab
    sizes and refuses the whole batch if any changed. The guard narrows the
    race to about one round trip; it is not a lock.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      requests: a list of Sheets API requests, e.g. `[{"addChart": {...}}]`.
      dry_run: true (default) plans; false applies `plan_id`.
      plan_id: a plan from an earlier dry run, applied exactly as previewed.

    Returns:
      The plan with the affected ranges, or the receipt: the affected ranges
      and each request's reply.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        if plan_id is not None:
            if dry_run:
                raise reply.Refusal("plan_id applies an earlier dry run: call again with "
                                    "dry_run=false.")
            if requests is not None:
                raise reply.Refusal("Pass either requests or plan_id, not both.")
            payload = sheet_script.load(spreadsheet_id, plan_id, TOOL)
            return reply.text(sheet_script.apply(spreadsheet_id, plan_id, payload))
        if not dry_run:
            raise reply.Refusal("batch_update always needs a plan: call it with dry_run=true "
                                "for a plan_id, then apply that plan_id with dry_run=false.")
        requests = process.parse_input(requests)
        if isinstance(requests, dict):
            requests = [requests]
        if not isinstance(requests, list) or not requests or not all(
                isinstance(r, dict) and len(r) == 1 for r in requests):
            raise reply.Refusal("requests must be a non-empty list of Sheets requests, each "
                                "one object with one key such as addChart.")
        return reply.text(sheet_script.plan(spreadsheet_id, TOOL, [], requests, []))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot write to spreadsheet {spreadsheet_id}: {e}") from e

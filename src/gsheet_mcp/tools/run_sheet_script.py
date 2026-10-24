# Copied from google-docs-mcp@2fb4652 src/<package>/tools/run_doc_script.py; the doc writers replaced by sheet.*, always behind a plan.
"""run_sheet_script: loops and logic over a spreadsheet in a sandboxed script."""
from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from .. import reply, sheet_script, sheets_api
from ..app import EDIT, tool
from ..sandbox import process

TOOL = "run_sheet_script"


def _tail(logs: list[str], result: str | None) -> list[str]:
    out = []
    if logs:
        out.append("logs:\n" + "\n".join(f"  {x}" for x in logs))
    if result is not None and result != "null":
        out.append(f"return: {result}")
    return out


@tool(EDIT)
def run_sheet_script(spreadsheet: str, script: str | None = None, input: Any = None,  # noqa: A002
                     reads: list[str] | None = None, dry_run: bool = True,
                     plan_id: str | None = None) -> ToolResult:
    """Run a short JavaScript program for loops and logic over a spreadsheet.

    The script runs in a sandbox with no network, filesystem, modules or
    timers, and sees `input` and a small `sheet` API that only records
    operations; nothing is sent while it runs:
      sheet.read(range)            rows of values (unformatted) for an A1
                                   range or scan alias read before the run
      sheet.write(range, values, format?)  the same write write_cells makes
      sheet.request(request)       one raw Sheets batchUpdate request
    `sheet.read` with a string literal is read for you; for computed ranges
    list them in `reads`. Example:
      for (let i = 0; i < 10; i++) sheet.write(`comms!C${i + 3}`, i * 2)

    Always behind a plan: the dry run (default) returns the diff and a
    `plan_id` (valid 1 hour, single use); call again with `plan_id` and
    dry_run=false to apply. Applying re-reads the touched cells and refuses
    the whole batch if any changed. The guard narrows the race to about one
    round trip; it is not a lock.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      script: the JavaScript body; `return` a value to see it in the reply.
      input: any JSON value, available to the script as `input`.
      reads: extra ranges or aliases `sheet.read` may ask for, at most 20.
      dry_run: true (default) plans; false applies `plan_id`.
      plan_id: a plan from an earlier dry run, applied exactly as previewed.

    Returns:
      The plan (writes, raw requests, diff, warnings, logs, return value,
      plan_id), or the receipt: written cells re-read and the ranges the raw
      requests affected.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        if plan_id is not None:
            if dry_run:
                raise reply.Refusal("plan_id applies an earlier dry run: call again with "
                                    "dry_run=false. To preview again, send the script instead.")
            if script is not None or input is not None:
                raise reply.Refusal("Pass either a script or plan_id, not both.")
            payload = sheet_script.load(spreadsheet_id, plan_id, TOOL)
            return reply.text(sheet_script.apply(spreadsheet_id, plan_id, payload))
        if not dry_run:
            raise reply.Refusal("run_sheet_script always goes through a plan: run it as a dry "
                                "run, then apply its plan_id with dry_run=false.")
        if not script or not script.strip():
            raise reply.Refusal("run_sheet_script needs a script (or a plan_id from a dry run).")
        outcome = sheet_script.run(spreadsheet_id, script, process.parse_input(input), reads)
        return reply.text(sheet_script.plan(spreadsheet_id, TOOL, outcome.writes,
                                            outcome.requests,
                                            _tail(outcome.logs, outcome.result)))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot write to spreadsheet {spreadsheet_id}: {e}") from e

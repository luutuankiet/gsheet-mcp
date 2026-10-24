"""`edit_sheet`: typed structural operations in one atomic `batchUpdate`,
always behind a plan and the structural guard."""
from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from .. import reply, sheets_api, structure, writes
from ..app import EDIT, tool


@tool(EDIT)
def edit_sheet(spreadsheet: str, operations: list[dict[str, Any]] | None = None,
               plan_id: str | None = None) -> ToolResult:
    """Change a sheet's structure: typed operations in one atomic batchUpdate.

    Every call is planned first: pass `operations` for a dry run that lists
    the requests and returns a `plan_id` (valid 1 hour, single use), then call
    again with `plan_id` alone to apply. Applying re-reads the touched cells,
    the values in the affected rows and each tab's row and column counts, and
    refuses the whole batch if any changed. The guard narrows the race to
    about one round trip; it is not a lock.

    Operations, each a dict with `op` (rows are `'tab'!5:6`, columns `'tab'!B:C`):
      freeze {tab, rows?, columns?} · hide / unhide {range} or {tab}
      merge {range, type?} (refused across a frozen edge) · unmerge {range}
      insert / delete {range of rows or columns} · move {range, to: row number or column letter}
      add_tab {title, rows?, columns?} · rename_tab {tab, title} · duplicate_tab {tab, title?}
      delete_tab {tab} · sort {range, by: [{column: "B", order: "asc"|"desc"}]}
      set_filter {range} · clear_filter {tab}
      add_filter_view {range, title} · update_filter_view {id, range?, title?}
      delete_filter_view {id} · checkbox {range} · dropdown {range, values, strict?}
      clear_validation {range} · append_rows {table, rows: [[...]]} (native Table)
      note {range, note} · clear_note {range} (a note-only mask)
      comment {text} (unanchored, through Drive) · reply {comment_id, text}
      resolve {comment_id, text?}

    Args:
      spreadsheet: the spreadsheet's URL or id.
      operations: the operations, applied in order in one batch.
      plan_id: a plan from an earlier call, to apply.

    Returns:
      The plan (requests, plan_id), or what was applied, with Google's replies.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        if plan_id:
            payload = writes.load(spreadsheet_id, plan_id)
            if payload.get("op") != structure.OP:
                raise reply.Refusal(f"Plan {plan_id} is not an edit_sheet plan; apply it "
                                    "with the tool that made it.")
            return reply.text(structure.apply(spreadsheet_id, plan_id, payload))
        return reply.text(structure.plan(spreadsheet_id, list(operations or [])))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot edit spreadsheet {spreadsheet_id}: {e}") from e

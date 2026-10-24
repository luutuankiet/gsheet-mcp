"""`write_cells`: values, formulas, Markdown rich text and format patches over
one or more ranges, behind the stale-write guard; also lands an uploaded CSV."""
from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from .. import landing, reply, sheets_api, writes
from ..app import EDIT, tool


@tool(EDIT)
def write_cells(spreadsheet: str, range: str | None = None,  # noqa: A002 - the spec's word
                values: Any = None, format: dict[str, Any] | None = None,  # noqa: A002
                writes_: list[dict[str, Any]] | None = None, dry_run: bool = False,
                plan_id: str | None = None, upload: str | None = None) -> ToolResult:
    """Write values, formulas, rich text and format patches; styling is kept.

    Values go in through `updateCells` with a value-only mask, so fills,
    fonts and borders stay. Types come from JSON: a string is text (inline
    Markdown `**bold**`, `*italic*`, `~~strike~~`, `[text](url)` becomes rich
    text), a number is a number, true/false is a checkbox state, a string
    starting with `=` is a formula, and an ISO date (`2026-03-01`) in a cell
    already formatted as a date becomes a date. `null` leaves a cell as it is;
    `""` clears its value. Refused: a covered cell of a merge, anything but
    true/false in a checkbox, a value outside a strict dropdown. Warned: plain
    text over rich text (its runs are dropped), a value over a formula.

    Over 200 cells needs a plan: call with `dry_run=true` for a diff and a
    `plan_id` (valid 1 hour, single use), then call with `plan_id` alone.
    Applying re-reads the cells and refuses the whole batch if any changed,
    listing `B4: "old" → "new"`. The guard narrows the race to about one
    round trip; it is not a lock.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      range: a cell (the top-left of `values`), a range the size of `values`,
        or a scan alias. With `upload`: where the CSV lands.
      values: one value, a row `[...]`, or rows `[[...], ...]`.
      format: a `userEnteredFormat` patch naming only its own fields,
        e.g. `{"textFormat": {"bold": true}}`; `null` clears a field.
      writes_: several writes at once, each `{"range", "values", "format"}`,
        applied in one atomic batch.
      dry_run: plan instead of writing.
      plan_id: a plan from an earlier dry run (or upload), to apply.
      upload: the handle upload_slot returned; lands its CSV in an empty
        range, always through a plan.

    Returns:
      The plan (diff, warnings, plan_id), or the receipt: the touched cells
      re-read in read_range's notation with its style legend, up to 50.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    try:
        if plan_id:
            payload = writes.load(spreadsheet_id, plan_id)
            if payload.get("op") == landing.OP:
                return reply.text(landing.apply(spreadsheet_id, plan_id, payload))
            if payload.get("op") != writes.OP:
                raise reply.Refusal(f"Plan {plan_id} is a {payload.get('op')} plan; apply it "
                                    "with that tool.")
            return reply.text(writes.apply(spreadsheet_id, plan_id, payload))
        if upload:
            if not range:
                raise reply.Refusal("An upload needs a range to land in.")
            return reply.text(landing.plan(spreadsheet_id, range, upload))
        batch = list(writes_ or [])
        if range is not None or values is not None or format is not None:
            batch.insert(0, {"range": range, "values": values, "format": format})
        if dry_run:
            return reply.text(writes.plan(spreadsheet_id, batch))
        return reply.text(writes.run(spreadsheet_id, batch))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot write to spreadsheet {spreadsheet_id}: {e}") from e

"""Landing a CLI query result: a CSV from an upload slot into an empty range,
always behind a plan (spec, "Tables to DuckDB" and "Plans").

    plan(spreadsheet_id, target, upload) -> reply text with a plan_id
    apply(spreadsheet_id, plan_id, payload) -> receipt text

The plan checks the target is empty and stores its fingerprint; applying
re-reads it through the shared stale-write guard and refuses the whole write
when anything changed there since. Values go in through
`updateCells` with the `userEnteredValue` mask only, so styling stays.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from . import a1, addressing, auth, guard, reply, sheets_api, state_store, transfer, writes

MAX_LISTED = 20          # changed or filled cells named in a refusal
RECEIPT_CELLS = 50
PREVIEW_ROWS = 3
# Implementation default: a CSV field that is a plain decimal number lands as
# a number; everything else, a leading `=` included, lands as text.
_NUMBER = re.compile(r"-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
OP = "land_upload"


def _caller() -> str | None:
    try:
        return auth.caller_id()
    except auth.NotSignedInError:
        return None


def _rows(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise reply.Refusal(f"The upload is not UTF-8 CSV: {e}") from None
    rows = list(csv.reader(io.StringIO(text)))
    width = max((len(r) for r in rows), default=0)
    if not width:
        raise reply.Refusal("The upload holds no rows; nothing to land.")
    return [r + [""] * (width - len(r)) for r in rows]


def _cell(value: str) -> dict[str, Any]:
    if value == "":
        return {}
    if _NUMBER.fullmatch(value):
        number = float(value)
        return {"userEnteredValue": {"numberValue": int(number) if number.is_integer()
                                     and "." not in value and "e" not in value.lower()
                                     else number}}
    return {"userEnteredValue": {"stringValue": value}}


def _cells(rng: a1.Range, rows: list[list[Any]]) -> list[tuple[str, Any]]:
    """Each non-blank cell of `rows` read at `rng`, as (A1 name, value)."""
    return [(a1.Range(None, rng.start_row + r, rng.start_row + r + 1,
                      rng.start_col + c, rng.start_col + c + 1).a1, v)
            for r, row in enumerate(rows) for c, v in enumerate(row) if v != ""]


def _target(spreadsheet_id: str, target: str, height: int, width: int
            ) -> tuple[a1.Range, dict[str, Any]]:
    rng = addressing.resolve(spreadsheet_id, target)
    props = sheets_api.find_tab(sheets_api.get_tabs(spreadsheet_id), rng.tab)
    r0, c0 = rng.start_row or 0, rng.start_col or 0
    landed = a1.Range(props["title"], r0, r0 + height, c0, c0 + width)
    given_h = None if rng.end_row is None else rng.end_row - r0
    given_w = None if rng.end_col is None else rng.end_col - c0
    # A single cell or a whole tab is an anchor; a sized range must match the result.
    if not (given_h == given_w == 1 or rng.whole_tab) and (
            (given_h is not None and given_h != height) or (given_w is not None and given_w != width)):
        raise reply.Refusal(f"The upload is {height} rows x {width} columns but {target} is "
                            f"not; pass its top-left cell instead.")
    grid = props.get("gridProperties", {})
    if r0 + height > grid.get("rowCount", 0) or c0 + width > grid.get("columnCount", 0):
        raise reply.Refusal(
            f"{landed} does not fit: tab \"{props['title']}\" is "
            f"{grid.get('rowCount', 0)}x{grid.get('columnCount', 0)}. Land it lower in a "
            f"bigger tab, or grow the tab first.")
    return landed, props


def _filled(spreadsheet_id: str, rng: a1.Range) -> list[tuple[str, Any]]:
    return _cells(rng, sheets_api.values_get(spreadsheet_id, rng))


def _fingerprint(spreadsheet_id: str, landed: a1.Range) -> dict[str, list[str]]:
    _, cells = guard.read(spreadsheet_id, [landed])
    touched = [guard.handle(landed.tab or "", r, c)
               for r in range(landed.start_row, landed.end_row)
               for c in range(landed.start_col, landed.end_col)]
    return guard.fingerprint(cells, touched)


def plan(spreadsheet_id: str, target: str, upload: str) -> str:
    handle, data = transfer.read_upload(upload)
    rows = _rows(data)
    landed, props = _target(spreadsheet_id, target, len(rows), len(rows[0]))
    filled = _filled(spreadsheet_id, landed)
    if filled:
        listed = ", ".join(f"{name} {str(v)!r}" for name, v in filled[:MAX_LISTED])
        more = f" (+{len(filled) - MAX_LISTED} more)" if len(filled) > MAX_LISTED else ""
        raise reply.Refusal(f"{landed} is not empty, and an upload lands only in an empty "
                            f"range or a new tab: {listed}{more}")
    plan_id = state_store.current().save_plan(
        {"op": OP, "handle": handle, "range": str(landed), "sheet_id": props["sheetId"],
         "fingerprint": _fingerprint(spreadsheet_id, landed)},
        spreadsheet_id=spreadsheet_id, caller=_caller())
    preview = "\n".join("  " + " | ".join(r) for r in rows[:PREVIEW_ROWS])
    return (f"plan {plan_id} (valid 1 hour, single use): lands {len(rows)} rows x "
            f"{len(rows[0])} columns from {handle} into {landed}, empty now\n"
            f"first rows:\n{preview}\n"
            f"apply: write_cells with plan_id=\"{plan_id}\"")


def apply(spreadsheet_id: str, plan_id: str, payload: dict[str, Any]) -> str:
    """Apply a landing plan whose payload `write_cells` already loaded."""
    store = state_store.current()
    store.expire_plan(plan_id)                 # single use, applied or refused
    handle, data = transfer.read_upload(payload["handle"])
    rows = _rows(data)
    landed = a1.parse(payload["range"])
    guard.check(payload.get("fingerprint") or {}, _fingerprint(spreadsheet_id, landed),
                str(landed))
    sheets_api.batch_update(spreadsheet_id, [{"updateCells": {
        "start": {"sheetId": payload["sheet_id"], "rowIndex": landed.start_row,
                  "columnIndex": landed.start_col},
        "rows": [{"values": [_cell(v) for v in row]} for row in rows],
        "fields": "userEnteredValue"}}])
    store.delete_object(handle)
    writes.after_write(spreadsheet_id)
    written = _filled(spreadsheet_id, landed)
    shown = "\n".join(f"  {name} {str(v)!r}" for name, v in written[:RECEIPT_CELLS])
    more = (f"\n  +{len(written) - RECEIPT_CELLS} more cells" if len(written) > RECEIPT_CELLS
            else "")
    return f"landed {len(rows)} rows x {len(rows[0])} columns into {landed}\n{shown}{more}"

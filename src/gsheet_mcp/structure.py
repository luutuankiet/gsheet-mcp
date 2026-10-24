"""`edit_sheet`'s engine (spec, "Layers" and "Plans and the stale-write guard"):
typed structural operations compiled into one atomic `batchUpdate`, always
behind a plan and the structural guard.

    plan(spreadsheet_id, operations)        -> dry run: the requests, a plan_id
    apply(spreadsheet_id, plan_id, payload) -> re-read, compare, send, receipt
    compile_ops(book, operations)           -> (requests, drive calls, guard ranges)

The structural guard fingerprints the cells an operation touches, the values
in the rows it affects, and every touched tab's row and column counts.
Comments go through Drive after the batch, because Sheets has no comment API.
"""
from __future__ import annotations

import json
from typing import Any

from . import a1, guard, reply, sheets_api, state_store, writes

OP = "edit_sheet"
BOOK_FIELDS = ("spreadsheetId,sheets(properties(sheetId,title,index,hidden,gridProperties),"
               "merges,tables(tableId,name,range),filterViews(filterViewId,title,range),"
               "basicFilter(range))")
GUARD_CELLS = 5000          # the guard reads at most this many cells (default, review)


def _tab(book: dict[str, Any], title: str | None) -> dict[str, Any]:
    for sheet in book.get("sheets") or []:
        if title is None or sheet["properties"]["title"] == title:
            return sheet
    raise reply.Refusal(f"No tab {title!r} in this spreadsheet.")


def _range(book: dict[str, Any], text: str | None) -> tuple[a1.Range, dict[str, Any]]:
    if not text:
        raise reply.Refusal("This operation needs a range.")
    rng = a1.parse(text)
    sheet = _tab(book, rng.tab)
    return rng.with_tab(sheet["properties"]["title"]), sheet


def _dimension(rng: a1.Range) -> tuple[str, int, int]:
    """ROWS or COLUMNS, start, end, for a whole-row (`5:6`) or whole-column (`B:C`) range."""
    if rng.start_col is None and rng.end_col is None and rng.start_row is not None:
        return "ROWS", rng.start_row, rng.end_row or rng.start_row + 1
    if rng.start_row is None and rng.end_row is None and rng.start_col is not None:
        return "COLUMNS", rng.start_col, rng.end_col or rng.start_col + 1
    raise reply.Refusal(f"{rng} must be whole rows (like 5:6) or whole columns (like B:C).")


def _dim_range(sheet_id: int, dim: str, start: int, end: int) -> dict[str, Any]:
    return {"sheetId": sheet_id, "dimension": dim, "startIndex": start, "endIndex": end}


def _rows_of(rng: a1.Range) -> a1.Range:
    """The whole rows a range sits on: the guard reads their values."""
    return a1.Range(rng.tab, rng.start_row, rng.end_row, None, None)


def _cell_data(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, int | float):
        return {"userEnteredValue": {"numberValue": value}}
    text = str(value)
    kind = "formulaValue" if text.startswith("=") else "stringValue"
    return {"userEnteredValue": {kind: text}}


def _refuse_frozen_merge(rng: a1.Range, sheet: dict[str, Any]) -> None:
    grid = sheet["properties"].get("gridProperties") or {}
    cols, rows = grid.get("frozenColumnCount", 0), grid.get("frozenRowCount", 0)
    c0, c1 = rng.start_col or 0, rng.end_col if rng.end_col is not None else grid["columnCount"]
    r0, r1 = rng.start_row or 0, rng.end_row if rng.end_row is not None else grid["rowCount"]
    if cols and c0 < cols < c1:
        raise reply.Refusal(f"Nothing was sent: merging {rng} would cross the frozen column "
                            f"edge after column {a1.column_letters(cols - 1)}; Sheets refuses "
                            "a merge across frozen and unfrozen columns. Unfreeze first, or "
                            "merge on one side.")
    if rows and r0 < rows < r1:
        raise reply.Refusal(f"Nothing was sent: merging {rng} would cross the frozen row "
                            f"edge after row {rows}. Unfreeze first, or merge on one side.")


def compile_ops(book: dict[str, Any], operations: list[dict[str, Any]]
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[a1.Range], set[str]]:
    """Operations -> (batchUpdate requests, Drive calls, guarded ranges, guarded tabs)."""
    requests: list[dict[str, Any]] = []
    drive: list[dict[str, Any]] = []
    ranges: list[a1.Range] = []
    tabs: set[str] = set()
    for i, op in enumerate(operations):
        kind = op.get("op")
        try:
            _compile_one(book, op, kind, requests, drive, ranges, tabs)
        except (a1.A1Error, KeyError) as e:
            raise reply.Refusal(f"operation {i + 1} ({kind}): {e}") from None
    return requests, drive, ranges, tabs


def _compile_one(book, op, kind, requests, drive, ranges, tabs) -> None:  # noqa: C901, PLR0912, PLR0915
    def tab_of(key: str = "tab") -> dict[str, Any]:
        sheet = _tab(book, op[key])
        tabs.add(sheet["properties"]["title"])
        return sheet

    if kind == "freeze":
        sheet = tab_of()
        grid, mask = {}, []
        for key, field in (("rows", "frozenRowCount"), ("columns", "frozenColumnCount")):
            if key in op:
                grid[field] = int(op[key])
                mask.append(f"gridProperties.{field}")
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet["properties"]["sheetId"], "gridProperties": grid},
            "fields": ",".join(mask)}})
    elif kind in ("hide", "unhide"):
        hidden = kind == "hide"
        if "range" not in op:
            sheet = tab_of()
            requests.append({"updateSheetProperties": {
                "properties": {"sheetId": sheet["properties"]["sheetId"], "hidden": hidden},
                "fields": "hidden"}})
            return
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        dim, start, end = _dimension(rng)
        requests.append({"updateDimensionProperties": {
            "range": _dim_range(sheet["properties"]["sheetId"], dim, start, end),
            "properties": {"hiddenByUser": hidden}, "fields": "hiddenByUser"}})
    elif kind in ("merge", "unmerge"):
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        ranges.append(rng)
        grid = rng.to_grid(sheet["properties"]["sheetId"])
        if kind == "merge":
            _refuse_frozen_merge(rng, sheet)
            requests.append({"mergeCells": {"range": grid,
                                            "mergeType": op.get("type", "MERGE_ALL")}})
        else:
            requests.append({"unmergeCells": {"range": grid}})
    elif kind in ("insert", "delete"):
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        dim, start, end = _dimension(rng)
        sid = sheet["properties"]["sheetId"]
        if kind == "insert":
            requests.append({"insertDimension": {"range": _dim_range(sid, dim, start, end),
                                                 "inheritFromBefore": start > 0}})
        else:
            requests.append({"deleteDimension": {"range": _dim_range(sid, dim, start, end)}})
        ranges.append(rng)
    elif kind == "move":
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        dim, start, end = _dimension(rng)
        to = op["to"]
        # `to` is a row number (1-based) for rows, a column letter for columns
        dest = int(to) - 1 if dim == "ROWS" else a1.column_index(str(to))
        requests.append({"moveDimension": {
            "source": _dim_range(sheet["properties"]["sheetId"], dim, start, end),
            "destinationIndex": dest}})
        ranges.append(rng)
    elif kind == "add_tab":
        props: dict[str, Any] = {"title": op["title"]}
        if "rows" in op or "columns" in op:
            props["gridProperties"] = {"rowCount": int(op.get("rows", 1000)),
                                       "columnCount": int(op.get("columns", 26))}
        requests.append({"addSheet": {"properties": props}})
    elif kind == "rename_tab":
        sheet = tab_of()
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet["properties"]["sheetId"], "title": op["title"]},
            "fields": "title"}})
    elif kind == "duplicate_tab":
        sheet = tab_of()
        body = {"sourceSheetId": sheet["properties"]["sheetId"],
                "insertSheetIndex": sheet["properties"].get("index", 0) + 1}
        if op.get("title"):
            body["newSheetName"] = op["title"]
        requests.append({"duplicateSheet": body})
    elif kind == "delete_tab":
        sheet = tab_of()
        requests.append({"deleteSheet": {"sheetId": sheet["properties"]["sheetId"]}})
    elif kind == "sort":
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        specs = [{"dimensionIndex": a1.column_index(str(s["column"])),
                  "sortOrder": "DESCENDING" if str(s.get("order", "asc")).lower()
                  .startswith("desc") else "ASCENDING"} for s in op["by"]]
        requests.append({"sortRange": {"range": rng.to_grid(sheet["properties"]["sheetId"]),
                                       "sortSpecs": specs}})
        ranges.append(_rows_of(rng))
    elif kind == "set_filter":
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        requests.append({"setBasicFilter": {"filter": {
            "range": rng.to_grid(sheet["properties"]["sheetId"])}}})
    elif kind == "clear_filter":
        requests.append({"clearBasicFilter": {"sheetId": tab_of()["properties"]["sheetId"]}})
    elif kind in ("add_filter_view", "update_filter_view"):
        view: dict[str, Any] = {}
        mask = []
        if op.get("range"):
            rng, sheet = _range(book, op["range"])
            tabs.add(rng.tab)
            view["range"] = rng.to_grid(sheet["properties"]["sheetId"])
            mask.append("range")
        if op.get("title"):
            view["title"] = op["title"]
            mask.append("title")
        if kind == "add_filter_view":
            requests.append({"addFilterView": {"filter": view}})
        else:
            view["filterViewId"] = int(op["id"])
            requests.append({"updateFilterView": {"filter": view, "fields": ",".join(mask)}})
    elif kind == "delete_filter_view":
        requests.append({"deleteFilterView": {"filterId": int(op["id"])}})
    elif kind in ("checkbox", "dropdown", "clear_validation"):
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        ranges.append(rng)
        body: dict[str, Any] = {"range": rng.to_grid(sheet["properties"]["sheetId"])}
        if kind == "checkbox":
            body["rule"] = {"condition": {"type": "BOOLEAN"}}
        elif kind == "dropdown":
            body["rule"] = {"condition": {"type": "ONE_OF_LIST", "values": [
                {"userEnteredValue": str(v)} for v in op["values"]]},
                "strict": bool(op.get("strict", True)), "showCustomUi": True}
        requests.append({"setDataValidation": body})
    elif kind == "append_rows":
        name = op["table"]
        for sheet in book.get("sheets") or []:
            table = next((t for t in sheet.get("tables") or [] if t.get("name") == name), None)
            if table:
                break
        else:
            raise reply.Refusal(f"No native Table named {name!r} in this spreadsheet.")
        title = sheet["properties"]["title"]
        tabs.add(title)
        ranges.append(a1.Range.from_grid(table["range"], title))
        requests.append({"appendCells": {
            "sheetId": sheet["properties"]["sheetId"], "tableId": table["tableId"],
            "rows": [{"values": [_cell_data(v) for v in row]} for row in op["rows"]],
            "fields": "userEnteredValue"}})
    elif kind in ("note", "clear_note"):
        rng, sheet = _range(book, op["range"])
        tabs.add(rng.tab)
        ranges.append(rng)
        grid = sheet["properties"]["gridProperties"]
        note = op.get("note") if kind == "note" else None
        cell = {"note": note} if note else {}
        width = rng.width(grid["columnCount"])
        height = (rng.end_row or grid["rowCount"]) - (rng.start_row or 0)
        requests.append({"updateCells": {
            "start": {"sheetId": sheet["properties"]["sheetId"],
                      "rowIndex": rng.start_row or 0, "columnIndex": rng.start_col or 0},
            "rows": [{"values": [dict(cell) for _ in range(width)]} for _ in range(height)],
            "fields": "note"}})
    elif kind == "comment":
        drive.append({"call": "add", "content": op["text"]})
    elif kind in ("reply", "resolve"):
        drive.append({"call": kind, "comment_id": op["comment_id"],
                      "content": op.get("text", "")})
    else:
        raise reply.Refusal(f"Unknown operation {kind!r}. See edit_sheet's description.")


# ---- the structural guard ------------------------------------------------------


def _clip(book: dict[str, Any], ranges: list[a1.Range], budget: int) -> list[a1.Range]:
    """The guarded ranges cut to their tab's size, then to `budget` cells in all,
    in the order given and row by row, so the read asks Google only for cells
    the guard can keep (default, review)."""
    out: list[a1.Range] = []
    left = budget
    for rng in ranges:
        if left <= 0:
            break
        grid = _tab(book, rng.tab)["properties"]["gridProperties"]
        rows, cols = grid.get("rowCount", 0), grid.get("columnCount", 0)
        r0, c0 = rng.start_row or 0, rng.start_col or 0
        r1 = min(rows if rng.end_row is None else rng.end_row, rows)
        c1 = min(cols if rng.end_col is None else rng.end_col, cols)
        if r1 <= r0 or c1 <= c0:
            continue
        width = min(c1 - c0, left)
        height = min(r1 - r0, left // width)
        out.append(a1.Range(rng.tab, r0, r0 + height, c0, c0 + width))
        left -= height * width
    return out


def _fingerprint(spreadsheet_id: str, ranges: list[a1.Range], tabs: set[str]
                 ) -> dict[str, Any]:
    book = sheets_api.get_spreadsheet(spreadsheet_id, fields=BOOK_FIELDS,
                                      include_grid_data=False)
    sizes = {}
    for title in sorted(tabs):
        grid = _tab(book, title)["properties"]["gridProperties"]
        sizes[title] = [grid["rowCount"], grid["columnCount"]]
    cells: dict[str, list[str]] = {}
    clipped = _clip(book, ranges, GUARD_CELLS)
    if clipped:
        _, got = guard.read(spreadsheet_id, clipped)
        touched = sorted(got, key=guard._order)[:GUARD_CELLS]     # row, then column
        cells = guard.fingerprint(got, touched)
    return {"sizes": sizes, "cells": cells}


def _size_line(title: str, old: list[int], new: list[int] | None) -> str:
    """`'tab': R rows x C columns → R' rows x C' columns`, or `→ gone`."""
    after = f"{new[0]} rows x {new[1]} columns" if new else "gone"
    return f"  '{title}': {old[0]} rows x {old[1]} columns → {after}"


def _check(old: dict[str, Any], new: dict[str, Any]) -> None:
    moved = [_size_line(t, size, new["sizes"].get(t))
             for t, size in old["sizes"].items() if new["sizes"].get(t) != size]
    if moved:
        raise reply.Refusal("Nothing was written: the tab's shape changed since the plan.\n"
                            + "\n".join(moved) + "\nMake a new plan.")
    guard.check(old["cells"], new["cells"], "this edit", one_tab=False)


# ---- plan and apply --------------------------------------------------------------


def _book(spreadsheet_id: str) -> dict[str, Any]:
    return sheets_api.get_spreadsheet(spreadsheet_id, fields=BOOK_FIELDS,
                                      include_grid_data=False)


def _compile(spreadsheet_id: str, operations: list[dict[str, Any]],
             book: dict[str, Any] | None = None):
    if not operations:
        raise reply.Refusal("edit_sheet needs at least one operation.")
    return compile_ops(book or _book(spreadsheet_id), operations)


def _resized(before: dict[str, Any], sizes: dict[str, list[int]], after: dict[str, Any]
             ) -> list[str]:
    """Size lines for the guarded tabs whose counts the batch changed, found by
    sheetId so a renamed tab keeps its line; a deleted tab is `gone`."""
    now = {s["properties"]["sheetId"]: s["properties"] for s in after.get("sheets") or []}
    lines = []
    for title, size in sizes.items():
        props = now.get(_tab(before, title)["properties"]["sheetId"])
        grid = (props or {}).get("gridProperties") or {}
        new = [grid.get("rowCount"), grid.get("columnCount")] if props else None
        if new != size:
            lines.append(_size_line(props["title"] if props else title, size, new))
    return lines


def plan(spreadsheet_id: str, operations: list[dict[str, Any]]) -> str:
    requests, drive, ranges, tabs = _compile(spreadsheet_id, operations)
    fp = _fingerprint(spreadsheet_id, ranges, tabs)
    plan_id = state_store.current().save_plan(
        {"op": OP, "operations": operations, "fingerprint": fp},
        spreadsheet_id=spreadsheet_id, caller=writes._caller())
    lines = [f"plan {plan_id} (valid 1 hour, single use): {len(requests)} "
             f"{'request' if len(requests) == 1 else 'requests'} in one batchUpdate"
             + (f", {len(drive)} Drive comment calls" if drive else "") + ", nothing written yet"]
    lines += ["  " + json.dumps(r, ensure_ascii=False) for r in requests]
    lines += ["  drive: " + json.dumps(d, ensure_ascii=False) for d in drive]
    lines.append(f'apply: edit_sheet with plan_id="{plan_id}"; it re-reads the touched cells, '
                 "the affected rows and the tab sizes first and refuses the whole batch if any "
                 "changed")
    return "\n".join(lines)


def apply(spreadsheet_id: str, plan_id: str, payload: dict[str, Any]) -> str:
    state_store.current().expire_plan(plan_id)        # single use, applied or refused
    book = _book(spreadsheet_id)
    requests, drive, ranges, tabs = _compile(spreadsheet_id, payload["operations"], book)
    _check(payload["fingerprint"], _fingerprint(spreadsheet_id, ranges, tabs))
    lines = []
    if requests:
        result = sheets_api.batch_update(spreadsheet_id, requests)
        writes.after_write(spreadsheet_id)
        lines.append(f"applied {len(requests)} {'request' if len(requests) == 1 else 'requests'}"
                     " in one batchUpdate")
        lines += _resized(book, payload["fingerprint"]["sizes"], _book(spreadsheet_id))
        for req, rep in zip(requests, result.get("replies") or [], strict=False):
            if rep:
                lines.append(f"  {next(iter(req))}: {json.dumps(rep, ensure_ascii=False)}")
    for call in drive:
        if call["call"] == "add":
            got = sheets_api.add_comment(spreadsheet_id, call["content"])
            lines.append(f"comment {got.get('id')} added (unanchored)")
        else:
            got = sheets_api.reply_comment(spreadsheet_id, call["comment_id"], call["content"],
                                           resolve=call["call"] == "resolve")
            lines.append(f"{call['call']} on comment {call['comment_id']}: reply {got.get('id')}")
    return "\n".join(lines)

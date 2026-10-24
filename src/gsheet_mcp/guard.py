"""The stale-write guard (spec, "Plans and the stale-write guard"): read the
cells a write touches, fingerprint them, compare before writing.

    read(spreadsheet_id, ranges)     -> (book, {handle: cell}) for every touched range
    fingerprint(cells)               -> {handle: [shown, digest, parts]}, non-empty cells only
    check(old, new, what)            -> raises a Refusal listing up to 20 changes

A cell's fingerprint covers its value or formula, its rich text runs, its
`userEnteredFormat` and its note. Plain REST has no compare-and-set, so this
narrows the race to about one round trip; it is not a lock. Shared by
`write_cells` and the upload landing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import a1, reply, sheets_api

MAX_LISTED = 20
FIELDS = ("spreadsheetId,properties(title,locale,timeZone),"
          "sheets(properties(sheetId,title,hidden,gridProperties),merges,"
          "data(startRow,startColumn,rowMetadata(hiddenByUser,hiddenByFilter),"
          "columnMetadata(hiddenByUser),rowData.values(userEnteredValue,effectiveValue,"
          "formattedValue,userEnteredFormat,textFormatRuns,note,dataValidation)))")


def handle(title: str, row: int, col: int) -> str:
    return str(a1.Range(title, row, row + 1, col, col + 1))


def read(spreadsheet_id: str, ranges: list[a1.Range]
         ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """One `spreadsheets.get` over `ranges`; every cell Google returned, by handle."""
    book = sheets_api.get_spreadsheet(spreadsheet_id, [str(r) for r in ranges], fields=FIELDS)
    cells: dict[str, dict[str, Any]] = {}
    for sheet in book.get("sheets") or []:
        title = sheet["properties"]["title"]
        for block in sheet.get("data") or []:
            r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
            for i, row in enumerate(block.get("rowData") or []):
                for j, cell in enumerate(row.get("values") or []):
                    if cell:
                        cells[handle(title, r0 + i, c0 + j)] = cell
    return book, cells


def shown(cell: dict[str, Any] | None) -> str:
    cell = cell or {}
    uev = cell.get("userEnteredValue") or {}
    if "formulaValue" in uev:
        return uev["formulaValue"]
    if "formattedValue" in cell:
        return str(cell["formattedValue"])
    return str(next(iter(uev.values()), ""))


# What each fingerprinted field is called when only it changed.
PARTS = (("userEnteredValue", "value"), ("textFormatRuns", "rich text"),
         ("userEnteredFormat", "format"), ("note", "note"))


def _digest(cell: dict[str, Any]) -> str:
    part = {k: cell.get(k) for k, _ in PARTS if cell.get(k)}
    return hashlib.sha1(json.dumps(part, sort_keys=True).encode()).hexdigest()[:12] if part else ""


def _parts(cell: dict[str, Any]) -> str:
    """One short digest per field, so a refusal can say which field moved."""
    return " ".join(hashlib.sha1(json.dumps(cell.get(k), sort_keys=True).encode()).hexdigest()[:8]
                    if cell.get(k) else "-" for k, _ in PARTS)


def fingerprint(cells: dict[str, dict[str, Any]], touched: list[str]) -> dict[str, list[str]]:
    """The touched cells' fingerprints; an empty cell is left out (absent = empty)."""
    out = {}
    for name in touched:
        digest = _digest(cells.get(name) or {})
        if digest:
            out[name] = [shown(cells.get(name)), digest, _parts(cells.get(name) or {})]
    return out


def short(name: str, one_tab: bool) -> str:
    return name.rsplit("!", 1)[-1] if one_tab else name


def changes(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """(handle, old shown, new shown) for every cell whose fingerprint moved."""
    names = sorted(set(old) | set(new), key=_order)
    return [(n, (old.get(n) or ["", ""])[0], (new.get(n) or ["", ""])[0]) for n in names
            if (old.get(n) or ["", ""])[1] != (new.get(n) or ["", ""])[1]]


def _what_changed(old: list[str] | None, new: list[str] | None) -> str:
    """The fields that moved in a cell whose shown value did not, e.g. `format changed`."""
    def split(entry: list[str] | None) -> list[str]:
        if not entry:                                    # absent = empty
            return ["-"] * len(PARTS)
        return entry[2].split() if len(entry) > 2 else []
    a, b = split(old), split(new)
    if len(a) != len(PARTS) or len(b) != len(PARTS):     # a plan made before parts were kept
        return "changed"
    moved = [word for (_, word), x, y in zip(PARTS, a, b, strict=True) if x != y]
    return " and ".join(moved) + " changed" if moved else "changed"


def _change_line(name: str, a: str, b: str, old: list[str] | None, new: list[str] | None) -> str:
    if a == b:
        return f"  {name}: {_what_changed(old, new)}"
    return f"  {name}: {json.dumps(a, ensure_ascii=False)} → {json.dumps(b, ensure_ascii=False)}"


def _order(name: str) -> tuple:
    rng = a1.parse(name)
    return (rng.tab or "", rng.start_row or 0, rng.start_col or 0)


def check(old: dict[str, list[str]], new: dict[str, list[str]], what: str,
          one_tab: bool = True) -> None:
    """Refuse the whole batch when any touched cell changed since `old`."""
    moved = changes(old, new)
    if not moved:
        return
    listed = "\n".join(_change_line(short(n, one_tab), a, b, old.get(n), new.get(n))
                       for n, a, b in moved[:MAX_LISTED])
    more = f"\n  +{len(moved) - MAX_LISTED} more" if len(moved) > MAX_LISTED else ""
    raise reply.Refusal(f"Nothing was written: {len(moved)} "
                        f"{'cell' if len(moved) == 1 else 'cells'} of {what} changed since "
                        f"the plan.\n{listed}{more}\nMake a new plan.")

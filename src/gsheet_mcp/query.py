"""`query_range`: filter, pick, sort and count the rows of one table region,
live, answered as a Markdown table under the read budget."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from . import a1, notation, reader, reply, scan, sheets_api

_CONDITION = re.compile(
    r"^\s*(?P<col>.+?)\s*(?P<op>is not empty|is empty|contains|>=|<=|!=|==|=|>|<|~)"
    r"\s*(?P<value>.*?)\s*$", re.IGNORECASE)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHEETS_EPOCH = dt.date(1899, 12, 30)


@dataclass
class _Condition:
    text: str
    col: int
    op: str
    value: str


@dataclass
class _Region:
    box: scan.Box
    header: int
    names: list[str]          # one per column of the box


def _number(text: str) -> float | None:
    """A condition value as a number: `50`, `1,200`, `£52`, or an ISO date
    (as the day serial Sheets stores dates under)."""
    text = text.strip().strip("'\"")
    if _ISO_DATE.match(text):
        return float((dt.date.fromisoformat(text) - _SHEETS_EPOCH).days)
    try:
        return float(re.sub(r"[,\s£$€%]", "", text))
    except ValueError:
        return None


def _cell_number(cell: dict[str, Any]) -> float | None:
    value = (cell.get("effectiveValue") or {}).get("numberValue")
    return float(value) if isinstance(value, (int, float)) else None


def _cell_text(cell: dict[str, Any]) -> str:
    return str(cell.get("formattedValue", ""))


def _column(region: _Region, name: str) -> int:
    """A column of the region by header name (any case) or by column letter."""
    wanted = name.strip().strip("'\"").casefold()
    for i, header in enumerate(region.names):
        if header and header.casefold() == wanted:
            return region.box.c0 + i
    if re.fullmatch(r"[A-Za-z]{1,3}", name.strip()):
        col = a1.column_index(name.strip().upper())
        if region.box.c0 <= col <= region.box.c1:
            return col
    known = ", ".join(n or a1.column_letters(region.box.c0 + i)
                      for i, n in enumerate(region.names))
    raise reply.Refusal(f"no column {name!r} in {region.box.a1()}; its columns are: {known}")


def _parse(region: _Region, text: str) -> _Condition:
    m = _CONDITION.match(text)
    if not m:
        raise reply.Refusal(
            f"cannot read the condition {text!r}; write `<column> <op> <value>` with op one of "
            "= != > >= < <= contains ~ (regex), or `<column> is empty` / `is not empty`")
    op = m["op"].lower()
    op = "=" if op == "==" else op
    value = m["value"].strip().strip("'\"")
    if op not in ("is empty", "is not empty") and not value:
        raise reply.Refusal(f"the condition {text!r} has no value to compare with")
    if op == "~":
        try:
            re.compile(value)
        except re.error as e:
            raise reply.Refusal(f"/{value}/ in {text!r} is not a valid Python regex: {e}") from e
    return _Condition(text.strip(), _column(region, m["col"]), op, value)


def _matches(cond: _Condition, cell: dict[str, Any]) -> bool:
    text = _cell_text(cell)
    if cond.op == "is empty":
        return not text
    if cond.op == "is not empty":
        return bool(text)
    if cond.op == "contains":
        return cond.value.casefold() in text.casefold()
    if cond.op == "~":
        return re.search(cond.value, text, re.IGNORECASE) is not None
    left, right = _cell_number(cell), _number(cond.value)
    if left is not None and right is not None:
        pair: tuple[Any, Any] = (left, right)
    else:
        if cond.op in (">", ">=", "<", "<=") and not text:
            return False
        pair = (text.casefold(), cond.value.casefold())
    a, b = pair
    return {"=": a == b, "!=": a != b, ">": a > b, ">=": a >= b,
            "<": a < b, "<=": a <= b}[cond.op]


def _sort_key(cell: dict[str, Any]) -> tuple[int, Any]:
    number = _cell_number(cell)
    if number is not None:
        return (0, number)
    text = _cell_text(cell)
    return (1, text.casefold()) if text else (2, "")


def _pick_region(tab: scan.Tab, named: list[dict[str, Any]], box: scan.Box) -> _Region:
    regions, _loose = scan.find_regions(tab, named)
    tables = [r for r in regions if r.overlap(box) and scan.is_table(tab, r)]
    if not tables:
        raise reply.Refusal(f"no table region in {box.a1()}; query_range works on one table "
                            "region (scan_sheet lists them), read_range shows anything else")
    if len(tables) > 1:
        raise reply.Refusal(
            f"{len(tables)} table regions in {box.a1()} ("
            + ", ".join(r.a1() for r in tables)
            + "); pass one of them, or its scan alias, as the range")
    region = tables[0]
    if region.label.startswith("Table") and region.names:
        header, names = 1, list(region.names)
    else:
        header = scan._header_rows(tab, region)
        names = scan._header_names(tab, region, header, scan._merges_in(tab, region))
    names += [""] * (region.c1 - region.c0 + 1 - len(names))
    return _Region(region, header, names)


def query(spreadsheet_id: str, rng: a1.Range, *, where: list[str], columns: list[str] | None,
          sort: str | None, count: bool, include_hidden: bool,
          max_chars: int = notation.MAX_CELL_CHARS) -> str:
    props = sheets_api.find_tab(sheets_api.get_tabs(spreadsheet_id), rng.tab)
    title = props["title"]
    rng = rng.with_tab(title)
    book = sheets_api.get_spreadsheet(spreadsheet_id, [str(rng)], fields=sheets_api.LEAN_FIELDS)
    sheet = next(s for s in book.get("sheets", [])
                 if s["properties"]["sheetId"] == props["sheetId"])
    tab = scan.build_tab(sheet)
    region = _pick_region(tab, book.get("namedRanges") or [], reader._box(rng, props))
    box = region.box
    conditions = [_parse(region, text) for text in where]
    picked = ([_column(region, c) for c in columns] if columns
              else list(range(box.c0, box.c1 + 1)))
    keys = []
    for part in (sort or "").split(","):
        part = part.strip()
        if not part:
            continue
        desc = part.startswith("-") or part.lower().endswith(" desc")
        name = re.sub(r"\s+(desc|asc)$", "", part.lstrip("-+"), flags=re.IGNORECASE)
        keys.append((_column(region, name), desc))

    hidden: set[int] = set()
    for block in sheet.get("data") or []:
        r0 = block.get("startRow", 0)
        for i, meta in enumerate(block.get("rowMetadata") or []):
            if meta.get("hiddenByUser") or meta.get("hiddenByFilter"):
                hidden.add(r0 + i)
    rows, skipped_hidden, totals = [], 0, 0
    for r in range(box.r0 + region.header, box.r1 + 1):
        if r in box.blank_rows or not any((r, c) in tab.cells
                                           for c in range(box.c0, box.c1 + 1)):
            continue
        if r in hidden and not include_hidden:
            skipped_hidden += 1
            continue
        # (default, review) suspected total and subtotal rows are left out of a query
        if scan._is_total(tab, box, r):
            totals += 1
            continue
        rows.append(r)
    considered = len(rows)
    rows = [r for r in rows
            if all(_matches(c, tab.cells.get((r, c.col), {})) for c in conditions)]
    for col, desc in reversed(keys):
        filled = [r for r in rows if _cell_text(tab.cells.get((r, col), {}))]
        empty = [r for r in rows if r not in filled]
        filled.sort(key=lambda r, col=col: _sort_key(tab.cells.get((r, col), {})), reverse=desc)
        rows = filled + empty    # empty cells sort last either way

    label = f" · {box.label}" if box.label else ""
    head = (f"{a1.quote_tab(title, always=True)}!{box.a1()}{label} · "
            f"{len(rows)} of {considered} {'row' if considered == 1 else 'rows'} match")
    if conditions:
        head += " " + " and ".join(c.text for c in conditions)
    if keys:
        head += " · sorted by " + ", ".join(
            (region.names[col - box.c0] or a1.column_letters(col)) + (" desc" if desc else "")
            for col, desc in keys)
    notes = []
    if skipped_hidden:
        notes.append(f"{skipped_hidden} hidden {'row' if skipped_hidden == 1 else 'rows'} "
                     "left out · include_hidden=true includes them")
    if totals:
        notes.append(f"{totals} total {'row' if totals == 1 else 'rows'} left out")
    if count or not rows:
        return "\n".join([head, *notes])

    names = [region.names[c - box.c0] or a1.column_letters(c) for c in picked]
    table = ["| row | " + " | ".join(reader._cell_md(n) for n in names) + " |",
             "|" + "---|" * (len(picked) + 1)]
    budget = reader.BUDGET_TOKENS * reader.CHARS_PER_TOKEN - sum(len(x) + 1 for x in table)
    shown = 0
    for r in rows:
        cells = [tab.cells.get((r, c), {}) for c in picked]
        line = (f"| {r + 1} | " + " | ".join(
            reader._cell_md(notation.cut(notation.value(cell), max_chars) if cell else "")
            for cell in cells) + " |")
        if shown and len(line) + 1 > budget:
            break
        budget -= len(line) + 1
        table.append(line)
        shown += 1
    if shown < len(rows):
        notes.append(f"showing the first {shown} of {len(rows)} rows (read budget); narrow "
                     "the conditions, pick fewer columns, or pull the table with pull_tables")
    return "\n".join([head, *notes, "", *table])

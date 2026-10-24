"""The landscape scan behind `scan_sheet`: tabs, regions, images, comments.

Pure functions over one `spreadsheets.get` reply, the xlsx export and the
Drive comment list, plus the 10-minute scan cache that holds the region
aliases (`<tab>#<n>`) other tools resolve.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import a1, auth, generation, sheets_api, state_store

SCAN_FIELDS = (
    "properties.title,namedRanges,"
    "sheets(properties,merges,charts,tables,basicFilter,filterViews(title,range),"
    "data(startRow,startColumn,rowMetadata(hiddenByUser,hiddenByFilter,pixelSize),"
    "columnMetadata(hiddenByUser,pixelSize),"
    "rowData.values(formattedValue,effectiveValue,userEnteredFormat.numberFormat,"
    "effectiveFormat.numberFormat,dataValidation.condition.type,"
    "pivotTable(source,dataSourceId))))")
MAX_CHART_SOURCES = 6       # (default, review) source ranges listed per chart line

BIG_TAB_CELLS = 500_000
HEAD_ROWS = 2_000
TAIL_ROWS = 500
JOIN_GAP_ROWS = 3
MAX_REGIONS = 20
MAX_HEADER_ROWS = 3
FOOTNOTE_GAP_ROWS = 3       # (default, review) how far under a table a footnote may sit
TEXT_CHARS = 60
CACHE_TTL_SECONDS = 600
DEFAULT_COLUMN_PX = 100
DEFAULT_ROW_PX = 21
TOTAL_RE = re.compile(r"^(grand total|subtotal|total)\b", re.IGNORECASE)


# ---- the grid ------------------------------------------------------------------


@dataclass
class Box:
    """Rows and columns, 0-based, ends inclusive."""
    r0: int
    c0: int
    r1: int
    c1: int
    blank_rows: list[int] = field(default_factory=list)
    label: str = ""            # "Table Orders", "named range quarterly_targets"
    table_name: str = ""       # a native Table's own name, kept as the CLI's table name
    types: list[str] | None = None
    names: list[str] | None = None
    estimated: bool = False
    footnotes: list[tuple[int, int]] = field(default_factory=list)

    @property
    def area(self) -> int:
        return (self.r1 - self.r0 + 1) * (self.c1 - self.c0 + 1)

    def overlap(self, other: Box) -> int:
        rows = min(self.r1, other.r1) - max(self.r0, other.r0) + 1
        cols = min(self.c1, other.c1) - max(self.c0, other.c0) + 1
        return max(rows, 0) * max(cols, 0)

    def a1(self) -> str:
        return a1.Range(None, self.r0, self.r1 + 1, self.c0, self.c1 + 1).a1

    def contains(self, row: int, col: int) -> bool:
        return self.r0 <= row <= self.r1 and self.c0 <= col <= self.c1


def _grid_box(grid: dict[str, Any]) -> Box:
    rng = a1.Range.from_grid(grid)
    return Box(rng.start_row or 0, rng.start_col or 0, (rng.end_row or 1) - 1,
               (rng.end_col or 1) - 1)


def cell_type(cell: dict[str, Any]) -> str | None:
    value = cell.get("effectiveValue") or {}
    if "stringValue" in value:
        return "TEXT"
    if "boolValue" in value:
        return "BOOLEAN"
    if "errorValue" in value:
        return "ERROR"
    if "numberValue" in value:
        fmt = ((cell.get("effectiveFormat") or cell.get("userEnteredFormat") or {})
               .get("numberFormat") or {}).get("type")
        if fmt in ("DATE", "DATE_TIME", "TIME"):
            return "DATE"
        return fmt if fmt in ("CURRENCY", "PERCENT") else "DOUBLE"
    return None


def _filled(cell: dict[str, Any]) -> bool:
    return bool(cell.get("formattedValue")) or bool(cell.get("effectiveValue"))


@dataclass
class Tab:
    props: dict[str, Any]
    sheet: dict[str, Any]
    cells: dict[tuple[int, int], dict[str, Any]]
    row_px: dict[int, int]
    col_px: dict[int, int]
    hidden_rows: int
    hidden_cols: int
    head_end: int | None = None     # big tab: last head row read, and first tail row
    tail_start: int | None = None

    @property
    def title(self) -> str:
        return self.props["title"]

    def text(self, row: int, col: int) -> str:
        return str(self.cells.get((row, col), {}).get("formattedValue", ""))


def build_tab(sheet: dict[str, Any]) -> Tab:
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    row_px: dict[int, int] = {}
    col_px: dict[int, int] = {}
    hidden_rows: set[int] = set()
    hidden_cols: set[int] = set()
    for block in sheet.get("data") or []:
        r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
        for i, meta in enumerate(block.get("rowMetadata") or []):
            row_px[r0 + i] = meta.get("pixelSize", DEFAULT_ROW_PX)
            if meta.get("hiddenByUser") or meta.get("hiddenByFilter"):
                hidden_rows.add(r0 + i)
        for j, meta in enumerate(block.get("columnMetadata") or []):
            col_px[c0 + j] = meta.get("pixelSize", DEFAULT_COLUMN_PX)
            if meta.get("hiddenByUser"):
                hidden_cols.add(c0 + j)
        for i, row in enumerate(block.get("rowData") or []):
            for j, cell in enumerate(row.get("values") or []):
                if cell and _filled(cell):
                    cells[(r0 + i, c0 + j)] = cell
    return Tab(sheet["properties"], sheet, cells, row_px, col_px,
               len(hidden_rows), len(hidden_cols))


# ---- regions --------------------------------------------------------------------


def _components(tab: Tab) -> list[Box]:
    """8-connected flood fill over filled cells; a merge whose top-left cell is
    filled fills its whole area."""
    filled = set(tab.cells)
    for merge in tab.sheet.get("merges") or []:
        box = _grid_box(merge)
        if (box.r0, box.c0) in tab.cells:
            filled |= {(r, c) for r in range(box.r0, box.r1 + 1)
                       for c in range(box.c0, box.c1 + 1)}
    seen: set[tuple[int, int]] = set()
    boxes = []
    for start in sorted(filled):
        if start in seen:
            continue
        seen.add(start)
        stack, box = [start], Box(*start, *start)
        while stack:
            r, c = stack.pop()
            box.r0, box.r1 = min(box.r0, r), max(box.r1, r)
            box.c0, box.c1 = min(box.c0, c), max(box.c1, c)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nxt = (r + dr, c + dc)
                    if nxt in filled and nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
        boxes.append(box)
    return _merge_overlaps(boxes)


def _merge_overlaps(boxes: list[Box]) -> list[Box]:
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                if a.overlap(b):
                    a.r0, a.c0 = min(a.r0, b.r0), min(a.c0, b.c0)
                    a.r1, a.c1 = max(a.r1, b.r1), max(a.c1, b.c1)
                    a.estimated |= b.estimated
                    boxes.remove(b)
                    changed = True
                    break
            if changed:
                break
    return boxes


def _join(boxes: list[Box], tab: Tab) -> list[Box]:
    """Join a block to the one above when at most 3 blank rows separate them
    and both column ends match within one column. A big tab's head and tail
    blocks join across the unread middle and are marked estimated."""
    boxes = sorted(boxes, key=lambda b: (b.r0, b.c0))
    out: list[Box] = []
    for box in boxes:
        target = None
        for above in out:
            if abs(above.c0 - box.c0) > 1 or abs(above.c1 - box.c1) > 1:
                continue
            gap = box.r0 - above.r1 - 1
            across_middle = (tab.head_end is not None and above.r1 == tab.head_end
                             and box.r0 == tab.tail_start)
            if 0 <= gap <= JOIN_GAP_ROWS or across_middle:
                target = above
                if across_middle:
                    above.estimated = True
                else:
                    above.blank_rows += list(range(above.r1 + 1, box.r0))
                break
        if target is None:
            out.append(box)
            continue
        target.r1 = max(target.r1, box.r1)
        target.c0, target.c1 = min(target.c0, box.c0), max(target.c1, box.c1)
        target.blank_rows += box.blank_rows
    for box in out:
        if tab.head_end is not None and box.r1 == tab.head_end:
            box.estimated = True
    return out


def _exact_ranges(tab: Tab, named_ranges: list[dict[str, Any]]) -> list[Box]:
    sheet_id = tab.props.get("sheetId", 0)
    exact = []
    for table in tab.sheet.get("tables") or []:
        box = _grid_box(table["range"])
        box.label = f"Table {table.get('name', '')}"
        box.table_name = table.get("name", "")
        columns = table.get("columnProperties") or []
        box.names = [c.get("columnName", "") for c in columns]
        box.types = [c.get("columnType") or "-" for c in columns]
        exact.append(box)
    for named in named_ranges:
        rng = named.get("range") or {}
        if rng.get("sheetId", 0) == sheet_id:
            box = _grid_box(rng)
            box.label = f"named range {named.get('name', '')}"
            exact.append(box)
    return exact


def find_regions(tab: Tab, named_ranges: list[dict[str, Any]]) -> tuple[list[Box], list[str]]:
    """Regions in reading order, and the loose single cells (A1 names)."""
    guessed = _join(_components(tab), tab)
    exact = _exact_ranges(tab, named_ranges)
    # Spec gap: "exact ranges override the guess" does not say how much
    # overlap counts; a guessed block half or more inside an exact range goes.
    guessed = [g for g in guessed
               if not any(g.overlap(e) * 2 >= g.area for e in exact)]
    singles = [g for g in guessed if g.area == 1]
    regions = [g for g in guessed if g.area > 1] + exact
    loose = []
    footnotes: dict[int, list[tuple[int, int]]] = {}
    for cell in singles:
        owner = next((i for i, r in enumerate(regions)
                      if 1 <= cell.r0 - r.r1 <= FOOTNOTE_GAP_ROWS + 1
                      and r.c0 <= cell.c0 <= r.c1
                      and cell_type(tab.cells.get((cell.r0, cell.c0), {})) == "TEXT"), None)
        if owner is None:
            loose.append(cell)
        else:
            footnotes.setdefault(owner, []).append((cell.r0, cell.c0))
    for i, region in enumerate(regions):
        region.footnotes = footnotes.get(i, [])
    regions.sort(key=lambda b: (b.r0, b.c0))
    return regions, [b.a1() for b in sorted(loose, key=lambda b: (b.r0, b.c0))]


# ---- what a region reports --------------------------------------------------------


def _merges_in(tab: Tab, box: Box) -> list[Box]:
    return [m for m in (_grid_box(g) for g in tab.sheet.get("merges") or []) if box.overlap(m)]


def _row_types(tab: Tab, box: Box, row: int) -> list[str]:
    return [t for c in range(box.c0, box.c1 + 1)
            if (t := cell_type(tab.cells.get((row, c), {})))]


def _header_rows(tab: Tab, box: Box) -> int:
    rows = [r for r in range(box.r0, box.r1 + 1) if r not in box.blank_rows]
    count = 0
    for r in rows[:MAX_HEADER_ROWS]:
        types = _row_types(tab, box, r)
        if not types or any(t != "TEXT" for t in types):
            break
        count += 1
    if count == len(rows) or (count > 1 and all(set(_row_types(tab, box, r)) <= {"TEXT"}
                                                 for r in rows)):
        count = 1   # an all-text block: only its first row is a header
    return max(count, 1)


def _header_names(tab: Tab, box: Box, header: int, merges: list[Box]) -> list[str]:
    names = []
    for c in range(box.c0, box.c1 + 1):
        parts: list[str] = []
        for r in range(box.r0, box.r0 + header):
            merge = next((m for m in merges if m.contains(r, c)), None)
            text = tab.text(merge.r0, merge.c0) if merge else tab.text(r, c)
            if text and text not in parts:
                parts.append(text)
        names.append(" / ".join(parts))
    return names


def _column_type(types: list[str]) -> str:
    if not types:
        return "-"
    counts: dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    if len(counts) == 1:
        return types[0]
    shares = " ".join(f"{t} {round(100 * n / len(types))}%"
                      for t, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"mixed({shares})"


def _is_total(tab: Tab, box: Box, row: int) -> bool:
    for c in range(box.c0, box.c1 + 1):
        cell = tab.cells.get((row, c))
        if cell and cell_type(cell) == "TEXT":
            return bool(TOTAL_RE.match(str(cell.get("formattedValue", "")).strip()))
    return False


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS - 1] + "…"


def is_table(tab: Tab, box: Box) -> bool:
    """A table region, as opposed to a communication block: a native Table,
    or at least two rows and columns with data under the header and no merge there."""
    header = _header_rows(tab, box)
    below_header = [m for m in _merges_in(tab, box) if m.r0 >= box.r0 + header]
    return (box.label.startswith("Table") or
            (box.r1 > box.r0 and box.c1 > box.c0 and not below_header
             and any(r >= box.r0 + header for r, c in tab.cells if box.contains(r, c))))


def describe(tab: Tab, box: Box, images: list[dict[str, Any]]) -> str:
    merges = _merges_in(tab, box)
    header = _header_rows(tab, box)
    table = is_table(tab, box)
    head = f"{box.a1()}{' ' + box.label if box.label else ''}"
    extras = []
    if box.estimated:
        extras.append("extent estimated")
    if box.blank_rows:
        extras.append("blank rows " + ",".join(str(r + 1) for r in sorted(box.blank_rows)))
    if not table:
        cells = sorted((r, c) for (r, c) in tab.cells if box.contains(r, c))
        text = _clip(" ".join(tab.text(r, c) for r, c in cells
                              if cell_type(tab.cells[(r, c)]) == "TEXT"))
        checkboxes = sum(1 for r, c in cells if (tab.cells[(r, c)].get("dataValidation") or {})
                         .get("condition", {}).get("type") == "BOOLEAN")
        pictures = sum(1 for i in images if box.contains(i["row"], i["col"]))
        return " · ".join([f"{head} comms \"{text}\"", *extras, f"{len(merges)} merges",
                           f"{pictures} images", f"{checkboxes} checkboxes"])
    total_rows = [r for r in range(box.r0 + header, box.r1 + 1) if _is_total(tab, box, r)]
    data_rows = [r for r in range(box.r0 + header, box.r1 + 1)
                 if r not in total_rows and r not in box.blank_rows]
    names = box.names or _header_names(tab, box, header, merges)
    types = box.types or [
        _column_type([t for r in data_rows if (t := cell_type(tab.cells.get((r, c), {})))])
        for c in range(box.c0, box.c1 + 1)]
    parts = [f"{head} table", *extras]
    if header > 1:
        parts.append(f"{header} header rows")
    parts.append("columns: " + ", ".join(n or "-" for n in names))
    parts.append("types: " + ",".join(types))
    if total_rows:
        parts.append("totals: row " + ",".join(str(r + 1) for r in total_rows))
    for r, c in box.footnotes:
        ref = a1.Range(None, r, r + 1, c, c + 1).a1
        parts.append(f"footnote {ref} \"{_clip(tab.text(r, c))}\"")
    return " · ".join(parts)


# ---- images and comments ---------------------------------------------------------


def covered_range(tab: Tab | None, image: dict[str, Any]) -> str:
    """The cells a floating image covers, from its anchor, offsets and size."""
    def span(start: int, offset: int, length: int, sizes: dict[int, int], default: int) -> int:
        end, edge, index = offset + max(length, 1), 0, start
        while True:
            edge += sizes.get(index, default)
            if edge >= end:
                return index
            index += 1

    col_px = tab.col_px if tab else {}
    row_px = tab.row_px if tab else {}
    last_col = span(image["col"], image["col_off"], image["width"], col_px, DEFAULT_COLUMN_PX)
    last_row = span(image["row"], image["row_off"], image["height"], row_px, DEFAULT_ROW_PX)
    return a1.Range(None, image["row"], last_row + 1, image["col"], last_col + 1).a1


def place_comments(threads: list[dict[str, Any]],
                   comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each Drive thread with the tab and cell the export places it on (None
    when the export has no thread with the same text)."""
    pool = list(threads)
    placed = []
    for comment in comments:
        if comment.get("deleted"):
            continue
        match = next((t for t in pool if t["text"].strip() == comment.get("content", "").strip()),
                     None)
        if match:
            pool.remove(match)
        placed.append({"tab": match["tab"] if match else None,
                       "cell": match["cell"] if match else None,
                       "resolved": bool(comment.get("resolved")), "comment": comment})
    return placed


# ---- fetching -----------------------------------------------------------------------


def _ranges_for(props: dict[str, Any]) -> list[str]:
    grid = props.get("gridProperties", {})
    rows, cols = grid.get("rowCount", 0), grid.get("columnCount", 0)
    whole = a1.quote_tab(props["title"], always=True)
    if rows * cols <= BIG_TAB_CELLS:
        return [whole]
    tail_start = max(rows - TAIL_ROWS + 1, HEAD_ROWS + 1)
    return [f"{whole}!1:{HEAD_ROWS}", f"{whole}!{tail_start}:{rows}"]


def _cache_key(spreadsheet_id: str) -> str:
    try:
        caller = auth.caller_id()
    except auth.NotSignedInError:
        caller = ""
    # The generation changes on every write, so a write by any caller hides every scan.
    return f"scan:{caller}:{spreadsheet_id}:{generation.current(spreadsheet_id)}"


def load_cached(spreadsheet_id: str) -> dict[str, Any] | None:
    raw = state_store.current().get_cached(_cache_key(spreadsheet_id))
    return json.loads(raw) if raw else None


def resolve_alias(spreadsheet_id: str, alias: str) -> str | None:
    """`<tab>#<n>` as its handle (`'tab'!A1:D20`) while the scan cache lives."""
    cached = load_cached(spreadsheet_id)
    return (cached or {}).get("aliases", {}).get(alias)


def clear(spreadsheet_id: str) -> None:
    """Forget the cached scan; every write through our tools calls this."""
    state_store.current().drop_cached(_cache_key(spreadsheet_id))


def _xlsx_parts(spreadsheet_id: str) -> tuple[list | None, list | None, str]:
    """Images and export threads, or None for each with the reason."""
    try:
        xlsx = sheets_api.download(sheets_api.export_url(spreadsheet_id, "xlsx"))
    except (sheets_api.SheetsApiError, TimeoutError, OSError) as e:
        too_large = isinstance(e, TimeoutError) or "larger than" in str(e)
        return None, None, "workbook too large" if too_large else str(e)
    return sheets_api.xlsx_images(xlsx), sheets_api.xlsx_threads(xlsx), ""


def scan(spreadsheet_id: str, *, tab: str | None = None, include_hidden: bool = False) -> str:
    """Scan the workbook (or one tab), cache the aliases and image list, and
    return the reply text."""
    tabs_reply = sheets_api.get_tabs(spreadsheet_id)
    all_props = [s["properties"] for s in tabs_reply.get("sheets", [])]
    if tab is not None:
        all_props = [sheets_api.find_tab(tabs_reply, tab)]
    to_read = [p for p in all_props if include_hidden or not p.get("hidden")]
    ranges = [r for p in to_read for r in _ranges_for(p)]
    book = (sheets_api.get_spreadsheet(spreadsheet_id, ranges, fields=SCAN_FIELDS) if ranges
            else sheets_api.get_spreadsheet(spreadsheet_id, fields="namedRanges",
                                            include_grid_data=False))
    by_id = {s["properties"]["sheetId"]: s for s in book.get("sheets", [])}
    named = book.get("namedRanges") or []
    images, threads, xlsx_problem = _xlsx_parts(spreadsheet_id)
    try:
        comments = place_comments(threads or [], sheets_api.list_comments(spreadsheet_id))
        comment_problem = "" if threads is not None else xlsx_problem
    except sheets_api.SheetsApiError as e:
        comments, comment_problem = [], str(e)

    title = (tabs_reply.get("properties") or {}).get("title", "")
    out = [f"spreadsheet \"{title}\" ({spreadsheet_id}) · {len(tabs_reply.get('sheets', []))} tabs"]
    names_by_tab = {p.get("sheetId"): p["title"] for p in
                    (s["properties"] for s in tabs_reply.get("sheets", []))}
    if named:
        out.append("named ranges: " + ", ".join(
            f"{n.get('name')} {a1.Range.from_grid(n['range'], names_by_tab.get(n['range'].get('sheetId', 0)))}"
            for n in named))
    out.append(_comment_line(comments, comment_problem))
    if images is None:
        out.append(f"images: not checked ({xlsx_problem})")

    aliases: dict[str, str] = {}
    regions: dict[str, dict[str, Any]] = {}
    for props in all_props:
        sheet = by_id.get(props["sheetId"])
        built = build_tab(sheet) if sheet else None
        if built and len(_ranges_for(props)) == 2:
            built.head_end = HEAD_ROWS - 1
            built.tail_start = max(props["gridProperties"]["rowCount"] - TAIL_ROWS, HEAD_ROWS)
        out += _tab_block(props, built, named, images, comments, aliases, full=tab is not None,
                          regions=regions, titles=names_by_tab)

    # A one-tab scan adds its aliases to the whole-workbook scan's.
    cached = (load_cached(spreadsheet_id) or {}) if tab is not None else {}
    cached.setdefault("aliases", {}).update(aliases)
    cached.setdefault("regions", {}).update(regions)
    if images is not None:
        cached["images"] = images
    state_store.current().put_cached(_cache_key(spreadsheet_id), json.dumps(cached).encode(),
                                     ttl_seconds=CACHE_TTL_SECONDS)
    return "\n".join(out)


def _cell_a1(row: int, col: int) -> str:
    return a1.Range(None, row, row + 1, col, col + 1).a1


def _grid_a1(grid: dict[str, Any], titles: dict[int, str]) -> str:
    return str(a1.Range.from_grid(grid, titles.get(grid.get("sheetId", 0))))


def pivot_lines(sheet: dict[str, Any], found: list[Box], titles: dict[int, str]) -> list[str]:
    """One line per pivot table: its anchor, its source and the region it fills."""
    lines = []
    for block in sheet.get("data") or []:
        r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
        for i, row in enumerate(block.get("rowData") or []):
            for j, cell in enumerate(row.get("values") or []):
                pivot = (cell or {}).get("pivotTable")
                if not pivot:
                    continue
                r, c = r0 + i, c0 + j
                if "source" in pivot:
                    source = _grid_a1(pivot["source"], titles)
                else:
                    source = f"data source {pivot.get('dataSourceId', '?')}"
                fills = next((b for b in found if b.contains(r, c)), None)
                lines.append(f"  pivot at {_cell_a1(r, c)} · source {source}"
                             + (f" · fills {fills.a1()}" if fills else ""))
    return lines


def _chart_sources(spec: dict[str, Any], titles: dict[int, str]) -> str:
    """` · sources <a>, <b>`: every GridRange under a `sourceRange` in the chart
    spec, in order, deduped, at most MAX_CHART_SOURCES."""
    seen: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "sourceRange" and isinstance(value, dict):
                    for grid in value.get("sources") or []:
                        name = _grid_a1(grid, titles)
                        if name not in seen:
                            seen.append(name)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec)
    if not seen:
        return ""
    more = len(seen) - MAX_CHART_SOURCES
    return (" · sources " + ", ".join(seen[:MAX_CHART_SOURCES])
            + (f", +{more} more" if more > 0 else ""))


def _comment_line(comments: list[dict[str, Any]], problem: str) -> str:
    if problem:
        return f"comments: not checked ({problem})"
    if not comments:
        return "comments: none"
    per_tab: dict[str, list[int]] = {}
    for c in comments:
        counts = per_tab.setdefault(c["tab"] or "unplaced", [0, 0])
        counts[1 if c["resolved"] else 0] += 1
    noun = "thread" if len(comments) == 1 else "threads"
    return f"comments: {len(comments)} {noun} · " + " · ".join(
        f"{t} {o} open {r} resolved" for t, (o, r) in per_tab.items())


def _tab_block(props: dict[str, Any], tab: Tab | None, named: list[dict[str, Any]],
               images: list[dict[str, Any]] | None, comments: list[dict[str, Any]],
               aliases: dict[str, str], *, full: bool,
               regions: dict[str, dict[str, Any]] | None = None,
               titles: dict[int, str] | None = None) -> list[str]:
    grid = props.get("gridProperties", {})
    head = [f"tab \"{props['title']}\" (gid {props.get('sheetId')}) · "
            f"{grid.get('rowCount', 0)}x{grid.get('columnCount', 0)}"]
    if grid.get("frozenRowCount") or grid.get("frozenColumnCount"):
        head.append(f"frozen {grid.get('frozenRowCount', 0)}/{grid.get('frozenColumnCount', 0)}")
    if props.get("hidden"):
        head.append("hidden")
    if tab and (tab.hidden_rows or tab.hidden_cols):
        head.append(f"hidden rows {tab.hidden_rows} cols {tab.hidden_cols}")
    tab_images = [i for i in images or [] if i["tab"] == props["title"]]
    if images is not None:
        head.append(f"{len(tab_images)} images")
    lines = [" · ".join(head)]
    if tab is None:
        if props.get("hidden"):
            lines.append("  regions not scanned (hidden tab; pass include_hidden=true)")
        return lines
    sheet = tab.sheet
    titles = titles or {}
    found, loose = find_regions(tab, named)
    lines += pivot_lines(sheet, found, titles)
    for chart in sheet.get("charts") or []:
        spec = chart.get("spec", {})
        kind = (spec.get("basicChart", {}).get("chartType")
                or next((k.removesuffix("Chart") for k in spec if k.endswith("Chart")), "chart"))
        anchor = (chart.get("position", {}).get("overlayPosition", {}).get("anchorCell"))
        where = f" at {_cell_a1(anchor.get('rowIndex', 0), anchor.get('columnIndex', 0))}" if anchor else ""
        lines.append(f"  chart {str(kind).lower()} \"{spec.get('title', '')}\"{where}"
                     + _chart_sources(spec, titles))
    if basic := sheet.get("basicFilter"):
        lines.append(f"  basic filter {a1.Range.from_grid(basic.get('range', {})).a1}")
    if views := sheet.get("filterViews"):
        lines.append(f"  filter views ({len(views)}): "
                     + ", ".join(f"\"{v.get('title', '')}\"" for v in views))
    for image in tab_images:
        lines.append(f"  image over {covered_range(tab, image)} "
                     f"({image['width']}x{image['height']} px)")
    tab_comments = [c for c in comments if c["tab"] == props["title"]]
    if tab_comments:
        lines.append("  comments: " + ", ".join(
            f"{c['cell']} {'resolved' if c['resolved'] else 'open'}" for c in tab_comments))
    shown = found if full else found[:MAX_REGIONS]
    for n, region in enumerate(found, 1):
        alias = f"{props['title']}#{n}"
        aliases[alias] = str(
            a1.Range(props["title"], region.r0, region.r1 + 1, region.c0, region.c1 + 1))
        if regions is not None:
            # What pull_tables needs without scanning again: the tab, the kind, the header.
            regions[alias] = {"tab": props["title"], "gid": props.get("sheetId"),
                              "range": aliases[alias], "label": region.label,
                              "kind": "table" if is_table(tab, region) else "comms",
                              "header_rows": _header_rows(tab, region),
                              "estimated": region.estimated}
            if region.table_name:
                regions[alias]["table_name"] = region.table_name
    for n, region in enumerate(shown, 1):
        lines.append(f"  #{n} {describe(tab, region, tab_images)}")
    if len(found) > len(shown):
        lines.append(f"  +{len(found) - len(shown)} more (scan_sheet with tab= lists them all)")
    if loose:
        lines.append("  loose cells: " + ", ".join(loose))
    return lines

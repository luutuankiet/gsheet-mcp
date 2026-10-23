"""`read_range`'s text: one region at a time, in the shape the scan gave it.

Communication regions are cell-addressed lines under `── row N`; table regions
are Markdown tables with a `row` column. Styles and dropdown rules go into
legends the cells reference (`{s1}`, `▾v1`). The read is cut at row
boundaries into pages of about BUDGET_TOKENS; the header names the cell that
starts the next page."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import a1, notation, reply, scan, sheets_api

BUDGET_TOKENS = 8_000
CHARS_PER_TOKEN = 4
COMMENT_CHARS = 100        # (default, review) a comment's text in its line is clipped here
READ_FIELDS = sheets_api.LEAN_FIELDS.replace(
    "userEnteredFormat,", "userEnteredFormat,effectiveFormat,")

FormatDetail = str  # "default" | "formats" | "formulas"


# ---- cells and their styles ---------------------------------------------------


def _hex(colour: dict[str, Any] | None) -> str | None:
    if not colour:
        return None
    rgb = colour.get("rgbColor", colour)
    return "#" + "".join(f"{round(rgb.get(k, 0) * 255):02X}" for k in ("red", "green", "blue"))


def _format(cell: dict[str, Any]) -> dict[str, Any]:
    return cell.get("effectiveFormat") or cell.get("userEnteredFormat") or {}


def style_parts(cell: dict[str, Any], detail: str) -> tuple[str, ...]:
    """The style words of a cell; white fill, black text and left alignment are
    the defaults and say nothing."""
    fmt = _format(cell)
    text = fmt.get("textFormat") or {}
    fill = _hex(fmt.get("backgroundColorStyle") or fmt.get("backgroundColor"))
    colour = _hex(text.get("foregroundColorStyle") or text.get("foregroundColor"))
    parts = []
    if fill and fill != "#FFFFFF":
        parts.append(f"fill {fill}")
    if colour and colour != "#000000":
        parts.append(f"text {colour}")
    if text.get("bold"):
        parts.append("bold")
    if fmt.get("horizontalAlignment") == "CENTER":
        parts.append("centred")
    if detail == "formats":
        number = fmt.get("numberFormat") or {}
        if number.get("pattern"):
            parts.append(f"format {number['pattern']}")
        sides = [side for side in ("top", "bottom", "left", "right")
                 if (fmt.get("borders") or {}).get(side, {}).get("style", "NONE") != "NONE"]
        if sides:
            parts.append("border " + "/".join(sides))
    return tuple(parts)


@dataclass
class Legends:
    """Styles and dropdown rules, numbered in order of first use over the whole read."""
    detail: str
    styles: dict[tuple[str, ...], str] = field(default_factory=dict)
    rules: dict[tuple[str, ...], str] = field(default_factory=dict)

    def style(self, cell: dict[str, Any]) -> str | None:
        parts = style_parts(cell, self.detail)
        if not parts:
            return None
        return self.styles.setdefault(parts, f"s{len(self.styles) + 1}")

    def rule(self, cell: dict[str, Any]) -> str | None:
        values = notation.dropdown_values(cell)
        if values is None:
            return None
        return self.rules.setdefault(values, f"v{len(self.rules) + 1}")


# ---- the read, as units of rows ---------------------------------------------------


@dataclass
class Unit:
    """A row group of a communication region, or a row of a table: the
    smallest piece a page boundary may fall between."""
    anchor: tuple[int, int]
    lines: list[str]
    cells: int
    styles: set[str]
    rules: set[str]
    table_head: list[str] | None = None   # re-emitted when a page starts inside a table
    region: str = ""                      # the region line this unit belongs to


def _cell_name(row: int, col: int) -> str:
    return f"{a1.column_letters(col)}{row + 1}"


def _shown(cell: dict[str, Any]) -> bool:
    return bool(cell.get("formattedValue") or cell.get("effectiveValue")
                or notation.formula(cell) or cell.get("note"))


class Reader:
    def __init__(self, sheet: dict[str, Any], *, detail: str, include_hidden: bool,
                 max_chars: int, images: list[dict[str, Any]],
                 comments: list[dict[str, Any]] | None):
        self.sheet = sheet
        self.tab = scan.build_tab(sheet)
        self.detail = detail
        self.max_chars = max_chars
        self.include_hidden = include_hidden
        self.legends = Legends(detail)
        self.hidden_user: set[int] = set()
        self.hidden_filter: set[int] = set()
        self.hidden_cols: set[int] = set()
        self.cells: dict[tuple[int, int], dict[str, Any]] = {}
        for block in sheet.get("data") or []:
            r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
            for i, meta in enumerate(block.get("rowMetadata") or []):
                if meta.get("hiddenByUser"):
                    self.hidden_user.add(r0 + i)
                elif meta.get("hiddenByFilter"):
                    self.hidden_filter.add(r0 + i)
            for j, meta in enumerate(block.get("columnMetadata") or []):
                if meta.get("hiddenByUser"):
                    self.hidden_cols.add(c0 + j)
            for i, row in enumerate(block.get("rowData") or []):
                for j, cell in enumerate(row.get("values") or []):
                    if cell and _shown(cell):
                        self.cells[(r0 + i, c0 + j)] = cell
        self.merges = {(m.r0, m.c0): m for m in
                       (scan._grid_box(g) for g in sheet.get("merges") or [])}
        self.images = images
        self.comments = comments or []

    # -- what is skipped

    def skipped(self, row: int, col: int) -> bool:
        if self.include_hidden:
            return False
        return (row in self.hidden_user or row in self.hidden_filter
                or col in self.hidden_cols)

    def hidden_lines(self) -> list[str]:
        out = []
        for rows, why in ((self.hidden_user, "by the user"), (self.hidden_filter, "by filter")):
            for first, last in _runs(rows):
                n = last - first + 1
                name = f"row {first + 1}" if n == 1 else f"rows {first + 1}-{last + 1}"
                out.append(f"{name} hidden {why} ({n} {'row' if n == 1 else 'rows'})")
        for first, last in _runs(self.hidden_cols):
            n = last - first + 1
            name = (f"column {a1.column_letters(first)}" if n == 1 else
                    f"columns {a1.column_letters(first)}-{a1.column_letters(last)}")
            out.append(f"{name} hidden ({n} {'column' if n == 1 else 'columns'})")
        if out and not self.include_hidden:
            out[-1] += " · their content is skipped; include_hidden=true shows it"
        return out

    # -- one cell

    def value(self, cell: dict[str, Any], *, with_formula: bool) -> tuple[str, set[str]]:
        rule = self.legends.rule(cell)
        text = notation.cut(notation.value(cell, rule), self.max_chars)
        formula = notation.formula(cell)
        if with_formula and formula and text != formula:
            text = f"{text} ← {formula}"
        return text, {rule} if rule else set()

    def address(self, row: int, col: int) -> str:
        merge = self.merges.get((row, col))
        return merge.a1() if merge and merge.area > 1 else _cell_name(row, col)

    def extras(self, row: int, col: int, indent: str = "  ") -> list[str]:
        """Note and comment lines under a cell."""
        cell = self.cells.get((row, col), {})
        out = []
        if cell.get("note"):
            out.append(f"{indent}note: {notation.cut(cell['note'], self.max_chars)}")
        name = _cell_name(row, col)
        for placed in self.comments:
            if placed.get("cell") == name:
                out.append(indent + comment_line(placed, name))
        return out

    def image_lines(self, row: int, cols: range | None) -> list[tuple[int, str]]:
        return [(img["col"], f"{_cell_name(row, img['col'])} [image {img['width']}x"
                             f"{img['height']} over {scan.covered_range(self.tab, img)}, floating]")
                for img in self.images
                if img["row"] == row and (cols is None or img["col"] in cols)
                and not self.skipped(img["row"], img["col"])]

    # -- regions

    def comms_units(self, box: scan.Box | None, cells: list[tuple[int, int]],
                    region_line: str, image_rows: dict[int, list] | None = None) -> list[Unit]:
        by_row: dict[int, list[int]] = {}
        for r, c in cells:
            by_row.setdefault(r, []).append(c)
        for r in (image_rows or {}):
            by_row.setdefault(r, [])
        units = []
        for row in sorted(by_row):
            styles: set[str] = set()
            rules: set[str] = set()
            items: list[tuple[int, list[str]]] = []
            for col in sorted(by_row[row]):
                cell = self.cells[(row, col)]
                text, used = self.value(cell, with_formula=True)
                rules |= used
                style = self.legends.style(cell)
                if style:
                    styles.add(style)
                head = self.address(row, col) + (f" {{{style}}}" if style else "")
                text = text.replace("\n", "\n    ")
                items.append((col, [f"{head}: {text}" if text else head]
                              + self.extras(row, col)))
            for col, line in (image_rows or {}).get(row, []):
                items.append((col, [line]))
            items.sort(key=lambda item: item[0])
            first_col = box.c0 if box else min(c for c, _ in items)
            units.append(Unit((row, first_col), [f"── row {row + 1}"]
                              + [line for _, lines in items for line in lines],
                              len(by_row[row]), styles, rules, region=region_line))
        return units

    def table_units(self, box: scan.Box, region_line: str) -> list[Unit]:
        cols = [c for c in range(box.c0, box.c1 + 1)
                if self.include_hidden or c not in self.hidden_cols]
        header = scan._header_rows(self.tab, box)
        names = scan._header_names(self.tab, box, header, scan._merges_in(self.tab, box))
        names = [names[c - box.c0] or a1.column_letters(c) for c in cols]
        head = ["| row | " + " | ".join(_cell_md(n) for n in names) + " |",
                "|---|" + "---|" * len(cols)]
        with_formula = self.detail == "formulas"
        units = []
        rows = [r for r in range(box.r0 + header, box.r1 + 1)
                if any((r, c) in self.cells for c in cols)
                and (self.include_hidden or (r not in self.hidden_user
                                             and r not in self.hidden_filter))]
        header_cells = sum(1 for r in range(box.r0, box.r0 + header) for c in cols
                           if (r, c) in self.cells)
        for i, row in enumerate(rows):
            styles: set[str] = set()
            rules: set[str] = set()
            values, extra = [], []
            count = 0
            for col in cols:
                cell = self.cells.get((row, col))
                if not cell:
                    values.append("")
                    continue
                count += 1
                text, used = self.value(cell, with_formula=with_formula)
                rules |= used
                style = self.legends.style(cell)
                if style:
                    styles.add(style)
                    text += f" {{{style}}}"
                values.append(_cell_md(text))
                extra += [f"{_cell_name(row, col)} {line}" if line.startswith("note:")
                          else line for line in self.extras(row, col, indent="")]
            extra += [line for _, line in self.image_lines(row, range(box.c0, box.c1 + 1))]
            lines = [f"| {row + 1} | " + " | ".join(values) + " |"] + [f"  {e}" for e in extra]
            if i == 0:
                header_styles = {s for r in range(box.r0, box.r0 + header) for c in cols
                                 if (r, c) in self.cells
                                 and (s := self.legends.style(self.cells[(r, c)]))}
                styles |= header_styles
                count += header_cells
            units.append(Unit((row, box.c0), lines, count, styles, rules, table_head=head,
                              region=region_line))
        for r, c in box.footnotes:
            if (r, c) in self.cells and not self.skipped(r, c):
                units += self.comms_units(None, [(r, c)], region_line)
        return units

    def units(self, named: list[dict[str, Any]]) -> tuple[list[Unit], int, int]:
        """Every unit in reading order; the count of charts and images in the read."""
        regions, _loose = scan.find_regions(self.tab, named)
        claimed: set[tuple[int, int]] = set()
        units: list[Unit] = []
        images = [img for img in self.images if not self.skipped(img["row"], img["col"])]
        placed_images: set[int] = set()
        for box in regions:
            inside = [(r, c) for (r, c) in self.cells if box.contains(r, c)]
            for r, c in box.footnotes:
                claimed.add((r, c))
            claimed.update(inside)
            label = f" · {box.label}" if box.label else ""
            if scan.is_table(self.tab, box):
                line = f"region {box.a1()} · table{label}"
                units += self.table_units(box, line)
                placed_images |= {id(i) for i in images if box.contains(i["row"], i["col"])}
            else:
                line = f"region {box.a1()} · communication{label}"
                image_rows: dict[int, list] = {}
                for img in images:
                    if box.contains(img["row"], img["col"]):
                        placed_images.add(id(img))
                        image_rows.setdefault(img["row"], []).extend(
                            self.image_lines(img["row"], range(img["col"], img["col"] + 1)))
                units += self.comms_units(box, [p for p in inside if not self.skipped(*p)],
                                          line, image_rows)
        loose = [p for p in self.cells if p not in claimed and not self.skipped(*p)]
        loose_images: dict[int, list] = {}
        for img in images:
            if id(img) not in placed_images:
                loose_images.setdefault(img["row"], []).extend(
                    self.image_lines(img["row"], range(img["col"], img["col"] + 1)))
        if loose or loose_images:
            units += self.comms_units(None, loose, "loose cells", loose_images)
        return units, len(images)


def _runs(indices: set[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in sorted(indices):
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def _cell_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def comment_line(placed: dict[str, Any], name: str | None) -> str:
    comment = placed["comment"]
    replies = len([r for r in comment.get("replies") or [] if not r.get("deleted")])
    state = "resolved" if placed.get("resolved") else "open"
    author = (comment.get("author") or {}).get("displayName", "someone")
    text = " ".join(str(comment.get("content", "")).split())
    if len(text) > COMMENT_CHARS:
        text = text[:COMMENT_CHARS] + "…"
    where = f"{name} " if name else ""
    return (f'{where}comment ({state}, {replies} {"reply" if replies == 1 else "replies"}) '
            f'@{author}: "{text}"')


# ---- the whole read --------------------------------------------------------------


def _images_for(spreadsheet_id: str, title: str, want_threads: bool
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, str]:
    """Floating images of the tab (from the scan cache when it lives), and the
    export's comment threads when asked for."""
    cached = scan.load_cached(spreadsheet_id) or {}
    images, threads, problem = cached.get("images"), None, ""
    if images is None or want_threads:
        fetched, threads, problem = scan._xlsx_parts(spreadsheet_id)
        if images is None:
            images = fetched
    return [i for i in images or [] if i.get("tab") == title], threads, problem


def read(spreadsheet_id: str, rng: a1.Range, *, detail: str = "default",
         comments: bool = False, include_hidden: bool = False,
         max_chars: int = notation.MAX_CELL_CHARS, cursor: str | None = None) -> str:
    props = sheets_api.find_tab(sheets_api.get_tabs(spreadsheet_id), rng.tab)
    title = props["title"]
    rng = rng.with_tab(title)
    book = sheets_api.get_spreadsheet(spreadsheet_id, [str(rng)], fields=READ_FIELDS)
    sheet = next(s for s in book.get("sheets", [])
                 if s["properties"]["sheetId"] == props["sheetId"])
    images, threads, problem = _images_for(spreadsheet_id, title, comments)
    box = _box(rng, props)
    images = [i for i in images if box.contains(i["row"], i["col"])]
    placed: list[dict[str, Any]] = []
    if comments:
        try:
            placed = scan.place_comments(threads or [], sheets_api.list_comments(spreadsheet_id))
        except sheets_api.SheetsApiError as e:
            problem = str(e)
    in_tab = [p for p in placed if p["tab"] == title and p["cell"]
              and box.contains(*_cell_index(p["cell"]))]
    unplaced = [p for p in placed if p["tab"] is None]

    reader = Reader(sheet, detail=detail, include_hidden=include_hidden, max_chars=max_chars,
                    images=images, comments=in_tab)
    units, image_count = reader.units(book.get("namedRanges") or [])
    # A comment on a cell with nothing in it has no line to sit under.
    lined = {(u.anchor[0], c) for u in units for c in range(box.c0, box.c1 + 1)
             if (u.anchor[0], c) in reader.cells}
    orphans = [p for p in in_tab if _cell_index(p["cell"]) not in lined]

    start = 0
    if cursor:
        anchor = _cell_index(cursor)
        start = next((i for i, u in enumerate(units) if u.anchor == anchor), -1)
        if start < 0:
            raise reply.Refusal(f"cursor {cursor} does not start a row of {rng}; the sheet "
                                "may have changed since, so read again without a cursor")
    budget = BUDGET_TOKENS * CHARS_PER_TOKEN
    end, used = start, 0
    while end < len(units):
        size = sum(len(line) + 1 for line in units[end].lines)
        if end > start and used + size > budget:
            break
        used += size
        end += 1
    page = units[start:end]
    total_cells = sum(u.cells for u in units)
    first_cell = sum(u.cells for u in units[:start]) + 1
    last_cell = first_cell + sum(u.cells for u in page) - 1
    total_chars = sum(len(line) + 1 for u in units for line in u.lines)

    out = [f"{rng}{' (hidden tab)' if props.get('hidden') else ''} · "
           + (f"cells {first_cell:,}-{last_cell:,} of {total_cells:,}" if total_cells
              else "no cells")
           + f" ({notation.token_estimate(total_chars)})"
           + (f" · next: {_cell_name(*units[end].anchor)}" if end < len(units) else "")]
    out += reader.hidden_lines()
    if problem and (comments or not images):
        out.append(f"images{' and comments' if comments else ''}: not checked ({problem})")
    if start == 0:
        out += [comment_line(p, None) for p in unplaced]
        out += [comment_line(p, p["cell"]) for p in orphans]

    styles = {s for u in page for s in u.styles}
    rules = {r for u in page for r in u.rules}
    if styles:
        out.append("styles:")
        out += [f"  {name} {' · '.join(parts)}" for parts, name in reader.legends.styles.items()
                if name in styles]
    out += [f"{name} dropdown: {' | '.join(values)}"
            for values, name in reader.legends.rules.items() if name in rules]
    if detail == "formats":
        out += _format_lines(sheet, box)

    region = None
    for i, unit in enumerate(page):
        if unit.region != region:
            region = unit.region
            out += ["", region + (" (continued)" if i == 0 and start > 0
                                  and units[start - 1].region == region else "")]
            if unit.table_head:
                out += unit.table_head
        elif unit.table_head and not page[i - 1].table_head:
            out += unit.table_head
        out += unit.lines

    charts = [c for c in sheet.get("charts") or []
              if box.contains(*_anchor(c))]
    fills = {parts for parts in reader.legends.styles if parts and parts[0].startswith("fill")}
    worth = [f"{n} {word}{'' if n == 1 else 's'}" for n, word in
             ((len(charts), "chart"), (image_count, "floating image"),
              (len(fills), "fill style")) if n]
    if worth:
        # (default, review) no pointer line when the range has nothing a render adds
        out += ["", f"this range has {', '.join(worth)} · render_range(\"{rng}\")"]
    return "\n".join(out)


def _box(rng: a1.Range, props: dict[str, Any]) -> scan.Box:
    grid = props.get("gridProperties", {})
    return scan.Box(rng.start_row or 0, rng.start_col or 0,
                    (rng.end_row or grid.get("rowCount", 1)) - 1,
                    (rng.end_col or grid.get("columnCount", 1)) - 1)


def _cell_index(name: str) -> tuple[int, int]:
    try:
        rng = a1.parse(name)
    except a1.A1Error:
        raise reply.Refusal(f"{name!r} is not a cell like B240") from None
    if rng.start_row is None or rng.start_col is None:
        raise reply.Refusal(f"{name!r} is not a cell like B240")
    return rng.start_row, rng.start_col


def _anchor(chart: dict[str, Any]) -> tuple[int, int]:
    cell = ((chart.get("position") or {}).get("overlayPosition") or {}).get("anchorCell") or {}
    return cell.get("rowIndex", 0), cell.get("columnIndex", 0)


def _format_lines(sheet: dict[str, Any], box: scan.Box) -> list[str]:
    """detail="formats": non-default column widths and row heights, and the
    conditional format rules on the range."""
    widths, heights = [], []
    for block in sheet.get("data") or []:
        r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
        for j, meta in enumerate(block.get("columnMetadata") or []):
            px = meta.get("pixelSize", scan.DEFAULT_COLUMN_PX)
            if px != scan.DEFAULT_COLUMN_PX:
                widths.append(f"{a1.column_letters(c0 + j)} {px}px")
        for i, meta in enumerate(block.get("rowMetadata") or []):
            px = meta.get("pixelSize", scan.DEFAULT_ROW_PX)
            if px != scan.DEFAULT_ROW_PX:
                heights.append(f"{r0 + i + 1} {px}px")
    out = []
    if widths:
        out.append("column widths: " + ", ".join(widths))
    if heights:
        out.append("row heights: " + ", ".join(heights))
    for rule in sheet.get("conditionalFormats") or []:
        ranges = [scan._grid_box(g) for g in rule.get("ranges") or []]
        if not any(r.overlap(box) for r in ranges):
            continue
        if "booleanRule" in rule:
            condition = rule["booleanRule"].get("condition") or {}
            values = [v.get("userEnteredValue", "") for v in condition.get("values") or []]
            what = condition.get("type", "") + (f" {' '.join(values)}" if values else "")
            parts = style_parts({"effectiveFormat": rule["booleanRule"].get("format") or {}},
                                "default")
            what += f" → {' · '.join(parts)}" if parts else ""
        else:
            what = "colour scale"
        out.append(f"conditional format {', '.join(r.a1() for r in ranges)}: {what}")
    return out

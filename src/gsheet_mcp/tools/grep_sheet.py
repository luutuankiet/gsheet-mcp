# Copied from google-docs-mcp@2fb4652 src/<package>/tools/grep.py; regex, trim and cap kept, cells replace lines.
"""`grep_sheet`: a Python regex over every cell's formatted value, link URLs,
note and formula, and comment text when asked; one line per hit."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastmcp.tools import ToolResult

from .. import a1, addressing, notation, reply, scan, sheets_api
from ..app import READ, tool

DEFAULT_MAX_RESULTS = 50
TRIM_CHARS = 80
FIELDS = ("value", "link", "note", "formula", "comment")


@dataclass
class _Hit:
    where: str          # `'comms'!B1:F1`, or `(unplaced)` for a comment the export cannot place
    field: str
    snippet: str
    hidden: str = ""    # "hidden row", "hidden column", "hidden tab", joined with " · "


def _trim_hit(line: str, m: re.Match) -> str:
    """About TRIM_CHARS characters centred on the match, `…` where cut."""
    if len(line) <= TRIM_CHARS:
        return line
    span = min(m.end() - m.start(), TRIM_CHARS)
    left = m.start() - (TRIM_CHARS - span) // 2
    left = max(0, min(left, len(line) - TRIM_CHARS))
    right = left + TRIM_CHARS
    return f"{'…' if left else ''}{line[left:right]}{'…' if right < len(line) else ''}"


def _links(cell: dict[str, Any]) -> list[str]:
    """Every URL on a cell: its hyperlink, links on text runs, and chip links."""
    urls = [cell["hyperlink"]] if cell.get("hyperlink") else []
    for run in cell.get("textFormatRuns") or []:
        uri = ((run.get("format") or {}).get("link") or {}).get("uri")
        if uri and uri not in urls:
            urls.append(uri)
    for run in cell.get("chipRuns") or []:
        chip = run.get("chip") or {}
        uri = ((chip.get("richLinkProperties") or {}).get("uri")
               or ("mailto:" + chip["personProperties"]["email"]
                   if (chip.get("personProperties") or {}).get("email") else None))
        if uri and uri not in urls:
            urls.append(uri)
    return urls


def _fields(cell: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    if value := cell.get("formattedValue"):
        out.append(("value", str(value)))
    out += [("link", url) for url in _links(cell)]
    if note := cell.get("note"):
        out.append(("note", str(note)))
    if formula := notation.formula(cell):
        out.append(("formula", str(formula)))
    return out


def _search(regex: re.Pattern, field: str, text: str, where: str, hidden: str) -> _Hit | None:
    m = regex.search(text)
    if not m:
        return None
    # One line per hit: line breaks become spaces, so the match keeps its place.
    flat = text.replace("\r", " ").replace("\n", " ")
    return _Hit(where, field, _trim_hit(flat, m), hidden)


def _hidden(props: dict[str, Any], rows: set[int], cols: set[int], row: int, col: int) -> str:
    tags = [tag for tag, on in (("hidden tab", props.get("hidden")), ("hidden row", row in rows),
                                ("hidden column", col in cols)) if on]
    return " · ".join(tags)


def _tab_hits(regex: re.Pattern, sheet: dict[str, Any], box: scan.Box,
              comments: list[dict[str, Any]]) -> list[_Hit]:
    props = sheet["properties"]
    quoted = a1.quote_tab(props["title"], always=True)
    hidden_rows: set[int] = set()
    hidden_cols: set[int] = set()
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for block in sheet.get("data") or []:
        r0, c0 = block.get("startRow", 0), block.get("startColumn", 0)
        for i, meta in enumerate(block.get("rowMetadata") or []):
            if meta.get("hiddenByUser") or meta.get("hiddenByFilter"):
                hidden_rows.add(r0 + i)
        for j, meta in enumerate(block.get("columnMetadata") or []):
            if meta.get("hiddenByUser"):
                hidden_cols.add(c0 + j)
        for i, row in enumerate(block.get("rowData") or []):
            for j, cell in enumerate(row.get("values") or []):
                if cell and box.contains(r0 + i, c0 + j):
                    cells[(r0 + i, c0 + j)] = cell
    merges = [scan._grid_box(g) for g in sheet.get("merges") or []]

    def where(row: int, col: int) -> str:
        merge = next((m for m in merges if m.contains(row, col)), None)
        name = merge.a1() if merge else f"{a1.column_letters(col)}{row + 1}"
        return f"{quoted}!{name}"

    found: list[tuple[tuple[int, int, int], _Hit]] = []
    for (row, col), cell in cells.items():
        for field, text in _fields(cell):
            hit = _search(regex, field, text, where(row, col),
                          _hidden(props, hidden_rows, hidden_cols, row, col))
            if hit:
                found.append(((row, col, FIELDS.index(field)), hit))
    for placed in comments:
        row, col = _cell_index(placed["cell"])
        if not box.contains(row, col):
            continue
        hit = _search(regex, "comment", _comment_text(placed), where(row, col),
                      _hidden(props, hidden_rows, hidden_cols, row, col))
        if hit:
            found.append(((row, col, FIELDS.index("comment")), hit))
    return [hit for _key, hit in sorted(found, key=lambda pair: pair[0])]


def _comment_text(placed: dict[str, Any]) -> str:
    """The thread's first comment and its replies, joined by ` / `."""
    comment = placed["comment"]
    parts = [str(comment.get("content", ""))]
    parts += [str(r.get("content", "")) for r in comment.get("replies") or []
              if not r.get("deleted") and r.get("content")]
    return " / ".join(parts)


def _cell_index(name: str) -> tuple[int, int]:
    rng = a1.parse(name)
    return rng.start_row or 0, rng.start_col or 0


def _line(hit: _Hit) -> str:
    tag = hit.field + (f" · {hit.hidden}" if hit.hidden else "")
    return f"{hit.where}  [{tag}]  {hit.snippet}"


@tool(READ)
def grep_sheet(spreadsheet: str, pattern: str, range: str | None = None,  # noqa: A002
               ignore_case: bool = False, comments: bool = False,
               max_results: int = DEFAULT_MAX_RESULTS) -> ToolResult:
    """Search a spreadsheet with a Python regex, one line per hit.

    Searches each cell's formatted value (what the sheet shows), its link
    URLs (hyperlink, linked text, chips), its note and its formula; with
    `comments=true`, comment threads too (the first comment and its replies).
    Hidden rows, columns and tabs are searched and their hits tagged.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      pattern: a Python regular expression (`re` syntax), matched within
        each field on its own.
      range: a tab plus an A1 range (`'table for comms'!B2:F8`), a whole tab
        (`'comms'`), or a scan alias (`comms#1`). Left out, every tab.
      ignore_case: match regardless of case.
      comments: also search comment threads (one extra export fetch).
      max_results: most hit lines to show (50); the rest are counted per tab
        as `+N more in <tab>`.

    Returns:
      A header with the hit count, then one line per hit:
      `'comms'!B1:F1  [value]  Weekly status · lorem ipsum`. A hit inside a
      merge names the merge range; the tag in brackets names the field that
      matched (`value`, `link`, `note`, `formula`, `comment`) and adds
      `hidden row`, `hidden column` or `hidden tab` when the cell is hidden.
      Text longer than about 80 characters is trimmed around the match with
      `…`; read_range shows it whole.
    """
    if max_results < 1:
        raise reply.Refusal(f"max_results must be at least 1, not {max_results}")
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise reply.Refusal(f"/{pattern}/ is not a valid Python regex: {e}") from e
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    rng = addressing.resolve(spreadsheet_id, range) if range else None
    try:
        return reply.text(_grep(spreadsheet_id, regex, rng, comments, max_results))
    except sheets_api.SheetsApiError as e:
        raise reply.Refusal(f"Cannot search {spreadsheet_id}: {e}") from None


def _grep(spreadsheet_id: str, regex: re.Pattern, rng: a1.Range | None, comments: bool,
          max_results: int) -> str:
    tabs = sheets_api.get_tabs(spreadsheet_id)
    if rng is None:
        # (default, review) with no range, every tab is searched, hidden tabs included
        chosen = [s["properties"] for s in tabs.get("sheets") or []]
        ranges = [a1.Range(p["title"]) for p in chosen]
    else:
        props = sheets_api.find_tab(tabs, rng.tab)
        chosen, ranges = [props], [rng.with_tab(props["title"])]
    book = sheets_api.get_spreadsheet(spreadsheet_id, [str(r) for r in ranges],
                                      fields=sheets_api.LEAN_FIELDS)
    sheets = {s["properties"]["sheetId"]: s for s in book.get("sheets") or []}

    placed: list[dict[str, Any]] = []
    problem = ""
    if comments:
        _images, threads, problem = scan._xlsx_parts(spreadsheet_id)
        try:
            placed = scan.place_comments(threads or [], sheets_api.list_comments(spreadsheet_id))
        except sheets_api.SheetsApiError as e:
            problem = str(e)

    per_tab: list[tuple[str, list[_Hit]]] = []
    for props, r in zip(chosen, ranges, strict=True):
        sheet = sheets.get(props["sheetId"])
        if sheet is None:
            continue
        grid = props.get("gridProperties", {})
        box = scan.Box(r.start_row or 0, r.start_col or 0,
                       (r.end_row or grid.get("rowCount", 1)) - 1,
                       (r.end_col or grid.get("columnCount", 1)) - 1)
        mine = [p for p in placed if p["tab"] == props["title"] and p["cell"]]
        hits = _tab_hits(regex, sheet, box, mine)
        if hits:
            per_tab.append((props["title"], hits))
    if rng is None:
        unplaced = [_search(regex, "comment", _comment_text(p), "(unplaced)", "")
                    for p in placed if p["tab"] is None]
        if hits := [h for h in unplaced if h]:
            per_tab.append(("(unplaced comments)", hits))

    flags = " (ignore case)" if regex.flags & re.IGNORECASE else ""
    where = str(ranges[0]) if rng is not None else "every tab"
    total = sum(len(h) for _t, h in per_tab)
    notes = [f"comments: not checked ({problem})"] if comments and problem else []
    if not total:
        return "\n".join([f"no hits for /{regex.pattern}/{flags} in {where}", *notes])
    head = (f"{total} {'hit' if total == 1 else 'hits'} in {len(per_tab)} "
            f"{'tab' if len(per_tab) == 1 else 'tabs'} for /{regex.pattern}/{flags} in {where}")
    if total > max_results:
        head += f" · showing the first {max_results} (max_results)"
    out = [head, *notes]
    budget = max_results
    for title, hits in per_tab:
        shown = hits[:budget]
        budget -= len(shown)
        out += [_line(h) for h in shown]
        if len(hits) > len(shown):
            name = title if title.startswith("(") else a1.quote_tab(title, always=True)
            out.append(f"+{len(hits) - len(shown)} more in {name}")
    return "\n".join(out)

"""A1 addressing: the canonical handle is a tab plus an A1 range (spec, "Addressing").

  parse("'table for comms'!B2:F8")  -> Range(tab="table for comms", rows 1-8, columns 1-6)
  str(range)                         -> the handle again
  Range.from_grid(grid_range, tab)   -> a Range from a Sheets GridRange (merges, Tables)
  range.to_grid(sheet_id)            -> the GridRange batchUpdate takes

Indexes are 0-based and half-open, as in a GridRange. Any bound may be None:
`A:C` has no row bounds, `3:5` no column bounds, `B2:B` no end row. A Range
with a tab and no bounds at all is the whole tab, written `'<tab>'`; spec,
"Addressing": a missing range means the whole used area, which the caller
resolves, not this module.

Quoting: a tab name with spaces or punctuation is quoted, a quote inside it is
doubled. A whole tab is always quoted, so `'hidden'` (the tab) never reads as a
bare named range or cell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PLAIN_TAB = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENDPOINT = re.compile(r"(?P<col>[A-Za-z]{1,3})?(?P<row>[1-9][0-9]*)?")
_CELL = re.compile(r"[A-Za-z]{1,3}[1-9][0-9]*")
MAX_COLUMNS = 18278  # ZZZ


class A1Error(ValueError):
    """Text that is not a tab plus an A1 range."""


def column_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    if index < 0:
        raise A1Error(f"column index {index} is negative")
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def column_index(letters: str) -> int:
    """A -> 0, Z -> 25, AA -> 26."""
    n = 0
    for ch in letters.upper():
        if not "A" <= ch <= "Z":
            raise A1Error(f"{letters!r} is not a column")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    if not 0 < n <= MAX_COLUMNS:
        raise A1Error(f"{letters!r} is not a column")
    return n - 1


def quote_tab(name: str, *, always: bool = False) -> str:
    """The tab name as it goes before `!`: quoted when it has spaces or
    punctuation, or looks like a cell (`AB12`), or when `always`."""
    if not always and _PLAIN_TAB.fullmatch(name) and not _CELL.fullmatch(name):
        return name
    return "'" + name.replace("'", "''") + "'"


@dataclass(frozen=True)
class Range:
    tab: str | None = None
    start_row: int | None = None
    end_row: int | None = None       # exclusive
    start_col: int | None = None
    end_col: int | None = None       # exclusive

    @property
    def whole_tab(self) -> bool:
        return (self.start_row is None and self.end_row is None
                and self.start_col is None and self.end_col is None)

    @property
    def a1(self) -> str:
        """The range part alone, without the tab; empty for a whole tab."""
        if self.whole_tab:
            return ""
        rows_open = self.start_row is None and self.end_row is None
        cols_open = self.start_col is None and self.end_col is None
        if rows_open:   # A:C
            return f"{column_letters(self.start_col or 0)}:{column_letters(_last(self.end_col))}"
        if cols_open:   # 3:5
            return f"{(self.start_row or 0) + 1}:{_last(self.end_row) + 1}"
        start = column_letters(self.start_col or 0) + str((self.start_row or 0) + 1)
        end = (column_letters(_last(self.end_col)) if self.end_col is not None else "") + \
            (str(_last(self.end_row) + 1) if self.end_row is not None else "")
        if self.end_col is not None and self.end_row is not None and \
                self.end_row - (self.start_row or 0) == 1 and \
                self.end_col - (self.start_col or 0) == 1:
            return start   # one cell
        return f"{start}:{end}"

    def __str__(self) -> str:
        if self.tab is None:
            return self.a1
        if self.whole_tab:
            return quote_tab(self.tab, always=True)
        return f"{quote_tab(self.tab)}!{self.a1}"

    def with_tab(self, tab: str) -> Range:
        return Range(tab, self.start_row, self.end_row, self.start_col, self.end_col)

    def width(self, tab_columns: int) -> int:
        """Columns this range spans on a tab `tab_columns` wide (open ends clipped)."""
        start = self.start_col or 0
        end = tab_columns if self.end_col is None else min(self.end_col, tab_columns)
        return max(end - start, 0)

    def to_grid(self, sheet_id: int) -> dict[str, Any]:
        """A GridRange; open bounds are left out, as Sheets reads them."""
        grid: dict[str, Any] = {"sheetId": sheet_id}
        for key, value in (("startRowIndex", self.start_row), ("endRowIndex", self.end_row),
                           ("startColumnIndex", self.start_col),
                           ("endColumnIndex", self.end_col)):
            if value is not None:
                grid[key] = value
        return grid

    @classmethod
    def from_grid(cls, grid: dict[str, Any], tab: str | None = None) -> Range:
        """A GridRange (a merge, a Table's range, a named range) as a Range.
        Sheets leaves a 0 start index out of its replies, so a missing start
        next to a present end is 0, not open."""
        def start(key: str, end_key: str) -> int | None:
            if key in grid:
                return grid[key]
            return 0 if end_key in grid else None
        return cls(tab, start("startRowIndex", "endRowIndex"), grid.get("endRowIndex"),
                   start("startColumnIndex", "endColumnIndex"), grid.get("endColumnIndex"))


def _last(end: int | None) -> int:
    if end is None:
        raise A1Error("an open end cannot be written here")
    return end - 1


def _split_tab(text: str) -> tuple[str | None, str, bool]:
    """(tab, rest, tab was quoted). `rest` is what follows `!`, or the whole text."""
    if text.startswith("'"):
        i, name = 1, []
        while i < len(text):
            if text[i] == "'":
                if text[i + 1:i + 2] == "'":
                    name.append("'")
                    i += 2
                    continue
                break
            name.append(text[i])
            i += 1
        else:
            raise A1Error(f"unclosed quote in {text!r}")
        after = text[i + 1:]
        if after == "":
            return "".join(name), "", True
        if not after.startswith("!"):
            raise A1Error(f"expected ! after the quoted tab name in {text!r}")
        return "".join(name), after[1:], True
    if "!" in text:
        tab, rest = text.rsplit("!", 1)
        return tab, rest, False
    return None, text, False


def _endpoint(text: str) -> tuple[int | None, int | None]:
    m = _ENDPOINT.fullmatch(text)
    if not text or not m:
        raise A1Error(f"{text!r} is not a cell, column or row")
    col = column_index(m["col"]) if m["col"] else None
    row = int(m["row"]) - 1 if m["row"] else None
    return row, col


def _range_part(text: str) -> tuple[int | None, int | None, int | None, int | None]:
    if ":" in text:
        first, second = text.split(":", 1)
    else:
        first = second = text
    r1, c1 = _endpoint(first)
    r2, c2 = _endpoint(second)
    if r1 is None and c1 is None or r2 is None and c2 is None:
        raise A1Error(f"{text!r} is not an A1 range")
    if c1 is None and c2 is not None or r1 is None and r2 is not None and c1 is None:
        raise A1Error(f"{text!r} is not an A1 range")
    if c1 is None:              # 3:5
        start_col = end_col = None
    else:
        start_col = c1
        end_col = c2 + 1 if c2 is not None else None
    if r1 is None:              # A:C
        start_row = end_row = None
        if r2 is not None:
            raise A1Error(f"{text!r} is not an A1 range")
    else:
        start_row = r1
        end_row = r2 + 1 if r2 is not None else None
    for lo, hi in ((start_row, end_row), (start_col, end_col)):
        if lo is not None and hi is not None and hi <= lo:
            raise A1Error(f"{text!r} ends before it starts")
    return start_row, end_row, start_col, end_col


def parse(text: str) -> Range:
    """A handle as a Range. Accepts `'tab'!B2:F8`, `tab!B2`, `'tab'` (the whole
    tab), `A:C`, `3:5`, `B2:B` and a bare tab name that is not an A1 range."""
    given = (text or "").strip()
    if not given:
        raise A1Error("no range given")
    tab, rest, quoted = _split_tab(given)
    if tab is not None and not rest:
        if not quoted:
            raise A1Error(f"nothing after ! in {given!r}")
        return Range(tab)
    if tab is None:
        try:
            return Range(None, *_range_part(rest))
        except A1Error:
            if _PLAIN_TAB.fullmatch(rest):   # a bare tab name: the whole tab
                return Range(rest)
            raise
    return Range(tab, *_range_part(rest))

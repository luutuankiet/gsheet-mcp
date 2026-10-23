# Copied from google-docs-mcp@2fb4652 tests/fake_docs.py; the Docs resource swapped for Sheets and Drive, builders dropped, canary fixtures served.
"""A fake Google Sheets and Drive API at the network boundary.

`FakeSheets` stands in for the googleapiclient `sheets` v4 and `drive` v3
resources, and for the editor's export link, so `gsheet_mcp.sheets_api` runs
its real code against it. It serves `tests/fixtures/canary.json` (spreadsheet
id `canary`) and `tests/fixtures/canary.xlsx`, and behaves like Google where
the tests depend on it:

- `spreadsheets().get()` applies its `fields` mask, and with `ranges` returns
  only the tabs those ranges are on, one `data` block per range.
- `values().get()` trims trailing blank cells and rows, as Google does.
- Drive's comment threads come from the xlsx export's threaded comments, with
  an opaque anchor, as Google gives them.
- Every `batchUpdate` is recorded in `fake.batch_updates`; `updateCells` is
  applied, every other request kind is recorded only.
- `fake.rate_limit(n)` makes the next n calls answer 429.
- `fake.set_cell(...)` changes a cell now; `fake.after_next_call(...)` changes
  one right after the next call, to stand in for someone editing between
  a stale-write guard's read and its write.
"""
from __future__ import annotations

import copy
import csv
import io
import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httplib2
from googleapiclient.errors import HttpError

from gsheet_mcp import a1, sheets_api

FIXTURES = Path(__file__).parent / "fixtures"
CANARY_ID = "canary"


def http_error(status: int, message: str) -> HttpError:
    """An HttpError shaped like the ones googleapiclient raises for Google's replies."""
    reason = {400: "INVALID_ARGUMENT", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
              429: "RESOURCE_EXHAUSTED"}.get(status, "")
    body = json.dumps({"error": {"code": status, "message": message, "status": reason}})
    return HttpError(httplib2.Response({"status": status}), body.encode())


# ---- field masks ------------------------------------------------------------


def parse_mask(fields: str) -> dict[str, Any]:
    """`a.b,c(d,e)` as a tree: {"a": {"b": {}}, "c": {"d": {}, "e": {}}}; {} = all."""
    tree: dict[str, Any] = {}
    stack = [tree]
    path: list[dict[str, Any]] = []   # the node each open path ends at
    name = ""

    def close_name() -> None:
        nonlocal name
        if name:
            node = path[-1] if path else stack[-1]
            path.append(node.setdefault(name, {}))
            name = ""

    for ch in fields.replace(" ", ""):
        if ch == ".":
            close_name()
        elif ch == "(":
            close_name()
            stack.append(path[-1])
            path.clear()
        elif ch == ",":
            close_name()
            path.clear()
        elif ch == ")":
            close_name()
            path.clear()
            stack.pop()
        else:
            name += ch
    close_name()
    return tree


def apply_mask(value: Any, mask: dict[str, Any]) -> Any:
    if not mask:
        return value
    if isinstance(value, list):
        return [apply_mask(v, mask) for v in value]
    if isinstance(value, dict):
        return {k: apply_mask(v, mask[k]) for k, v in value.items() if k in mask}
    return value


# ---- grid helpers -----------------------------------------------------------


def _sheet(book: dict[str, Any], tab: str | None) -> dict[str, Any]:
    for sheet in book["sheets"]:
        if tab is None or sheet["properties"]["title"] == tab:
            return sheet
    raise http_error(400, f"Unable to parse range: {tab}")


def _bounds(sheet: dict[str, Any], rng: a1.Range) -> tuple[int, int, int, int]:
    grid = sheet["properties"]["gridProperties"]
    return (rng.start_row or 0, min(rng.end_row or grid["rowCount"], grid["rowCount"]),
            rng.start_col or 0, min(rng.end_col or grid["columnCount"], grid["columnCount"]))


def _cell(sheet: dict[str, Any], row: int, col: int) -> dict[str, Any]:
    rows = sheet["data"][0]["rowData"]
    if row >= len(rows):
        return {}
    values = rows[row].get("values", [])
    return values[col] if col < len(values) else {}


def _rendered(cell: dict[str, Any], render: str) -> Any:
    if render == "FORMATTED_VALUE":
        return cell.get("formattedValue", "")
    source = cell.get("userEnteredValue" if render == "FORMULA" else "effectiveValue") or {}
    return next(iter(source.values()), "") if source else ""


def _trim(rows: list[list[Any]]) -> list[list[Any]]:
    out = []
    for row in rows:
        while row and row[-1] == "":
            row.pop()
        out.append(row)
    while out and not out[-1]:
        out.pop()
    return out


def blank_pdf(pages: int) -> bytes:
    """A real PDF of blank landscape letter pages, which PDFium can rasterise."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(792, 612)
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


class FakeSheets:
    """Holds spreadsheets by id and serves them the way the Sheets and Drive APIs do."""

    def __init__(self) -> None:
        self.books: dict[str, dict[str, Any]] = {}
        self.xlsx: dict[str, bytes] = {}
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.batch_updates: list[tuple[str, list[dict[str, Any]]]] = []
        self.calls: list[str] = []              # every call, e.g. "spreadsheets.get"
        self.downloads: list[str] = []          # every export URL fetched
        self._rate_limited = 0
        self.pdf_pages = 1                      # pages in a PDF export
        self._after_next: list[Callable[[], None]] = []
        self._ids = itertools.count(1)

    # -- setup ---------------------------------------------------------------

    def load_canary(self) -> str:
        book = json.loads((FIXTURES / "canary.json").read_text())
        self.books[CANARY_ID] = book
        self.xlsx[CANARY_ID] = (FIXTURES / "canary.xlsx").read_bytes()
        self.comments[CANARY_ID] = [self._drive_thread(t)
                                    for t in sheets_api.xlsx_threads(self.xlsx[CANARY_ID])]
        return CANARY_ID

    def _drive_thread(self, thread: dict[str, Any]) -> dict[str, Any]:
        author = {"displayName": "Canary Owner", "emailAddress": "owner@example.com"}
        return {"id": f"comment-{next(self._ids)}", "content": thread["text"],
                "author": author, "resolved": thread["done"], "deleted": False,
                "anchor": json.dumps({"type": "workbook-range", "uid": 0,
                                      "range": str(next(self._ids) * 1000003)}),
                "replies": [{"id": f"reply-{next(self._ids)}", "content": text,
                             "author": author, "deleted": False}
                            for text in thread["replies"]]}

    def rate_limit(self, calls: int) -> None:
        """The next `calls` calls (API or export) answer 429."""
        self._rate_limited = calls

    def set_cell(self, spreadsheet_id: str, handle: str, value: str | float | bool) -> None:
        """Change one cell's value now, as a person typing in the editor would."""
        rng = a1.parse(handle)
        sheet = _sheet(self.books[spreadsheet_id], rng.tab)
        row, col = rng.start_row or 0, rng.start_col or 0
        rows = sheet["data"][0].setdefault("rowData", [])
        while len(rows) <= row:
            rows.append({})
        values = rows[row].setdefault("values", [])
        while len(values) <= col:
            values.append({})
        kind = ("boolValue" if isinstance(value, bool) else
                "numberValue" if isinstance(value, int | float) else
                "formulaValue" if str(value).startswith("=") else "stringValue")
        shown = str(value).upper() if isinstance(value, bool) else str(value)
        values[col]["userEnteredValue"] = {kind: value}
        values[col]["effectiveValue"] = {kind if kind != "formulaValue" else "stringValue": value}
        values[col]["formattedValue"] = shown

    def after_next_call(self, change: Callable[[], None]) -> None:
        """Run `change` right after the next call is answered."""
        self._after_next.append(change)

    # -- the network boundary --------------------------------------------------

    def _answer(self, name: str, compute: Callable[[], Any]) -> Any:
        self.calls.append(name)
        if self._rate_limited:
            self._rate_limited -= 1
            raise http_error(429, "Quota exceeded for quota metric 'Read requests'")
        result = compute()
        pending, self._after_next = self._after_next, []
        for change in pending:
            change()
        return result

    def spreadsheets(self) -> _Spreadsheets:
        return _Spreadsheets(self)

    def download(self, url: str, timeout: float) -> bytes:
        """The editor's export link: xlsx from the fixture, CSV made from the
        tab's values, PDF as `pdf_pages` blank landscape pages."""
        import urllib.error

        self.downloads.append(url)
        self.calls.append("export")
        if self._rate_limited:
            self._rate_limited -= 1
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
        parsed = urlparse(url)
        spreadsheet_id = parsed.path.split("/d/")[1].split("/")[0]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if spreadsheet_id not in self.books:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]
        if query["format"] == "xlsx":
            return self.xlsx[spreadsheet_id]
        if query["format"] == "pdf":
            return blank_pdf(self.pdf_pages)
        book = self.books[spreadsheet_id]
        sheet = next(s for s in book["sheets"] if s["properties"]["sheetId"] == int(query["gid"]))
        rng = a1.parse(query["range"]) if "range" in query else a1.Range()
        r0, r1, c0, c1 = _bounds(sheet, rng)
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(
            [[_rendered(_cell(sheet, r, c), "FORMATTED_VALUE") for c in range(c0, c1)]
             for r in range(r0, r1)])
        return out.getvalue().encode()

    # -- what the resources do ---------------------------------------------------

    def _book(self, spreadsheet_id: str) -> dict[str, Any]:
        if spreadsheet_id not in self.books:
            raise http_error(404, "Requested entity was not found.")
        return self.books[spreadsheet_id]

    def get(self, spreadsheetId: str, fields: str | None = None,  # noqa: N803
            ranges: list[str] | None = None, includeGridData: bool = False) -> dict[str, Any]:  # noqa: N803
        book = copy.deepcopy(self._book(spreadsheetId))
        if ranges:
            sheets = []
            for text in ranges:
                rng = a1.parse(text)
                sheet = copy.deepcopy(_sheet(book, rng.tab))
                sheet["data"] = [self._grid(sheet, rng)]
                known = next((s for s in sheets if s["properties"]["sheetId"]
                              == sheet["properties"]["sheetId"]), None)
                if known:
                    known["data"].extend(sheet["data"])
                else:
                    sheets.append(sheet)
            book["sheets"] = sheets
        if not includeGridData:
            for sheet in book["sheets"]:
                sheet.pop("data", None)
        return apply_mask(book, parse_mask(fields)) if fields else book

    @staticmethod
    def _grid(sheet: dict[str, Any], rng: a1.Range) -> dict[str, Any]:
        r0, r1, c0, c1 = _bounds(sheet, rng)
        whole = sheet["data"][0]
        grid: dict[str, Any] = {}
        if r0:
            grid["startRow"] = r0
        if c0:
            grid["startColumn"] = c0
        rows = [{"values": row.get("values", [])[c0:c1]} if row.get("values") else {}
                for row in whole.get("rowData", [])[r0:r1]]
        grid["rowData"] = rows
        grid["rowMetadata"] = whole.get("rowMetadata", [])[r0:r1]
        grid["columnMetadata"] = whole.get("columnMetadata", [])[c0:c1]
        return grid

    def values_get(self, spreadsheetId: str, valueRenderOption: str = "FORMATTED_VALUE",  # noqa: N803
                   majorDimension: str = "ROWS", **kw: Any) -> dict[str, Any]:  # noqa: N803
        rng = a1.parse(kw["range"])   # Google's parameter name shadows a builtin
        sheet = _sheet(self._book(spreadsheetId), rng.tab)
        r0, r1, c0, c1 = _bounds(sheet, rng)
        rows = _trim([[_rendered(_cell(sheet, r, c), valueRenderOption) for c in range(c0, c1)]
                      for r in range(r0, r1)])
        reply: dict[str, Any] = {"range": str(rng.with_tab(sheet["properties"]["title"])),
                                 "majorDimension": majorDimension}
        if rows:
            reply["values"] = rows
        return reply

    def batch_update(self, spreadsheetId: str, body: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        book = self._book(spreadsheetId)
        requests = copy.deepcopy(body.get("requests", []))
        self.batch_updates.append((spreadsheetId, requests))
        replies = []
        for request in requests:
            if "updateCells" in request:
                self._update_cells(book, request["updateCells"])
            replies.append({})
        return {"spreadsheetId": spreadsheetId, "replies": replies}

    def _update_cells(self, book: dict[str, Any], body: dict[str, Any]) -> None:
        """`updateCells` with a `start` and `userEnteredValue` or `note` in its fields."""
        start = body["start"]
        sheet = next(s for s in book["sheets"] if s["properties"]["sheetId"] == start["sheetId"])
        title = sheet["properties"]["title"]
        fields = set(body.get("fields", "").split(","))
        for dr, row in enumerate(body.get("rows", [])):
            for dc, cell in enumerate(row.get("values", [])):
                handle = str(a1.Range(title, start.get("rowIndex", 0) + dr,
                                      start.get("rowIndex", 0) + dr + 1,
                                      start.get("columnIndex", 0) + dc,
                                      start.get("columnIndex", 0) + dc + 1))
                if "userEnteredValue" in fields and "userEnteredValue" in cell:
                    self.set_cell(book["spreadsheetId"], handle,
                                  next(iter(cell["userEnteredValue"].values())))
                if "note" in fields:
                    target = _cell(sheet, start.get("rowIndex", 0) + dr,
                                   start.get("columnIndex", 0) + dc)
                    if cell.get("note"):
                        target["note"] = cell["note"]
                    else:
                        target.pop("note", None)

    def comments_list(self, fileId: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        self._book(fileId)
        return {"comments": copy.deepcopy(self.comments.get(fileId, []))}

    def comments_create(self, fileId: str, body: dict[str, Any], **_: Any) -> dict[str, Any]:  # noqa: N803
        self._book(fileId)
        thread = {"id": f"comment-{next(self._ids)}", "content": body["content"],
                  "author": {"displayName": "Canary Owner"}, "resolved": False,
                  "deleted": False, "replies": []}
        self.comments.setdefault(fileId, []).append(thread)
        return copy.deepcopy(thread)

    def replies_create(self, fileId: str, commentId: str, body: dict[str, Any],  # noqa: N803
                       **_: Any) -> dict[str, Any]:
        thread = next((c for c in self.comments.get(fileId, []) if c["id"] == commentId), None)
        if thread is None:
            raise http_error(404, "Comment not found")
        reply = {"id": f"reply-{next(self._ids)}", "content": body.get("content", ""),
                 "author": {"displayName": "Canary Owner"}, "deleted": False}
        if body.get("action"):
            reply["action"] = body["action"]
            thread["resolved"] = body["action"] == "resolve"
        thread["replies"].append(reply)
        return copy.deepcopy(reply)


class _Request:
    def __init__(self, fake: FakeSheets, name: str, compute: Callable[[], Any]):
        self._fake, self._name, self._compute = fake, name, compute

    def execute(self) -> Any:
        return self._fake._answer(self._name, self._compute)


class _Spreadsheets:
    def __init__(self, fake: FakeSheets):
        self._fake = fake

    def get(self, **kw: Any) -> _Request:
        return _Request(self._fake, "spreadsheets.get", lambda: self._fake.get(**kw))

    def batchUpdate(self, **kw: Any) -> _Request:  # noqa: N802
        return _Request(self._fake, "spreadsheets.batchUpdate",
                        lambda: self._fake.batch_update(**kw))

    def values(self) -> _Values:
        return _Values(self._fake)


class _Values:
    def __init__(self, fake: FakeSheets):
        self._fake = fake

    def get(self, **kw: Any) -> _Request:
        return _Request(self._fake, "values.get", lambda: self._fake.values_get(**kw))


class _Comments:
    def __init__(self, fake: FakeSheets):
        self._fake = fake

    def list(self, **kw: Any) -> _Request:
        return _Request(self._fake, "comments.list", lambda: self._fake.comments_list(**kw))

    def create(self, **kw: Any) -> _Request:
        return _Request(self._fake, "comments.create",
                        lambda: self._fake.comments_create(**kw))


class _Replies:
    def __init__(self, fake: FakeSheets):
        self._fake = fake

    def create(self, **kw: Any) -> _Request:
        return _Request(self._fake, "replies.create", lambda: self._fake.replies_create(**kw))


class FakeDrive:
    """The `drive` v3 resource surface over the same FakeSheets."""

    def __init__(self, fake: FakeSheets):
        self._fake = fake

    def comments(self) -> _Comments:
        return _Comments(self._fake)

    def replies(self) -> _Replies:
        return _Replies(self._fake)

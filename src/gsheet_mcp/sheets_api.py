# Copied from google-docs-mcp@2fb4652 src/<package>/docs_api.py; Docs calls swapped for Sheets and Drive, export download from pages.py, 429 retry and per-user lock added.
"""The Google Sheets and Drive APIs, thinly.

  spreadsheet_id_from_url(url_or_id)           -> spreadsheet id
  get_spreadsheet(id, ranges=None, fields=LEAN) -> one `spreadsheets.get` with the lean mask
  get_tabs(id)                                  -> tab properties only, no grid
  batch_update(id, requests)                    -> the batchUpdate reply
  values_get(id, range)                         -> rows padded to the range's width on the tab
  export_url(id, fmt, gid=None, range=None)     -> the editor's CSV, xlsx or PDF export link
  download(url)                                 -> the export's bytes, as the caller
  list_comments(id) / add_comment / reply_comment -> Drive comment threads
  xlsx_threads(xlsx)                            -> the export's threaded comments with their cells

Every call builds its client from the current caller's Google grant, and
every call one caller makes runs one at a time (spec, "Deployment": the
server serialises per user). A 429, or a 403 that names a rate limit or
quota, is retried once after a backoff; a second one becomes a visible
refusal (spec, "Deployment" and `render_range`).

Without a lean field mask a grid read is far too big (spec, "Facts": 57 MB
for 363 valued cells), so `get_spreadsheet` always sends one.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from io import BytesIO
from typing import IO, Any
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import a1, auth, reply

_URL_PATTERNS = [
    re.compile(r"/spreadsheets/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
]
_ID_RE = re.compile(r"[a-zA-Z0-9_-]+")

# One `spreadsheets.get` covers almost every feature per cell and per tab
# (spec, "Facts"). tests/fixtures/canary.json was captured with this mask,
# less the locale, time zone and data offsets.
LEAN_FIELDS = (
    "spreadsheetId,properties(title,locale,timeZone),namedRanges,"
    "sheets(properties,merges,charts,tables,bandedRanges,basicFilter,filterViews,"
    "conditionalFormats,protectedRanges,"
    "data(startRow,startColumn,rowMetadata(hiddenByUser,hiddenByFilter,pixelSize),"
    "columnMetadata(hiddenByUser,pixelSize),"
    "rowData.values(userEnteredValue,effectiveValue,formattedValue,userEnteredFormat,"
    "textFormatRuns,chipRuns,note,hyperlink,dataValidation)))")
TAB_FIELDS = "spreadsheetId,properties(title,locale,timeZone),sheets.properties"

COMMENT_FIELDS = ("comments(id,content,author(displayName,emailAddress),resolved,anchor,"
                  "quotedFileContent,createdTime,modifiedTime,deleted,"
                  "replies(id,content,author(displayName,emailAddress),action,createdTime,"
                  "deleted)),nextPageToken")

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{id}/export?format={fmt}"
EXPORT_FORMATS = ("csv", "xlsx", "pdf")
EXPORT_TIMEOUT = 120           # seconds; a 200k-row tab's CSV takes about 14 s
MAX_EXPORT_BYTES = 512 * 1024 * 1024

RETRY_BACKOFF_SECONDS = 5.0    # (default, review) one wait before the single retry
RETRY_LATER = ("Google's Sheets quota for this account is used up for now (about 60 "
               "requests a minute per person). It was retried once. Nothing more was sent; "
               "retry in a minute.")


def spreadsheet_id_from_url(url_or_id: str) -> str:
    """Accept a Google Sheets URL or a raw spreadsheet id. Returns the id."""
    given = (url_or_id or "").strip()
    if not given:
        raise ValueError("No spreadsheet given: pass a Google Sheets URL or a spreadsheet id.")
    if urlparse(given).scheme in ("http", "https"):
        for pattern in _URL_PATTERNS:
            if m := pattern.search(given):
                return m.group(1)
        raise ValueError(f"Cannot find a spreadsheet id in this URL: {given}")
    if _ID_RE.fullmatch(given):
        return given
    raise ValueError(f"Not a Google Sheets URL or spreadsheet id: {given}")


def spreadsheet_url(spreadsheet_id: str, gid: int | None = None) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    return f"{url}#gid={gid}" if gid is not None else url


def _sheets_service():
    """The Sheets client for the current caller. Per call, never cached: one
    caller's client must not serve another."""
    return build("sheets", "v4", credentials=auth.caller_credentials(), cache_discovery=False)


def _drive_service():
    return build("drive", "v3", credentials=auth.caller_credentials(), cache_discovery=False)


class SheetsApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


def is_quota(status: int | None, text: str) -> bool:
    text = text.lower()
    return status == 429 or (status == 403 and ("rate limit" in text or "quota" in text))


# ---- per-user serialisation and the one retry ---------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _caller_lock() -> threading.Lock:
    try:
        who = auth.caller_id()
    except auth.NotSignedInError:
        who = ""   # no caller: the client build refuses on its own
    with _locks_guard:
        return _locks.setdefault(who, threading.Lock())


def _sleep(seconds: float) -> None:
    """The backoff wait. Tests replace it."""
    time.sleep(seconds)


def _with_retry(send: Callable[[], Any], quota: Callable[[Exception], bool]) -> Any:
    """Run `send` under the caller's lock; on a quota error wait, retry once,
    then refuse visibly. The lock is held through the wait, so the caller's
    next request queues behind the backoff instead of adding to the burst."""
    with _caller_lock():
        try:
            return send()
        except Exception as e:  # noqa: BLE001 - only quota errors are retried
            if not quota(e):
                raise
        _sleep(RETRY_BACKOFF_SECONDS)
        try:
            return send()
        except Exception as e:  # noqa: BLE001
            if quota(e):
                raise reply.Refusal(RETRY_LATER) from e
            raise


def _api_error(e: HttpError, api: str) -> SheetsApiError:
    status = getattr(e.resp, "status", None)
    message = f"Google {api} API error {status}: {e.reason or str(e)}"
    if str(status) == "401":
        message += (". Google no longer accepts this caller's sign-in. Ask the user "
                    "to re-authenticate this MCP server in their MCP client, then retry.")
    return SheetsApiError(message, status=int(status) if status else None, reason=e.reason)


def _http_quota(e: Exception) -> bool:
    return isinstance(e, HttpError) and is_quota(
        int(getattr(e.resp, "status", 0) or 0), f"{e.reason} {e}")


def _call(request, api: str = "Sheets") -> Any:
    try:
        return _with_retry(request.execute, _http_quota)
    except HttpError as e:
        raise _api_error(e, api) from e


# ---- Sheets -------------------------------------------------------------------


def get_spreadsheet(spreadsheet_id: str, ranges: list[str] | None = None, *,
                    fields: str = LEAN_FIELDS, include_grid_data: bool = True) -> dict[str, Any]:
    """One `spreadsheets.get` with a field mask. With `ranges`, Google returns
    only the tabs those ranges are on, one `data` block per range."""
    params: dict[str, Any] = {"spreadsheetId": spreadsheet_id, "fields": fields,
                              "includeGridData": include_grid_data}
    if ranges:
        params["ranges"] = [str(r) for r in ranges]
    return _call(_sheets_service().spreadsheets().get(**params))


def get_tabs(spreadsheet_id: str) -> dict[str, Any]:
    """Title, locale and each tab's properties; no grid."""
    return get_spreadsheet(spreadsheet_id, fields=TAB_FIELDS, include_grid_data=False)


def batch_update(spreadsheet_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """One atomic batchUpdate. Plain REST has no compare-and-set
    (`requiredRevisionId` is accepted but ignored; spec, "Facts"), so the
    stale-write guard lives above this call, not in it."""
    return _call(_sheets_service().spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}))


def find_tab(spreadsheet: dict[str, Any], tab: str | None) -> dict[str, Any]:
    """A tab's properties by title; the first tab when `tab` is None."""
    sheets = spreadsheet.get("sheets") or []
    for sheet in sheets:
        props = sheet.get("properties", {})
        if tab is None or props.get("title") == tab:
            return props
    names = ", ".join(repr(s.get("properties", {}).get("title")) for s in sheets)
    raise SheetsApiError(f"No tab {tab!r} in this spreadsheet. Tabs: {names}", status=404)


def values_get(spreadsheet_id: str, range_: str | a1.Range, *,
               render: str = "FORMATTED_VALUE") -> list[list[Any]]:
    """`values.get`, with each row padded to the range's width on its tab.

    Google trims trailing blank cells from each row (spec, "Facts"), so a
    row of 6 columns can come back with 2; padding puts them back as ""."""
    rng = range_ if isinstance(range_, a1.Range) else a1.parse(range_)
    props = find_tab(get_tabs(spreadsheet_id), rng.tab)
    rng = rng.with_tab(props["title"])
    width = rng.width(props.get("gridProperties", {}).get("columnCount", 0))
    got = _call(_sheets_service().spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=str(rng), valueRenderOption=render,
        majorDimension="ROWS"))
    # Spec gap: the spec pads rows to the width only; trailing blank rows that
    # Google drops are not added back.
    return [list(row) + [""] * (width - len(row)) for row in got.get("values", [])]


# ---- exports ------------------------------------------------------------------


def export_url(spreadsheet_id: str, fmt: str, *, gid: int | None = None,
               range_: str | a1.Range | None = None) -> str:
    """The editor's export link. CSV is one tab (`gid`); xlsx is the whole
    workbook; PDF is a tab or a range on it. Not a documented API: it is the
    link the editor's File > Download uses, and it needs only the
    `spreadsheets` scope (spec, "Facts")."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"export format must be one of {EXPORT_FORMATS}, not {fmt!r}")
    url = EXPORT_URL.format(id=spreadsheet_id, fmt=fmt)
    if fmt == "xlsx":
        return url
    if gid is None:
        raise ValueError(f"a {fmt} export needs the tab's gid")
    url += f"&gid={gid}"
    if range_ is not None:
        rng = range_ if isinstance(range_, a1.Range) else a1.parse(range_)
        if not rng.whole_tab:
            url += "&range=" + quote(rng.a1, safe=":")
    return url


def _download(url: str, timeout: float) -> bytes:
    """The one network call: GET an export link as the caller. Tests replace it."""
    token = auth.caller_credentials().token
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = response.read(MAX_EXPORT_BYTES + 1)
    if len(data) > MAX_EXPORT_BYTES:
        raise SheetsApiError(f"the export is larger than {MAX_EXPORT_BYTES // 2**20} MB")
    return data


def _export_quota(e: Exception) -> bool:
    return isinstance(e, urllib.error.HTTPError) and is_quota(e.code, str(e.reason))


def download(url: str, timeout: float = EXPORT_TIMEOUT) -> bytes:
    """An export's bytes. Repeated exports hit 429 (spec, "Facts"): one retry,
    then a visible refusal."""
    return _fetch_export(lambda: _download(url, timeout))


def _open_download(url: str, timeout: float) -> IO[bytes]:
    """GET an export link as the caller and hand back the open response,
    unread. Tests replace it."""
    token = auth.caller_credentials().token
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


def open_export(url: str, timeout: float = EXPORT_TIMEOUT) -> IO[bytes]:
    """An export as a stream to read and close, for copying it on without
    holding it in memory (a pull). No size cap: nothing is kept here. The
    same one retry on 429 as `download`."""
    return _fetch_export(lambda: _open_download(url, timeout))


def _fetch_export(send: Callable[[], Any]) -> Any:
    try:
        return _with_retry(send, _export_quota)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise SheetsApiError(f"Google refused the export ({e.code}); the caller cannot "
                                 f"open this spreadsheet, or must sign in again.",
                                 status=e.code) from e
        raise SheetsApiError(f"Google answered {e.code} to the export; try again in a "
                             f"moment", status=e.code) from e
    except urllib.error.URLError as e:
        raise SheetsApiError(f"network error fetching the export: {e.reason}") from e


# ---- Drive comments -------------------------------------------------------------


def list_comments(spreadsheet_id: str) -> list[dict[str, Any]]:
    """Every comment thread with its replies. A thread's `anchor` is an opaque
    id, not a cell (spec, "Facts"); `xlsx_threads` places them."""
    comments = _drive_service().comments()
    out: list[dict[str, Any]] = []
    token = None
    while True:
        page = _call(comments.list(fileId=spreadsheet_id, fields=COMMENT_FIELDS,
                                   pageSize=100, includeDeleted=False, pageToken=token),
                     "Drive")
        out.extend(page.get("comments") or [])
        token = page.get("nextPageToken")
        if not token:
            return out


def add_comment(spreadsheet_id: str, content: str) -> dict[str, Any]:
    """A new unanchored thread: no public API anchors a new comment to a cell
    (spec, "Rules")."""
    return _call(_drive_service().comments().create(
        fileId=spreadsheet_id, body={"content": content},
        fields="id,content,author(displayName),resolved,createdTime"), "Drive")


def reply_comment(spreadsheet_id: str, comment_id: str, content: str = "", *,
                  resolve: bool = False) -> dict[str, Any]:
    """A reply on an existing thread; `resolve=True` resolves it as well."""
    body: dict[str, Any] = {"content": content}
    if resolve:
        body["action"] = "resolve"
    return _call(_drive_service().replies().create(
        fileId=spreadsheet_id, commentId=comment_id, body=body,
        fields="id,content,action,author(displayName),createdTime"), "Drive")


_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_TC = "{http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships: id -> target path inside the package."""
    folder, name = part.rsplit("/", 1)
    rels = f"{folder}/_rels/{name}.rels"
    if rels not in z.namelist():
        return {}
    out = {}
    for rel in ElementTree.fromstring(z.read(rels)).iter(f"{_REL}Relationship"):
        target = rel.get("Target", "")
        path = target.lstrip("/") if target.startswith("/") else _join(folder, target)
        out[rel.get("Id", "")] = path
    return out


def _join(folder: str, target: str) -> str:
    parts = folder.split("/")
    for piece in target.split("/"):
        if piece == "..":
            parts.pop()
        elif piece not in ("", "."):
            parts.append(piece)
    return "/".join(parts)


_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
EMU_PER_PIXEL = 9525


def _int(node, path: str) -> int:
    found = node.find(path) if node is not None else None
    return int(found.text or 0) if found is not None else 0


def xlsx_images(xlsx: bytes) -> list[dict[str, Any]]:
    """The xlsx export's floating images, one entry per picture:
    `{"tab", "row", "col", "row_off", "col_off", "width", "height"}`, with the
    anchor cell 0-based and offsets and size in pixels. Plain REST never
    returns floating images; the export carries each with its anchor, but not
    its alt text (spec, "Facts"). Charts sit in the same drawing parts as
    graphic frames and are skipped here; the API reports them."""
    z = zipfile.ZipFile(BytesIO(xlsx))
    workbook = "xl/workbook.xml"
    targets = _rels(z, workbook)
    images: list[dict[str, Any]] = []
    for sheet in ElementTree.fromstring(z.read(workbook)).iter(f"{_MAIN}sheet"):
        part = targets.get(sheet.get(_R_ID, ""))
        if not part:
            continue
        for target in _rels(z, part).values():
            if "drawings/drawing" not in target or target not in z.namelist():
                continue
            root = ElementTree.fromstring(z.read(target))
            for kind in ("oneCellAnchor", "twoCellAnchor", "absoluteAnchor"):
                for anchor in root.iter(f"{_XDR}{kind}"):
                    if anchor.find(f"{_XDR}pic") is None:
                        continue
                    start = anchor.find(f"{_XDR}from")
                    ext = anchor.find(f"{_XDR}ext")
                    if ext is None:   # a two-cell anchor sizes the picture in its shape
                        ext = anchor.find(f"{_XDR}pic/{_XDR}spPr/{_A}xfrm/{_A}ext")
                    cx = int(ext.get("cx", 0)) if ext is not None else 0
                    cy = int(ext.get("cy", 0)) if ext is not None else 0
                    images.append({
                        "tab": sheet.get("name"),
                        "row": _int(start, f"{_XDR}row"), "col": _int(start, f"{_XDR}col"),
                        "row_off": _int(start, f"{_XDR}rowOff") // EMU_PER_PIXEL,
                        "col_off": _int(start, f"{_XDR}colOff") // EMU_PER_PIXEL,
                        "width": cx // EMU_PER_PIXEL, "height": cy // EMU_PER_PIXEL})
    return images


def xlsx_threads(xlsx: bytes) -> list[dict[str, Any]]:
    """The xlsx export's threaded comments, one entry per thread:
    `{"tab", "cell", "text", "replies": [text...], "done"}`. The export
    carries each thread with its cell; Drive carries it with an opaque anchor,
    so a thread is placed by matching its text (spec, "Facts")."""
    z = zipfile.ZipFile(BytesIO(xlsx))
    workbook = "xl/workbook.xml"
    targets = _rels(z, workbook)
    threads: list[dict[str, Any]] = []
    for sheet in ElementTree.fromstring(z.read(workbook)).iter(f"{_MAIN}sheet"):
        part = targets.get(sheet.get(_R_ID, ""))
        if not part:
            continue
        for target in _rels(z, part).values():
            if "threadedComments/" not in target or target not in z.namelist():
                continue
            by_id: dict[str, dict[str, Any]] = {}
            for tc in ElementTree.fromstring(z.read(target)).iter(f"{_TC}threadedComment"):
                node = tc.find(f"{_TC}text")
                text = "".join(node.itertext()) if node is not None else ""
                parent = tc.get("parentId")
                if parent and parent in by_id:
                    by_id[parent]["replies"].append(text)
                    continue
                thread = {"tab": sheet.get("name"), "cell": tc.get("ref"), "text": text,
                          "replies": [], "done": tc.get("done") == "1"}
                by_id[tc.get("id", "")] = thread
                threads.append(thread)
    return threads

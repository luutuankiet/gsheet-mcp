# Copied from google-docs-mcp@2fb4652 src/<package>/pages.py; tab export swapped for a range export, line mapping and receipts dropped, state-store cache and per-spreadsheet lock added.
"""The page renderer: a range as Google prints it, as image blocks.

  export_pdf(spreadsheet_id, gid, rng)  -> (the range's PDF bytes, served from the cache?)
  page_count(pdf)                       -> pages in the PDF
  rasterise(pdf, page_numbers, quality) -> one ImageContent per page
  parse_pages("2-4")                    -> [2, 3, 4]

The PDF comes from the editor's export link (`sheets_api.export_url`), fetched
with the caller's bearer token. Repeated exports hit 429 (spec, "Facts"), so
(spec, `render_range`, "Rate limits"):

- each range's PDF is cached in the state store, per caller, spreadsheet,
  tab and range;
- renders of one spreadsheet run one at a time, cache lookup included, so a
  second identical render waits for the first and is then served from the
  cache instead of exporting again;
- a 429 gets one retry with a backoff, then a visible refusal
  (`sheets_api.download`).
"""
from __future__ import annotations

import base64
import io
import re
import threading

from mcp.types import ImageContent

from . import a1, auth, generation, sheets_api, state_store

MAX_PAGES = 6                  # pages rendered per call; the rest are listed

# Longest side of each page image, in pixels. `standard` on a landscape
# letter page is about 1,080 image tokens, the cost the spec quotes.
QUALITIES = {"minimal": 384, "low": 512, "standard": 1024, "high": 1568}
DEFAULT_QUALITY = "standard"

# (default, review) The spec gives no expiry for a render. Drive's
# modifiedTime does not move on Sheets writes, so, as for the scan, a time
# limit is the only safe one; this uses the scan's 10 minutes.
CACHE_TTL_SECONDS = 600

# (default, review) How the export lays the range out: fit to the page
# width, landscape, gridlines kept, no tab name, title or page numbers.
# These are the editor's File > Download > PDF options; the spec names none.
PRINT_OPTIONS = ("&size=letter&portrait=false&fitw=true&gridlines=true"
                 "&sheetnames=false&printtitle=false&pagenum=UNDEFINED")

# PDFium is not thread safe, and tools run in worker threads.
_PDFIUM = threading.Lock()

# One lock per spreadsheet. It serialises renders inside one server
# instance; Cloud Run instances do not share it, and the state store cache
# is what spares a second instance the export.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class RenderError(RuntimeError):
    """A page render could not be made; the message is what the agent sees."""


def spreadsheet_lock(spreadsheet_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(spreadsheet_id, threading.Lock())


def _cache_key(spreadsheet_id: str, gid: int, rng: a1.Range) -> str:
    # Per caller: one caller's render must never serve another, who may not
    # be allowed to open the spreadsheet.
    where = "" if rng.whole_tab else rng.a1
    # The generation changes on every write through our tools, hiding older renders.
    return (f"render|{auth.caller_id()}|{spreadsheet_id}|{generation.current(spreadsheet_id)}"
            f"|{gid}|{where}")


# ---- the export -------------------------------------------------------------


def export_pdf(spreadsheet_id: str, gid: int, rng: a1.Range) -> tuple[bytes, bool]:
    """The range's PDF, and whether it came from the cache. Call it holding
    `spreadsheet_lock(spreadsheet_id)`."""
    key = _cache_key(spreadsheet_id, gid, rng)
    store = state_store.current()
    cached = store.get_cached(key)
    if cached is not None:
        return cached, True
    url = sheets_api.export_url(spreadsheet_id, "pdf", gid=gid, range_=rng) + PRINT_OPTIONS
    try:
        data = sheets_api.download(url)
    except sheets_api.SheetsApiError as e:
        raise RenderError(str(e)) from e
    if not data.startswith(b"%PDF"):
        raise RenderError("Google's export did not return a PDF (it may have sent a sign-in "
                          "page instead); the caller may not be able to open this spreadsheet")
    store.put_cached(key, data, ttl_seconds=CACHE_TTL_SECONDS)
    return data, False


# ---- images -----------------------------------------------------------------


def page_count(pdf: bytes) -> int:
    import pypdfium2 as pdfium

    with _PDFIUM:
        doc = pdfium.PdfDocument(pdf)
        try:
            return len(doc)
        finally:
            doc.close()


def rasterise(pdf: bytes, page_numbers: list[int], quality: str) -> list[ImageContent]:
    """PNG image blocks, the longest side QUALITIES[quality] pixels, in the order given."""
    import pypdfium2 as pdfium

    longest = QUALITIES[quality]
    out: list[ImageContent] = []
    with _PDFIUM:
        doc = pdfium.PdfDocument(pdf)
        try:
            for n in page_numbers:
                page = doc[n - 1]
                width, height = page.get_size()
                picture = page.render(scale=longest / max(width, height)).to_pil()
                buffer = io.BytesIO()
                picture.convert("RGB").save(buffer, format="PNG", optimize=True)
                out.append(ImageContent(type="image", mime_type="image/png",
                                        data=base64.b64encode(buffer.getvalue()).decode()))
        finally:
            doc.close()
    return out


def parse_pages(pages: str | int) -> list[int]:
    """`3`, `2-4` or `1,3,5` as page numbers, in order, once each."""
    out: list[int] = []
    for part in str(pages).split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not m:
            raise ValueError(f'pages is a page ("3"), a range ("2-4") or a list ("1,3"), '
                             f"not {pages!r}")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        if first < 1 or last < first:
            raise ValueError(f"pages {part.strip()} is not a forward range of pages from 1")
        out += [p for p in range(first, last + 1) if p not in out]
    return out

# Copied from google-docs-mcp@2fb4652 src/<package>/tools/render_pages.py; a doc tab swapped for a spreadsheet range, lines and revision dropped.
"""render_range: a range as Google prints it, as images."""
from __future__ import annotations

from typing import Literal

from fastmcp.tools import ToolResult

from .. import addressing, reply, sheets_api
from .. import pages as page_renderer  # `pages` is render_range's argument
from ..app import READ, tool


@tool(READ)
def render_range(spreadsheet: str, range: str | None = None,  # noqa: A002 - the spec's word
                 pages: str | int | None = None,
                 quality: Literal["minimal", "low", "standard", "high"] = "standard"
                 ) -> ToolResult:
    """See a range as Google prints it, as page images: colours, merges, links,
    images, checkboxes and charts. Opt-in; read_range is cheaper for the content.

    Loses dropdown pill colours, partial star ratings and frozen panes. Each
    page costs about 1,080 image tokens at `standard`. Renders are cached for
    10 minutes, so a repeat call costs no export.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      range: a tab plus an A1 range, `'table for comms'!B2:F8`, or a whole tab,
        `'table for comms'`. Left out, the first tab.
      pages: a page (`"3"`), a range (`"2-4"`) or a list (`"1,3"`); at most 6.
        Left out, the first 6 pages.
      quality: longest side of each page: `minimal` 384 px, `low` 512,
        `standard` 1024 (default), `high` 1568.

    Returns:
      One header line (the range, page count, quality, whether it came from
      the cache), then an image block per page. A 429 from Google is retried
      once, then refused.
    """
    if quality not in page_renderer.QUALITIES:
        raise reply.Refusal(f'quality is "minimal", "low", "standard" or "high", not '
                            f"{quality!r}")
    wanted = page_renderer.parse_pages(pages) if pages is not None else None
    if wanted is not None and len(wanted) > page_renderer.MAX_PAGES:
        raise reply.Refusal(f"at most {page_renderer.MAX_PAGES} pages per call, not "
                            f"{len(wanted)}; split them over several calls")
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    rng = addressing.resolve(spreadsheet_id, range)

    with page_renderer.spreadsheet_lock(spreadsheet_id):
        try:
            props = sheets_api.find_tab(sheets_api.get_tabs(spreadsheet_id), rng.tab)
            rng = rng.with_tab(props["title"])
            pdf, cached = page_renderer.export_pdf(spreadsheet_id, props["sheetId"], rng)
        except (sheets_api.SheetsApiError, page_renderer.RenderError) as e:
            raise reply.Refusal(f"Cannot render {rng}: {e}") from None
        total = page_renderer.page_count(pdf)
        if wanted is not None:
            past = [p for p in wanted if p > total]
            if past:
                raise reply.Refusal(f"{rng} prints on {total} "
                                    f"{'page' if total == 1 else 'pages'}, so no page "
                                    f"{', '.join(map(str, past))}")
            chosen = wanted
        else:
            chosen = list(range_of(total))
        images = page_renderer.rasterise(pdf, chosen, quality)

    header = (f"{rng} · {total} {'page' if total == 1 else 'pages'} · {quality} "
              f"({page_renderer.QUALITIES[quality]} px)"
              + (" · from cache" if cached else ""))
    if wanted is None and total > page_renderer.MAX_PAGES:
        header += (f"\nshowing pages 1-{page_renderer.MAX_PAGES}; ask with "
                   f'pages="{page_renderer.MAX_PAGES + 1}-'
                   f'{min(total, 2 * page_renderer.MAX_PAGES)}" for more')
    return reply.text(header, extra=list(images))


def range_of(total: int) -> range:
    """The default pages, 1 up to MAX_PAGES. Module level, because inside
    render_range the name `range` is the argument."""
    return range(1, min(total, page_renderer.MAX_PAGES) + 1)

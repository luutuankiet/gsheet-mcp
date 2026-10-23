"""`render_range` through the tool layer, against the fake Sheets API's PDF export."""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from itertools import pairwise

from gsheet_mcp import pages, sheets_api
from tests.conftest import call

PNG = b"\x89PNG\r\n\x1a\n"


async def test_the_range_comes_back_as_an_image_block_never_base64_text(fake, signed_in,
                                                                        client):
    result = await client.call_tool("render_range",
                                    {"spreadsheet": "canary", "range": "comms!B2:F8"},
                                    raise_on_error=False)
    assert not result.is_error
    assert result.structured_content is None
    texts = [b for b in result.content if b.type == "text"]
    images = [b for b in result.content if b.type == "image"]
    assert [t.text for t in texts] == ["comms!B2:F8 · 1 page · standard (1024 px)"]
    assert len(images) == 1 and images[0].mime_type == "image/png"
    assert base64.b64decode(images[0].data).startswith(PNG)
    assert images[0].data not in texts[0].text
    assert fake.downloads == [
        "https://docs.google.com/spreadsheets/d/canary/export?format=pdf&gid=101"
        "&range=B2:F8" + pages.PRINT_OPTIONS]


async def test_a_second_identical_call_is_served_from_the_cache(fake, signed_in, client, store):
    first, _ = await call(client, "render_range", spreadsheet="canary", range="comms!B2:F8")
    second, is_error = await call(client, "render_range", spreadsheet="canary",
                                  range="comms!B2:F8")
    assert not is_error
    assert "from cache" not in first and second.endswith(" · from cache")
    assert fake.calls.count("export") == 1
    assert len(store.cached) == 1
    # Another range is another export.
    await call(client, "render_range", spreadsheet="canary", range="comms!B2:C3")
    assert fake.calls.count("export") == 2


async def test_the_cache_is_per_caller(fake, signed_in, client, store, monkeypatch):
    await call(client, "render_range", spreadsheet="canary", range="comms!B2:F8")
    monkeypatch.setattr(pages.auth, "caller_id", lambda: "someone-else")
    text, _ = await call(client, "render_range", spreadsheet="canary", range="comms!B2:F8")
    assert "from cache" not in text
    assert fake.calls.count("export") == 2


async def test_two_concurrent_renders_on_one_spreadsheet_run_one_after_the_other(
        fake, signed_in, client, monkeypatch):
    """Each render's export and rasterising form one span; two spans on one
    spreadsheet never overlap. Without the per-spreadsheet lock both renders
    would look up the cache before either stored, and export twice."""
    spans: list[tuple[float, float]] = []
    inside = threading.local()
    real_export, real_rasterise = pages.export_pdf, pages.rasterise

    def export_pdf(*args):
        inside.start = time.monotonic()
        time.sleep(0.05)            # a slow export widens any overlap
        return real_export(*args)

    def rasterise(*args):
        out = real_rasterise(*args)
        spans.append((inside.start, time.monotonic()))
        return out

    monkeypatch.setattr(pages, "export_pdf", export_pdf)
    monkeypatch.setattr(pages, "rasterise", rasterise)
    results = await asyncio.gather(
        call(client, "render_range", spreadsheet="canary", range="comms!B2:F8"),
        call(client, "render_range", spreadsheet="canary", range="comms!B2:F8"),
        call(client, "render_range", spreadsheet="canary", range="dashboard!A1:D9"))
    assert not any(is_error for _, is_error in results)
    spans.sort()
    assert len(spans) == 3
    assert all(end <= next_start for (_, end), (next_start, _) in pairwise(spans)), \
        f"renders of one spreadsheet overlapped: {spans}"
    # The second identical render waited for the first, then hit the cache.
    assert fake.calls.count("export") == 2
    assert sum(text.endswith("from cache") for text, _ in results) == 1


async def test_a_429_is_retried_once_then_the_render_goes_through(fake, signed_in, client,
                                                                  offline):
    fake.rate_limit(1)
    text, is_error = await call(client, "render_range", spreadsheet="canary",
                                range="comms!B2:F8")
    assert not is_error
    assert offline == [sheets_api.RETRY_BACKOFF_SECONDS]


async def test_a_second_429_becomes_a_visible_refusal(fake, signed_in, client, store,
                                                     offline, monkeypatch):
    real_download = fake.download

    def always_429(url, timeout):
        fake.rate_limit(1)          # only the export answers 429, not the tab lookup
        return real_download(url, timeout)

    monkeypatch.setattr(sheets_api, "_download", always_429)
    text, is_error = await call(client, "render_range", spreadsheet="canary",
                                range="comms!B2:F8")
    assert is_error
    assert "retried once" in text
    assert fake.calls.count("export") == 2          # one retry, no more
    assert offline == [sheets_api.RETRY_BACKOFF_SECONDS]
    assert not store.cached                          # a refusal is never cached


async def test_a_whole_tab_and_a_page_past_the_end(fake, signed_in, client):
    fake.pdf_pages = 2
    text, is_error = await call(client, "render_range", spreadsheet="canary",
                                range="'dashboard'", pages="3")
    assert is_error
    assert text == "'dashboard' prints on 2 pages, so no page 3"
    assert fake.downloads[-1].endswith("format=pdf&gid=102" + pages.PRINT_OPTIONS)


async def test_bad_input_is_refused_before_any_export(fake, signed_in, client):
    for args, says in [({"range": "no such tab!A1"}, "No tab 'no such tab'"),
                       ({"pages": "1-7"}, "at most 6 pages"),
                       ({"quality": "huge"}, "quality")]:
        result = await client.call_tool("render_range", {"spreadsheet": "canary", **args},
                                        raise_on_error=False)
        assert result.is_error
        assert says in result.content[0].text
    assert "export" not in fake.calls

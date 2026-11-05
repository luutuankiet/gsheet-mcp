"""sheet_images through the tool layer, with `scripts.run` faked (tests/fake_script.py)."""
from __future__ import annotations

import base64
import io
import os

import pytest
from PIL import Image

from gsheet_mcp import apps_script, generation, images
from tests.conftest import call
from tests.fake_script import FakeScript

SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg"
SCRIPT_ID = "test-script-id"


def _png(width: int = 4, height: int = 3, noise: bool = False, fmt: str = "PNG") -> bytes:
    if noise:
        image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        image = Image.new("RGB", (width, height), (200, 30, 30))
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, signed_in) -> FakeScript:
    fake = FakeScript()
    monkeypatch.setenv(apps_script.ENV, SCRIPT_ID)
    monkeypatch.setattr(apps_script, "_post", fake.post)
    return fake


async def _images(client, **arguments) -> tuple[str, bool]:
    return await call(client, "sheet_images", spreadsheet=SHEET, **arguments)


# ---- each action's request shape -------------------------------------------------


async def test_insert_sends_the_image_and_a_fresh_tag(script, client):
    data = _png()
    text, is_error = await _images(client, action="insert", cell="'Evidence'!C4",
                                   image_base64=_b64(data), width_px=120, alt="row 1")
    assert not is_error, text
    assert script.urls == [f"https://script.googleapis.com/v1/scripts/{SCRIPT_ID}:run"]
    assert script.tokens == ["caller-secret-token"]
    (function, params), = script.calls
    assert function == "insertImage"
    ss, tab, cell, b64, mime, w, h, tag, ox, oy, alt = params
    assert (ss, tab, cell, mime, w, h, ox, oy, alt) == (
        SHEET, "Evidence", "C4", "image/png", 120, None, 0, 0, "row 1")
    assert base64.b64decode(b64) == data
    assert tag.startswith("gsheet-mcp:") and len(tag) == len("gsheet-mcp:") + 12
    assert text.startswith(f"Inserted {tag} · Evidence!C4 · 120x80 px")
    assert "alt 'row 1'" in text


async def test_insert_takes_the_tab_from_tab_or_the_first_tab(script, client):
    await _images(client, action="insert", cell="B2", tab="Notes", image_base64=_b64(_png()))
    await _images(client, action="insert", cell="B2", image_base64=_b64(_png()))
    assert [c[1][1] for c in script.calls] == ["Notes", None]


async def test_insert_refuses_a_range_or_two_tabs(script, client):
    text, is_error = await _images(client, action="insert", cell="'Evidence'!C4:D5",
                                   image_base64=_b64(_png()))
    assert is_error and "one cell" in text
    text, is_error = await _images(client, action="insert", cell="'Evidence'!C4", tab="Notes",
                                   image_base64=_b64(_png()))
    assert is_error and "pass one" in text
    assert script.calls == []


async def test_insert_forgets_the_scan_and_renders(script, client, monkeypatch):
    bumped = []
    monkeypatch.setattr(generation, "bump", bumped.append)
    await _images(client, action="insert", cell="'Evidence'!C4", image_base64=_b64(_png()))
    assert bumped == [SHEET]


async def test_list_passes_ids_through_tagged_and_derived(script, client):
    await _images(client, action="insert", cell="'Evidence'!C4", image_base64=_b64(_png()))
    script.add_foreign("Notes", "A1")
    text, is_error = await _images(client, action="list")
    assert not is_error, text
    lines = text.splitlines()
    assert lines[0] == "2 floating images on the spreadsheet:"
    assert lines[1].startswith("gsheet-mcp:") and "Evidence!C4" in lines[1]
    assert lines[2] == "pos:7:0 · Notes!A1 · 50x50 px"
    assert script.calls[-1] == ("listImages", [SHEET, None])

    text, _ = await _images(client, action="list", tab="Notes")
    assert script.calls[-1] == ("listImages", [SHEET, "Notes"])
    assert text.splitlines()[0] == "1 floating image on tab 'Notes':"


async def test_list_says_when_there_are_none(script, client):
    text, is_error = await _images(client, action="list", tab="Evidence")
    assert (text, is_error) == ("No floating images on tab 'Evidence'.", False)


async def test_move_reanchors_and_resizes(script, client):
    script.add_foreign("Evidence", "A1")
    text, is_error = await _images(client, action="move", id="pos:0:0", cell="'Evidence'!D9",
                                   width_px=180, height_px=90, offset_x=5, offset_y=5)
    assert not is_error, text
    assert script.calls[-1] == ("moveImage", [SHEET, "Evidence", "pos:0:0", "D9", 180, 90, 5, 5])
    assert text == "Moved pos:0:0 · Evidence!D9 · 180x90 px · offset 5,5"


async def test_move_needs_something_to_change(script, client):
    text, is_error = await _images(client, action="move", id="pos:0:0")
    assert is_error and "new cell, a size or offsets" in text
    text, is_error = await _images(client, action="move", cell="D9")
    assert is_error and "needs the image's id" in text
    assert script.calls == []


async def test_delete_previews_by_default_and_removes_with_dry_run_false(script, client):
    script.add_foreign("Evidence", "A1")
    text, is_error = await _images(client, action="delete", id="pos:0:0")
    assert not is_error, text
    assert text.startswith("Would delete pos:0:0 · Evidence!A1")
    assert "dry_run=false" in text
    assert [c[0] for c in script.calls] == ["listImages"]
    assert script.images["Evidence"]

    text, is_error = await _images(client, action="delete", id="pos:0:0", dry_run=False)
    assert not is_error, text
    assert text == "Deleted pos:0:0 · Evidence!A1 · 50x50 px"
    assert script.calls[-1] == ("deleteImage", [SHEET, None, "pos:0:0"])
    assert script.images["Evidence"] == []


async def test_delete_preview_refuses_an_unknown_id(script, client):
    text, is_error = await _images(client, action="delete", id="gsheet-mcp:000000000000")
    assert is_error and "No image gsheet-mcp:000000000000" in text


async def test_dry_run_insert_checks_the_image_without_calling(script, client):
    text, is_error = await _images(client, action="insert", cell="C4",
                                   image_base64=_b64(_png(10, 5)), dry_run=True)
    assert not is_error, text
    assert text.startswith("Would insert a 10x5 px image/png")
    assert "C4 on the first tab" in text
    assert script.calls == []


# ---- the bytes ----------------------------------------------------------------------


@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                           ("GIF", "image/gif")])
async def test_type_is_sniffed_from_magic_bytes(script, client, fmt, mime):
    await _images(client, action="insert", cell="C4", image_base64=_b64(_png(fmt=fmt)))
    assert script.calls[-1][1][4] == mime


async def test_unknown_type_is_refused(script, client):
    text, is_error = await _images(client, action="insert", cell="C4",
                                   image_base64=_b64(b"RIFF....WEBPVP8 "))
    assert is_error and "Not a PNG, JPEG or GIF" in text
    assert script.calls == []


@pytest.mark.parametrize("bad", ["not base64!!", "abc", ""])
async def test_bad_base64_is_refused(script, client, bad):
    text, is_error = await _images(client, action="insert", cell="C4", image_base64=bad)
    assert is_error
    assert "base64" in text or "needs cell and image_base64" in text
    assert script.calls == []


def test_base64_allows_whitespace_and_a_data_url():
    data = _png()
    wrapped = "data:image/png;base64," + "\n".join(_b64(data)[i:i + 10] for i in range(0, 40, 10))
    assert images.decode(wrapped + _b64(data)[40:]) == data


def test_a_small_image_goes_through_untouched():
    data = _png(1024, 1024)
    prepared = images.prepare(_b64(data))
    assert (prepared.data, prepared.warning) == (data, None)


def test_over_the_pixel_limit_is_downscaled():
    prepared = images.prepare(_b64(_png(1025, 1024)))
    assert prepared.width * prepared.height <= images.MAX_PIXELS
    assert prepared.mime == "image/png"
    assert "downscaled from 1025x1024 px" in prepared.warning


def test_over_the_byte_limit_is_downscaled():
    data = _png(900, 900, noise=True)
    assert len(data) > images.MAX_BYTES and 900 * 900 <= images.MAX_PIXELS
    prepared = images.prepare(_b64(data))
    assert len(prepared.data) <= images.MAX_BYTES
    assert prepared.width < 900 and prepared.mime == "image/png"
    with Image.open(io.BytesIO(prepared.data)) as check:
        assert check.size == (prepared.width, prepared.height)


def test_a_gif_over_the_limit_becomes_a_png_of_its_first_frame():
    prepared = images.prepare(_b64(_png(1200, 1000, fmt="GIF")))
    assert prepared.mime == "image/png" and prepared.data.startswith(b"\x89PNG")
    assert "as PNG" in prepared.warning


def test_a_png_that_cannot_shrink_enough_becomes_a_jpeg(monkeypatch):
    monkeypatch.setattr(images, "_PNG_TRIES", 1)
    prepared = images.prepare(_b64(_png(1000, 1000, noise=True)))
    assert prepared.mime == "image/jpeg" and len(prepared.data) <= images.MAX_BYTES
    assert "as JPEG" in prepared.warning


async def test_downscale_warning_reaches_the_reply(script, client):
    text, is_error = await _images(client, action="insert", cell="C4",
                                   image_base64=_b64(_png(1025, 1024)))
    assert not is_error, text
    assert text.splitlines()[-1].startswith("warning: downscaled from 1025x1024 px")
    sent = base64.b64decode(script.calls[-1][1][3])
    with Image.open(io.BytesIO(sent)) as check:
        assert check.size[0] * check.size[1] <= images.MAX_PIXELS


# ---- errors -------------------------------------------------------------------------


async def test_not_configured_without_the_script_id(signed_in, client, monkeypatch):
    monkeypatch.delenv(apps_script.ENV, raising=False)
    text, is_error = await _images(client, action="list")
    assert is_error and text == apps_script.NOT_CONFIGURED


async def test_script_exception_is_the_refusal(script, client):
    text, is_error = await _images(client, action="move", id="gsheet-mcp:nope", cell="C4")
    assert is_error
    assert text == "Cannot move the image: No image gsheet-mcp:nope"


async def test_http_404_names_the_likely_cause(script, client):
    script.reply = (404, {"error": {"code": 404, "message": "Requested entity was not found."}})
    text, is_error = await _images(client, action="list")
    assert is_error
    assert text.startswith("Cannot list the image: Apps Script API 404: Requested entity")
    assert apps_script.ENV in text


async def test_unknown_action_is_refused(script, client):
    text, is_error = await _images(client, action="resize", id="x")
    assert is_error
    assert script.calls == []


async def test_description_carries_the_mcp_piper_lines(client):
    tools = {t.name: t for t in await client.list_tools()}
    assert ('image_base64: PNG, JPEG or GIF bytes as base64. With mcp-piper, pass '
            '"@file64:/abs/path.png".') in tools["sheet_images"].description
    assert "under\n    about 3 MB" in tools["sheet_images"].description
    assert ('With mcp-piper, add "_save_to": "/abs/dir/" to save the image to disk instead of '
            'reading it. Ask for the smallest size when you only need to check layout.'
            ) in tools["render_range"].description

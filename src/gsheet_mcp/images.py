"""Floating images: the bytes checks before `scripts.run`, and the four actions.

The Sheets REST API can neither create, list nor move a floating (over-grid)
image, so every action runs a function of the server's Apps Script project
(`apps_script/Code.js`) through `apps_script.run`. Every check that can refuse
an image runs here first, so a refusal never costs a round trip.

Apps Script's `insertImage` refuses a blob over 2 MiB or over 1,048,576 pixels
(measured; both exact). Above either, the image is downscaled with Pillow and the
reply carries a warning.
"""
from __future__ import annotations

import base64
import binascii
import io
import math
import re
import secrets
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from . import a1, apps_script, reply

MAX_BYTES = 2_097_152
MAX_PIXELS = 1_048_576
TAG_PREFIX = "gsheet-mcp:"

PNG, JPEG, GIF = "image/png", "image/jpeg", "image/gif"
_MAGIC = ((b"\x89PNG", PNG), (b"\xff\xd8\xff", JPEG), (b"GIF8", GIF))
_PIL_FORMAT = {PNG: "PNG", JPEG: "JPEG"}
_DATA_URL = re.compile(r"^data:[\w/+.-]+;base64,", re.I)
_SHRINK = 0.8
_PNG_TRIES = 6        # PNG down to about a quarter of the side, then JPEG
_JPEG_TRIES = 12
_JPEG_QUALITY = 85


@dataclass(frozen=True)
class Prepared:
    data: bytes
    mime: str
    width: int
    height: int
    warning: str | None = None

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


def decode(image_base64: str) -> bytes:
    """Strict base64; whitespace and a `data:...;base64,` prefix are allowed."""
    text = _DATA_URL.sub("", (image_base64 or "").strip())
    text = re.sub(r"\s+", "", text)
    if not text:
        raise reply.Refusal("image_base64 is empty.")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise reply.Refusal(
            "image_base64 is not valid base64. With mcp-piper, pass "
            '"@file64:/abs/path.png" and the file is encoded for you.') from None


def sniff(data: bytes) -> str:
    """The MIME type from the magic bytes; anything but PNG, JPEG or GIF is refused."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    raise reply.Refusal("Not a PNG, JPEG or GIF image (checked by its first bytes); "
                        "convert it to PNG first.")


def prepare(image_base64: str) -> Prepared:
    """Decode, sniff and, above Apps Script's limits, downscale."""
    data = decode(image_base64)
    mime = sniff(data)
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise reply.Refusal(f"Cannot read the image: {e}") from None
    width, height = image.size
    if len(data) <= MAX_BYTES and width * height <= MAX_PIXELS:
        return Prepared(data, mime, width, height)
    return _downscale(image, data, mime)


def _encode(image: Image.Image, mime: str) -> bytes:
    out = io.BytesIO()
    if mime == JPEG:
        if image.mode not in ("RGB", "L"):
            image = _flatten(image)
        image.save(out, "JPEG", quality=_JPEG_QUALITY, optimize=True)
    else:
        image.save(out, "PNG", optimize=True)
    return out.getvalue()


def _flatten(image: Image.Image) -> Image.Image:
    """On white, for JPEG, which has no transparency."""
    rgba = image.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    return flat


def _downscale(image: Image.Image, original: bytes, mime: str) -> Prepared:
    """Shrink until both limits hold: PNG stays PNG and JPEG stays JPEG; a GIF
    becomes a PNG of its first frame; a PNG that cannot fit becomes a JPEG."""
    width, height = image.size
    if mime == GIF:
        image.seek(0)
        image = image.convert("RGBA")
        target = PNG
    else:
        target = mime
    if image.mode == "P":
        image = image.convert("RGBA")
    start = min(1.0, math.sqrt(MAX_PIXELS / (width * height)))
    attempts = [(target, _PNG_TRIES if target == PNG else _JPEG_TRIES)]
    if target == PNG:
        attempts.append((JPEG, _JPEG_TRIES))
    for out_mime, tries in attempts:
        scale = start
        for _ in range(tries):
            w = max(1, math.floor(width * scale))
            h = max(1, math.floor(height * scale))
            if w * h <= MAX_PIXELS:
                data = _encode(image.resize((w, h), Image.Resampling.LANCZOS), out_mime)
                if len(data) <= MAX_BYTES:
                    became = "" if out_mime == mime else f", as {_PIL_FORMAT[out_mime]}"
                    warning = (f"warning: downscaled from {width}x{height} px "
                               f"({len(original):,} bytes) to {w}x{h} px ({len(data):,} "
                               f"bytes{became}); Sheets takes at most {MAX_BYTES:,} bytes "
                               f"and {MAX_PIXELS:,} pixels")
                    return Prepared(data, out_mime, w, h, warning)
            scale *= _SHRINK
    raise reply.Refusal(f"Cannot shrink the {width}x{height} px image under {MAX_BYTES:,} "
                        "bytes; crop it or save it as JPEG first.")


def new_tag() -> str:
    return TAG_PREFIX + secrets.token_hex(6)


# ---- addressing ---------------------------------------------------------------


def one_cell(cell: str, tab: str | None) -> tuple[str | None, str]:
    """(tab or None, A1 of one cell) from `'Evidence'!C4` or `C4` plus `tab`."""
    try:
        rng = a1.parse(cell)
    except a1.A1Error as e:
        raise reply.Refusal(f"cell: {e}") from None
    single = (rng.start_row is not None and rng.start_col is not None
              and rng.end_row == rng.start_row + 1 and rng.end_col == rng.start_col + 1)
    if not single:
        raise reply.Refusal(f"cell must be one cell such as 'Evidence'!C4, not {cell!r}.")
    if rng.tab is not None and tab is not None and rng.tab != tab:
        raise reply.Refusal(f"cell names tab {rng.tab!r} but tab is {tab!r}; pass one.")
    return rng.tab if rng.tab is not None else tab, rng.a1


def where(record: dict[str, Any]) -> str:
    return f"{a1.quote_tab(record.get('sheet') or '')}!{record.get('cell')}"


def describe(record: dict[str, Any]) -> str:
    """One line per image: id, anchor, size, offsets, alt text."""
    line = (f"{record.get('id')} · {where(record)} · {record.get('widthPx')}x"
            f"{record.get('heightPx')} px")
    if record.get("offsetX") or record.get("offsetY"):
        line += f" · offset {record.get('offsetX') or 0},{record.get('offsetY') or 0}"
    if record.get("altTitle"):
        line += f" · alt {record['altTitle']!r}"
    return line


# ---- the four actions ---------------------------------------------------------


def _run(action: str, function: str, parameters: list[Any]) -> Any:
    try:
        return apps_script.run(function, parameters)
    except apps_script.ScriptError as e:
        raise reply.Refusal(f"Cannot {action} the image: {e}") from None


def insert(spreadsheet_id: str, tab: str | None, cell: str, prepared: Prepared,
           width_px: int | None, height_px: int | None, offset_x: int | None,
           offset_y: int | None, alt: str | None) -> dict[str, Any]:
    tag = new_tag()
    return _run("insert", "insertImage", [
        spreadsheet_id, tab, cell, prepared.base64, prepared.mime, width_px, height_px, tag,
        offset_x or 0, offset_y or 0, alt or None])


def list_images(spreadsheet_id: str, tab: str | None) -> list[dict[str, Any]]:
    return _run("list", "listImages", [spreadsheet_id, tab]) or []


def move(spreadsheet_id: str, tab: str | None, image_id: str, cell: str | None,
         width_px: int | None, height_px: int | None, offset_x: int | None,
         offset_y: int | None) -> dict[str, Any]:
    return _run("move", "moveImage", [spreadsheet_id, tab, image_id, cell, width_px,
                                      height_px, offset_x, offset_y])


def delete(spreadsheet_id: str, tab: str | None, image_id: str) -> dict[str, Any]:
    return _run("delete", "deleteImage", [spreadsheet_id, tab, image_id])

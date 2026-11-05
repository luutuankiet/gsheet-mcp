"""sheet_images: floating images anchored to a cell; insert, list, move, delete."""
from __future__ import annotations

from typing import Literal

from fastmcp.tools import ToolResult

from .. import images, reply, sheets_api, writes
from ..app import EDIT, tool

Action = Literal["insert", "list", "move", "delete"]


def _size(name: str, value: int | None, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise reply.Refusal(f"{name} is a whole number of pixels, at least {minimum}.")
    return value


@tool(EDIT)
def sheet_images(spreadsheet: str, action: Action, cell: str | None = None,
                 image_base64: str | None = None, id: str | None = None,  # noqa: A002
                 tab: str | None = None, width_px: int | None = None,
                 height_px: int | None = None, offset_x: int | None = None,
                 offset_y: int | None = None, alt: str | None = None,
                 dry_run: bool | None = None) -> ToolResult:
    """Floating images over the grid, anchored to a cell: put a screenshot or
    chart beside a row, then list, move or delete images. Send each file with
    mcp-piper so its bytes never enter your context, and keep files under
    about 3 MB: the server refuses a request over 4 MiB (HTTP 413) before this
    tool runs. Over 2 MiB or 1,048,576 pixels, the server downscales the image
    and says so. A small insert takes about 4 seconds.

    Every image this tool inserts gets an id `gsheet-mcp:<token>`, stored in its
    alt-text description, that lasts. Images inserted any other way get an id
    `pos:<tabId>:<index>` from their position, good only until images on that
    tab are added or removed: list again before using one.

    Actions:
      insert: `cell` and `image_base64`; optional `width_px` / `height_px`
        (one side keeps the aspect ratio; neither keeps the image's own size),
        `offset_x` / `offset_y` inside the cell, `alt` (alt-text title).
      list: every image, or only `tab`'s.
      move: `id`, and any of a new `cell` (same tab), size or offsets.
      delete: `id`. Previews by default; dry_run=false removes it.

    Args:
      spreadsheet: the spreadsheet's URL or id.
      action: insert, list, move or delete.
      cell: one cell with its tab, `'Evidence'!C4`; without a tab, `tab` or the
        first tab.
      image_base64: PNG, JPEG or GIF bytes as base64. With mcp-piper, pass "@file64:/abs/path.png".
      id: the image's id from insert or list.
      tab: a tab name; narrows list, and names the tab for a bare `cell` or an id.
      width_px: width in pixels.
      height_px: height in pixels.
      offset_x: pixels right of the cell's left edge.
      offset_y: pixels below the cell's top edge.
      alt: alt-text title for the inserted image.
      dry_run: delete previews unless false. For insert and move, true checks
        the arguments (and the image) without changing the sheet.

    Returns:
      One line per image: id, anchor cell, size in pixels, offsets and alt
      text; insert adds a warning line when the image was downscaled.
    """
    spreadsheet_id = sheets_api.spreadsheet_id_from_url(spreadsheet)
    width_px = _size("width_px", width_px, 1)
    height_px = _size("height_px", height_px, 1)
    offset_x = _size("offset_x", offset_x, 0)
    offset_y = _size("offset_y", offset_y, 0)

    if action == "list":
        found = images.list_images(spreadsheet_id, tab)
        where = f"tab {tab!r}" if tab else "the spreadsheet"
        if not found:
            return reply.text(f"No floating images on {where}.")
        head = f"{len(found)} floating {'image' if len(found) == 1 else 'images'} on {where}:"
        return reply.text("\n".join([head] + [images.describe(r) for r in found]))

    if action == "insert":
        if not cell or not image_base64:
            raise reply.Refusal("insert needs cell and image_base64.")
        if id is not None:
            raise reply.Refusal("insert makes a new id; leave id out.")
        tab_name, a1_cell = images.one_cell(cell, tab)
        prepared = images.prepare(image_base64)
        if dry_run:
            at = (images.where({"sheet": tab_name, "cell": a1_cell}) if tab_name
                  else f"{a1_cell} on the first tab")
            lines = [f"Would insert a {prepared.width}x{prepared.height} px "
                     f"{prepared.mime} ({len(prepared.data):,} bytes) at {at}."]
            return reply.text("\n".join(lines + ([prepared.warning] if prepared.warning else [])))
        record = images.insert(spreadsheet_id, tab_name, a1_cell, prepared, width_px,
                               height_px, offset_x, offset_y, alt)
        writes.after_write(spreadsheet_id)
        lines = [f"Inserted {images.describe(record)}"]
        return reply.text("\n".join(lines + ([prepared.warning] if prepared.warning else [])))

    if not id:
        raise reply.Refusal(f"{action} needs the image's id, from insert or list.")
    if image_base64 is not None:
        raise reply.Refusal(f"{action} takes no image_base64; delete and insert to replace "
                            "an image.")

    if action == "move":
        tab_name, a1_cell = images.one_cell(cell, tab) if cell else (tab, None)
        if a1_cell is None and width_px is None and height_px is None \
                and offset_x is None and offset_y is None:
            raise reply.Refusal("move needs a new cell, a size or offsets.")
        if dry_run:
            return reply.text(f"Would move {id}" + (f" to {cell}" if cell else "") + ".")
        record = images.move(spreadsheet_id, tab_name, id, a1_cell, width_px, height_px,
                             offset_x, offset_y)
        writes.after_write(spreadsheet_id)
        return reply.text(f"Moved {images.describe(record)}")

    if action == "delete":
        if dry_run is not False:
            found = [r for r in images.list_images(spreadsheet_id, tab) if r.get("id") == id]
            if not found:
                raise reply.Refusal(f"No image {id}" + (f" on tab {tab!r}" if tab else "")
                                    + "; list the images again.")
            if len(found) > 1:
                raise reply.Refusal(f"Image id {id} matches {len(found)} images; name the tab.")
            return reply.text(f"Would delete {images.describe(found[0])}\n"
                              "Call again with dry_run=false to delete it.")
        record = images.delete(spreadsheet_id, tab, id)
        writes.after_write(spreadsheet_id)
        return reply.text(f"Deleted {images.describe(record)}")

    raise reply.Refusal(f'action is "insert", "list", "move" or "delete", not {action!r}.')

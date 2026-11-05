"""A fake Apps Script API: what `scripts.run` would answer for apps_script/Code.js.

Install it over `apps_script._post`. It keeps floating images per tab in memory
and follows Code.js's contract: an image's id is its `gsheet-mcp:` alt-text tag,
else `pos:<sheetId>:<index>`; errors come back as HTTP 200 with an `error`, as
`scripts.run` returns a script's exception.
"""
from __future__ import annotations

import json
from typing import Any

TABS = {"Evidence": 0, "Notes": 7}


class FakeScript:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any]]] = []
        self.urls: list[str] = []
        self.tokens: list[str] = []
        self.images: dict[str, list[dict[str, Any]]] = {tab: [] for tab in TABS}
        self.reply: tuple[int, dict[str, Any]] | None = None   # forced next answer

    # ---- the seam ----
    def post(self, url: str, body: bytes, token: str) -> tuple[int, dict[str, Any]]:
        request = json.loads(body)
        self.urls.append(url)
        self.tokens.append(token)
        self.calls.append((request["function"], request["parameters"]))
        if self.reply is not None:
            forced, self.reply = self.reply, None
            return forced
        try:
            result = getattr(self, request["function"])(*request["parameters"])
        except LookupError as e:
            return 200, {"error": {"code": 3, "message": "ScriptError", "details": [{
                "@type": "type.googleapis.com/google.apps.script.v1.ExecutionError",
                "errorMessage": f"Exception: {e.args[0]}", "errorType": "USER_ERROR"}]}}
        return 200, {"done": True, "response": {"result": result}}

    # ---- Code.js, in Python ----
    def _tab(self, name: str | None) -> str:
        if name is None:
            return next(iter(TABS))
        if name not in TABS:
            raise LookupError(f"No tab named {name}")
        return name

    def _record(self, tab: str, index: int) -> dict[str, Any]:
        image = self.images[tab][index]
        tag = image["altDescription"] or ""
        return {**image, "index": index, "sheet": tab, "sheetId": TABS[tab],
                "id": tag if tag.startswith("gsheet-mcp:") else f"pos:{TABS[tab]}:{index}"}

    def _find(self, sheet_name: str | None, image_id: str) -> tuple[str, int]:
        if image_id.startswith("pos:"):
            sheet_id, index = (int(p) for p in image_id[4:].split(":"))
            tab = next(t for t, i in TABS.items() if i == sheet_id)
            if index >= len(self.images[tab]):
                raise LookupError(f"No image {image_id}")
            return tab, index
        tabs = [self._tab(sheet_name)] if sheet_name else list(TABS)
        found = [(t, i) for t in tabs for i, im in enumerate(self.images[t])
                 if im["altDescription"] == image_id]
        if not found:
            raise LookupError(f"No image {image_id}")
        if len(found) > 1:
            raise LookupError(f"Image id {image_id} matches {len(found)} images; name the tab")
        return found[0]

    def add_foreign(self, tab: str, cell: str) -> None:
        """An image someone inserted by hand: no tag."""
        self.images[tab].append({"cell": cell, "offsetX": 0, "offsetY": 0, "widthPx": 50,
                                 "heightPx": 50, "altTitle": "", "altDescription": ""})

    def insertImage(self, ss, sheet_name, a1, b64, mime, w, h, tag, ox, oy, alt):  # noqa: N802
        tab = self._tab(sheet_name)
        self.images[tab].append({"cell": a1, "offsetX": ox, "offsetY": oy,
                                 "widthPx": w or 100, "heightPx": h or 80,
                                 "altTitle": alt or "", "altDescription": tag})
        return {**self._record(tab, len(self.images[tab]) - 1), "bytes": len(b64) * 3 // 4}

    def listImages(self, ss, sheet_name):  # noqa: N802
        tabs = [self._tab(sheet_name)] if sheet_name else list(TABS)
        return [self._record(t, i) for t in tabs for i in range(len(self.images[t]))]

    def moveImage(self, ss, sheet_name, image_id, a1, w, h, ox, oy):  # noqa: N802
        tab, index = self._find(sheet_name, image_id)
        image = self.images[tab][index]
        for key, value in (("cell", a1), ("widthPx", w), ("heightPx", h), ("offsetX", ox),
                           ("offsetY", oy)):
            if value is not None:
                image[key] = value
        return self._record(tab, index)

    def deleteImage(self, ss, sheet_name, image_id):  # noqa: N802
        tab, index = self._find(sheet_name, image_id)
        before = self._record(tab, index)
        del self.images[tab][index]
        return before

"""The server half of tables to DuckDB: pull tabs as CSV, fingerprint them.

    pull(spreadsheet_id, tab, range_, include_hidden) -> reply text with signed CSV links
    fingerprint(spreadsheet_id, props)                -> "<rows>x<cols>:<head hash>:<tail hash>"
    status(spreadsheet_id, tab, since)                -> reply text, `changed` or `unchanged ...`

The server never parses a pulled row: each tab's CSV export is copied chunk
by chunk into the state bucket, and the scan's regions travel beside it as
A1 ranges for the CLI to cut the CSV with. A JSON manifest (format
`gsheet-pull/1`) carries the same links and regions for `gsheet pull`.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import a1, addressing, reply, scan, sheets_api, transfer

MANIFEST_FORMAT = "gsheet-pull/1"
HEAD_ROWS = 500
TAIL_ROWS = 500
CHUNK_BYTES = 1024 * 1024


def _regions(spreadsheet_id: str, tab: str | None, include_hidden: bool) -> dict[str, Any]:
    """The scan's regions by alias, from the scan cache, scanning when it has none.

    With a tab named, a cold cache is filled by scanning that tab only: any
    write clears the cache, and a whole-workbook scan of a big workbook does
    not fit in one call."""
    cached = scan.load_cached(spreadsheet_id) or {}
    regions = cached.get("regions")
    if (regions is None or include_hidden
            or (tab is not None and not any(r["tab"] == tab for r in regions.values()))):
        scan.scan(spreadsheet_id, tab=tab, include_hidden=include_hidden)
        regions = (scan.load_cached(spreadsheet_id) or {}).get("regions", {})
    return regions


def _hash(rows: list[list[Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:12]


def fingerprint(spreadsheet_id: str, props: dict[str, Any]) -> str:
    """The tab's grid size plus a hash of its first and last 500 rows.

    Rows are grid rows (spec gap: "first and last 500 rows" does not say grid
    or used rows; the grid is what the tab size reports). Data added in blank
    grid rows above the last 500 is not seen until the grid grows."""
    grid = props.get("gridProperties", {})
    rows, cols = grid.get("rowCount", 0), grid.get("columnCount", 0)
    whole = a1.quote_tab(props["title"], always=True)
    if not rows:
        return f"0x{cols}:{_hash([])}:{_hash([])}"
    head = sheets_api.values_get(spreadsheet_id, f"{whole}!1:{min(HEAD_ROWS, rows)}")
    tail = sheets_api.values_get(spreadsheet_id, f"{whole}!{max(rows - TAIL_ROWS + 1, 1)}:{rows}")
    return f"{rows}x{cols}:{_hash(head)}:{_hash(tail)}"


def _chunks(stream) -> Any:
    try:
        yield from iter(lambda: stream.read(CHUNK_BYTES), b"")
    finally:
        stream.close()


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 2**20 else f"{n / 2**20:.1f} MB"


def _region_line(alias: str | None, info: dict[str, Any]) -> str:
    parts = [f"  {alias or 'range'} {info['range']} {info['kind']}"]
    if info.get("label"):
        parts[0] += f" \"{info['label']}\""
    if info.get("header_rows") is not None:
        parts.append(f"header {info['header_rows']}")
    if info.get("estimated"):
        parts.append("extent estimated")
    return " · ".join(parts)


def pull(spreadsheet_id: str, *, tab: str | None = None, range_: str | None = None,
         include_hidden: bool = False) -> str:
    tabs_reply = sheets_api.get_tabs(spreadsheet_id)
    if tab is not None:
        # A clear refusal for a tab that is not there, and the title the scan uses.
        tab = sheets_api.find_tab(tabs_reply, tab)["title"]
    picked: list[tuple[str | None, dict[str, Any]]] = []
    skipped: list[str] = []
    if range_:
        rng = addressing.resolve(spreadsheet_id, range_)
        props = sheets_api.find_tab(tabs_reply, rng.tab or tab)
        rng = rng.with_tab(props["title"])
        regions = _regions(spreadsheet_id, props["title"], include_hidden)
        match = next(((alias, info) for alias, info in regions.items()
                      if info["range"] == str(rng)), None)
        # A named range that is no scanned region is pulled as given, so a
        # communication block or any other part of a tab can be named.
        picked.append(match or (None, {"tab": props["title"], "gid": props["sheetId"],
                                       "range": str(rng), "kind": "range", "label": ""}))
    else:
        regions = _regions(spreadsheet_id, tab, include_hidden)
        for alias, info in regions.items():
            if tab is not None and info["tab"] != tab:
                continue
            if info["kind"] == "table":
                picked.append((alias, info))
            else:
                skipped.append(alias)

    by_tab: dict[str, list[tuple[str | None, dict[str, Any]]]] = {}
    for alias, info in picked:
        by_tab.setdefault(info["tab"], []).append((alias, info))
    title = (tabs_reply.get("properties") or {}).get("title", "")
    out = [f"pulled \"{title}\" ({spreadsheet_id}) · {len(by_tab)} "
           f"tab{'' if len(by_tab) == 1 else 's'} · CSV links valid "
           f"{transfer.PULL_URL_TTL_SECONDS // 60} minutes"]
    manifest: dict[str, Any] = {"format": MANIFEST_FORMAT, "spreadsheet_id": spreadsheet_id,
                                "title": title, "pulled_at": transfer.iso(time.time()),
                                "tabs": []}
    for tab_title, tab_regions in by_tab.items():
        props = sheets_api.find_tab(tabs_reply, tab_title)
        stream = sheets_api.open_export(
            sheets_api.export_url(spreadsheet_id, "csv", gid=props["sheetId"]))
        hosted, size = transfer.hand_out_stream(_chunks(stream))
        grid = props.get("gridProperties", {})
        fp = fingerprint(spreadsheet_id, props)
        out.append(f"tab \"{tab_title}\" (gid {props['sheetId']}) · "
                   f"{grid.get('rowCount', 0)}x{grid.get('columnCount', 0)} · "
                   f"csv {_size(size)} from A1 · fingerprint {fp}")
        out.append(f"  csv {hosted.url}")
        out += [_region_line(alias, info) for alias, info in tab_regions]
        manifest["tabs"].append({
            "tab": tab_title, "gid": props["sheetId"], "rows": grid.get("rowCount", 0),
            "cols": grid.get("columnCount", 0), "fingerprint": fp, "csv": hosted.url,
            "regions": [{"alias": alias, "range": info["range"], "kind": info["kind"],
                         "label": info.get("label", ""), "header_rows": info.get("header_rows"),
                         **({"table_name": info["table_name"]} if info.get("table_name") else {})}
                        for alias, info in tab_regions]})
    if by_tab:
        # One link for `gsheet pull`: the CSV links and the regions to cut them with.
        out.insert(1, f"manifest {transfer.hand_out_json(manifest).url}")
    else:
        out.append("nothing pulled: no table regions or native Tables"
                   + (f" on tab \"{tab}\"" if tab else ""))
    if skipped:
        out.append("not pulled (communication blocks; name one with range= to pull it): "
                   + ", ".join(skipped))
    return "\n".join(out)


def _parts(fp: str) -> tuple[str, str, str]:
    parts = fp.strip().split(":")
    if len(parts) != 3:
        raise reply.Refusal(f"{fp!r} is not a fingerprint; pass one pull_tables or "
                            f"table_status returned, like 40x8:1a2b3c4d5e6f:9f8e7d6c5b4a.")
    return parts[0], parts[1], parts[2]


def status(spreadsheet_id: str, tab: str, since: str | None = None) -> str:
    name = tab
    if "#" in tab or "!" in tab:
        name = addressing.resolve(spreadsheet_id, tab).tab or tab
    props = sheets_api.find_tab(sheets_api.get_tabs(spreadsheet_id), name)
    now = fingerprint(spreadsheet_id, props)
    line = f"tab \"{props['title']}\" (gid {props['sheetId']}) · fingerprint {now}"
    if since is None:
        return line
    (size0, head0, tail0), (size1, head1, tail1) = _parts(since), _parts(now)
    changes = []
    if size0 != size1:
        changes.append(f"size {size0} → {size1}")
    if head0 != head1:
        changes.append(f"first {HEAD_ROWS} rows")
    if tail0 != tail1:
        changes.append(f"last {TAIL_ROWS} rows")
    if changes:
        return f"{line}\nchanged: {', '.join(changes)}"
    return f"{line}\nunchanged at head and tail (the rows between were not compared)"

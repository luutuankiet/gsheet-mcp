"""`scan_sheet` through the tool layer, against the fake Sheets API serving the canary."""
from __future__ import annotations

import pytest

from gsheet_mcp import scan, sheets_api
from tests.conftest import call
from tests.fake_sheets import CANARY_ID, FIXTURES


def _tab(text: str, title: str) -> str:
    """One tab's block of the reply."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f'tab "{title}"'))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("tab ")),
               len(lines))
    return "\n".join(lines[start:end])


@pytest.fixture
async def scanned(fake, signed_in, client) -> str:
    text, is_error = await call(client, "scan_sheet", spreadsheet=CANARY_ID)
    assert not is_error, text
    return text


def test_xlsx_images_reads_the_canary_picture_with_its_anchor_and_size():
    images = sheets_api.xlsx_images((FIXTURES / "canary.xlsx").read_bytes())
    assert images == [{"tab": "comms", "row": 2, "col": 7, "row_off": 16, "col_off": 16,
                       "width": 96, "height": 40}]


async def test_multi_region_joins_splits_and_reports_exact_ranges(scanned):
    block = _tab(scanned, "multi-region")
    # table A joins across its 2-row gap
    assert "#1 A1:D20 table · blank rows 12,13 ·" in block
    # table D after the 4-row gap stays separate, with A34 as its footnote
    assert "#3 A25:D32 table" in block
    assert 'footnote A34 "* all figures are synthetic, lorem ipsum"' in block
    # table B is the named range, reported exactly, separate, with its Total row
    assert "#2 G1:J9 named range quarterly_targets table" in block
    assert "totals: row 9" in block
    assert "named ranges: quarterly_targets 'multi-region'!G1:J9" in scanned
    assert "loose cells: L3, L15" in block


async def test_native_table_reports_orders_with_its_column_types(scanned):
    block = _tab(scanned, "native-table")
    assert "A1:F31 Table Orders" in block
    assert "types: TEXT,-,DOUBLE,CURRENCY,DATE,-" in block


async def test_comms_reports_frozen_panes_the_image_and_the_placed_comment(scanned):
    block = _tab(scanned, "comms")
    assert "frozen 2/1" in block.splitlines()[0]
    assert "1 images" in block.splitlines()[0]
    assert "image over H3:I5 (96x40 px)" in block
    assert "comments: F4 open" in block
    assert "comments: 1 thread · comms 1 open 0 resolved" in scanned


async def test_hidden_tab_regions_appear_only_with_include_hidden(fake, signed_in, client,
                                                                  scanned):
    block = _tab(scanned, "hidden-tab")
    assert "· hidden" in block.splitlines()[0] and "10x4" in block
    assert "#1" not in block
    text, _ = await call(client, "scan_sheet", spreadsheet=CANARY_ID, include_hidden=True)
    assert "#1 A1:B3 table" in _tab(text, "hidden-tab")


async def test_a_tab_over_500k_cells_is_read_head_and_tail_only(fake, signed_in, client,
                                                                monkeypatch):
    rows = 60_000
    book = fake.books[CANARY_ID]
    book["sheets"].append({
        "properties": {"sheetId": 999, "title": "big", "index": 7, "sheetType": "GRID",
                       "gridProperties": {"rowCount": rows, "columnCount": 10}},
        "data": [{"rowData": [{"values": [
            {"formattedValue": "id" if r == 0 else f"k{r}",
             "effectiveValue": {"stringValue": "id" if r == 0 else f"k{r}"}},
            {"formattedValue": "n" if r == 0 else str(r),
             "effectiveValue": {"stringValue": "n"} if r == 0 else {"numberValue": r}},
        ]} for r in range(rows)]}]})
    asked: list[list[str]] = []
    real_get = fake.get

    def recording_get(**kw):
        asked.append(list(kw.get("ranges") or []))
        return real_get(**kw)

    monkeypatch.setattr(fake, "get", recording_get)
    text, is_error = await call(client, "scan_sheet", spreadsheet=CANARY_ID, tab="big")
    assert not is_error, text
    assert ["'big'!1:2000", "'big'!59501:60000"] in asked
    assert "#1 A1:B60000 table · extent estimated" in _tab(text, "big")


async def test_the_whole_canary_reply_stays_under_2500_tokens(scanned):
    assert len(scanned) / 4 < 2500


async def test_scan_caches_region_aliases_until_a_write_clears_them(scanned, store):
    assert scan.resolve_alias(CANARY_ID, "multi-region#2") == "'multi-region'!G1:J9"
    scan.clear(CANARY_ID)
    assert scan.resolve_alias(CANARY_ID, "multi-region#2") is None


async def test_scan_cache_lives_ten_minutes(scanned, store, monkeypatch):
    now = store._clock()
    monkeypatch.setattr(store, "_clock", lambda: now + scan.CACHE_TTL_SECONDS + 1)
    assert scan.resolve_alias(CANARY_ID, "comms#1") is None


async def test_unknown_spreadsheet_is_refused(fake, signed_in, client):
    text, is_error = await call(client, "scan_sheet", spreadsheet="nope")
    assert is_error and "Cannot scan spreadsheet nope" in text


def _sheet(fake, title: str) -> dict:
    return next(s for s in fake.books[CANARY_ID]["sheets"] if s["properties"]["title"] == title)


async def test_a_pivot_table_gets_its_own_line_with_source_and_filled_region(fake, signed_in,
                                                                             client):
    sheet = _sheet(fake, "multi-region")
    anchor = sheet["data"][0]["rowData"][0]["values"][6]           # G1, table B's corner
    anchor["pivotTable"] = {"source": {"sheetId": 104, "startRowIndex": 0, "endRowIndex": 20,
                                       "startColumnIndex": 0, "endColumnIndex": 4},
                            "rows": [{"sourceColumnOffset": 0}]}
    text, is_error = await call(client, "scan_sheet", spreadsheet=CANARY_ID)
    assert not is_error, text
    block = _tab(text, "multi-region")
    assert "  pivot at G1 · source 'multi-region'!A1:D20 · fills G1:J9" in block


async def test_a_data_source_pivot_names_its_data_source(fake, signed_in, client):
    sheet = _sheet(fake, "multi-region")
    sheet["data"][0]["rowData"][0]["values"][6]["pivotTable"] = {"dataSourceId": "ds-1"}
    text, _ = await call(client, "scan_sheet", spreadsheet=CANARY_ID)
    assert "pivot at G1 · source data source ds-1" in _tab(text, "multi-region")


async def test_a_chart_line_lists_its_source_ranges(fake, signed_in, client):
    def src(c0: int, c1: int) -> dict:
        return {"sourceRange": {"sources": [{"sheetId": 104, "startRowIndex": 0,
                                             "endRowIndex": 20, "startColumnIndex": c0,
                                             "endColumnIndex": c1}]}}
    _sheet(fake, "multi-region").setdefault("charts", []).append({
        "chartId": 7,
        "spec": {"title": "by region", "basicChart": {
            "chartType": "BAR", "domains": [{"domain": src(0, 1)}],
            "series": [{"series": src(1, 2)}, {"series": src(0, 1)}]}},
        "position": {"overlayPosition": {"anchorCell": {"sheetId": 104, "rowIndex": 21,
                                                        "columnIndex": 5}}}})
    text, is_error = await call(client, "scan_sheet", spreadsheet=CANARY_ID)
    assert not is_error, text
    assert ('chart bar "by region" at F22 · sources '
            "'multi-region'!A1:A20, 'multi-region'!B1:B20") in _tab(text, "multi-region")

"""`write_cells` through the tool layer, against the fake Sheets API serving
the canary: value-only masks, Markdown rich text, refusals and warnings for
cells with structure, plans and the stale-write guard, receipts, and the
caches a write clears. One test per acceptance line of the writes issue,
then the spec rules those lines lean on."""
from __future__ import annotations

import re

import pytest

from gsheet_mcp import guard, sheets_api, writes
from tests.conftest import call
from tests.fake_sheets import CANARY_ID


@pytest.fixture
def ready(fake, signed_in, client):
    return client


def _cell(fake, tab: str, row: int, col: int) -> dict:
    sheet = next(s for s in fake.books[CANARY_ID]["sheets"] if s["properties"]["title"] == tab)
    rows = sheet["data"][0].get("rowData", [])
    values = rows[row].get("values", []) if row < len(rows) else []
    return values[col] if col < len(values) else {}


def _update_cells(fake) -> list[dict]:
    return [r["updateCells"] for _, requests in fake.batch_updates for r in requests
            if "updateCells" in r]


def _plan_id(text: str) -> str:
    return re.search(r"plan (\S+)", text).group(1)


# ---- the acceptance lines ---------------------------------------------------------


async def test_writing_a_value_into_a_filled_bold_cell_keeps_its_format(ready, fake):
    assert _cell(fake, "comms", 0, 0)["userEnteredFormat"]["textFormat"]["bold"]
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!A1", values="W44")
    assert not is_error, text
    (request,) = _update_cells(fake)
    assert request["fields"] == "userEnteredValue"
    assert request["rows"] == [{"values": [{"userEnteredValue": {"stringValue": "W44"}}]}]
    cell = _cell(fake, "comms", 0, 0)
    assert cell["userEnteredValue"] == {"stringValue": "W44"}
    assert cell["userEnteredFormat"]["textFormat"]["bold"]


async def test_markdown_bold_and_link_becomes_a_bold_run_and_a_link_run(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1",
                                values="**bold** and [link](https://example.com)")
    assert not is_error, text
    (request,) = _update_cells(fake)
    assert request["fields"] == "userEnteredValue,textFormatRuns"
    (cell,) = request["rows"][0]["values"]
    assert cell["userEnteredValue"] == {"stringValue": "bold and link"}
    styled = [r for r in cell["textFormatRuns"] if r["format"]]
    assert styled == [{"format": {"bold": True}},
                      {"startIndex": 9, "format": {"link": {"uri": "https://example.com"}}}]
    # The receipt reads it back in the same Markdown.
    assert "**bold** and [link](https://example.com)" in text


async def test_writing_plain_text_over_comms_b4_warns_that_runs_are_dropped(ready, fake):
    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="comms!B4", values="Plain now", dry_run=True)
    assert not is_error, planned
    warning = re.search(r"warning: B4: drops 2 rich text runs \(italic, link\)", planned)
    assert warning, planned
    assert not fake.batch_updates

    written, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="comms!B4", values="Plain now")
    assert not is_error, written
    assert warning.group(0) in written
    (request,) = _update_cells(fake)
    assert request["fields"] == "userEnteredValue,textFormatRuns"
    assert "textFormatRuns" not in _cell(fake, "comms", 3, 1)


async def test_a_covered_merge_cell_is_refused_with_its_top_left_and_yes_into_a_checkbox_is_refused(
        ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!C1", values="inside the merge")
    assert is_error and "Nothing was written" in text
    assert "C1 is covered by the merge B1:F1" in text and "top-left cell B1" in text

    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!D3", values="yes")
    assert is_error and "D3 is a checkbox and takes only true or false" in text
    assert not fake.batch_updates

    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!D3", values=False)
    assert not is_error, text
    assert _cell(fake, "comms", 2, 3)["userEnteredValue"] == {"boolValue": False}


async def test_a_250_cell_write_without_a_plan_is_refused_and_with_a_plan_it_applies(
        ready, fake):
    values = [[f"r{r}c{c}" for c in range(10)] for r in range(25)]
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="'multi-region'!A12", values=values)
    assert is_error and "250 cells" in text and "dry_run=true" in text
    assert not fake.batch_updates

    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="'multi-region'!A12", values=values, dry_run=True)
    assert not is_error, planned
    assert "250 cells" in planned and not fake.batch_updates

    applied, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(planned))
    assert not is_error, applied
    assert len(fake.batch_updates) == 1
    assert sheets_api.values_get(CANARY_ID, "'multi-region'!J36") == [["r24c9"]]


async def test_a_cell_changed_between_plan_and_apply_refuses_the_whole_batch(ready, fake):
    planned, is_error = await call(
        ready, "write_cells", spreadsheet=CANARY_ID, dry_run=True,
        writes_=[{"range": "comms!B4", "values": "mine"},
                 {"range": "comms!A1", "values": "W44"}])
    assert not is_error, planned
    fake.set_cell(CANARY_ID, "'comms'!B4", "old")
    old_plan = _plan_id(planned)
    planned, _ = await call(
        ready, "write_cells", spreadsheet=CANARY_ID, dry_run=True,
        writes_=[{"range": "comms!B4", "values": "mine"},
                 {"range": "comms!A1", "values": "W44"}])
    fake.set_cell(CANARY_ID, "'comms'!B4", "new")

    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                plan_id=_plan_id(planned))
    assert is_error and "Nothing was written" in text
    assert 'B4: "old" → "new"' in text and "Make a new plan" in text
    assert "A1" not in text.split("changed since")[1]
    assert not fake.batch_updates
    # The older plan was taken over B4's first text, so it is stale too.
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, plan_id=old_plan)
    assert is_error and 'B4: "Dolor migration (ticket)" → "new"' in text
    assert not fake.batch_updates


async def test_the_receipt_shows_the_touched_cells_in_read_notation_capped_at_50(ready, fake):
    values = [[f"v{r}{c}" for c in range(6)] for r in range(10)]          # 60 cells
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="formulas!A11", values=values)
    assert not is_error, text
    lines = text.splitlines()
    assert lines[0] == "wrote 60 cells, showing the first 50"
    cells = [line for line in lines if re.match(r"[A-Z]+\d+( \{s\d+\})?: ", line)]
    assert len(cells) == 50
    assert cells[0] == "A11: v00" and cells[-1] == "B19: v81"

    text, _ = await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1",
                         values="W44")
    assert re.search(r"^A1 \{s\d+\}: W44$", text, re.M), text
    assert re.search(r"^styles:\n  s\d+ .*bold", text, re.M), text


# ---- the rules those lines lean on ------------------------------------------------


async def test_a_strict_dropdown_refuses_a_value_outside_its_list(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!E3", values="Sideways")
    assert is_error and "E3" in text and "is not in its dropdown list" in text
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!E3", values="At risk")
    assert not is_error, text


async def test_overwriting_a_formula_with_a_plain_value_warns(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="formulas!B2", values=7)
    assert not is_error, text
    assert "warning: B2: overwrites the formula =A2*2" in text


async def test_an_iso_date_in_a_date_cell_becomes_a_serial_and_elsewhere_stays_text(
        ready, fake):
    await call(ready, "write_cells", spreadsheet=CANARY_ID, range="'native-table'!E2",
               values="2026-03-01")
    await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1",
               values="2026-03-01")
    first, second = _update_cells(fake)
    assert first["rows"][0]["values"][0]["userEnteredValue"] == {"numberValue": 46082}
    assert second["rows"][0]["values"][0]["userEnteredValue"] == {"stringValue": "2026-03-01"}


async def test_a_format_patch_names_only_its_own_fields_and_null_clears(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1",
                                format={"textFormat": {"bold": None, "italic": True}})
    assert not is_error, text
    (request,) = _update_cells(fake)
    assert request["fields"] == ("userEnteredFormat.textFormat.bold,"
                                 "userEnteredFormat.textFormat.italic")
    fmt = _cell(fake, "comms", 0, 0)["userEnteredFormat"]["textFormat"]
    assert fmt.get("italic") is True and "bold" not in fmt


async def test_an_empty_string_clears_a_value_and_null_leaves_a_cell(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="formulas!A2:B2", values=["", None])
    assert not is_error, text
    assert "userEnteredValue" not in _cell(fake, "formulas", 1, 0)
    assert _cell(fake, "formulas", 1, 1)["userEnteredValue"] == {"formulaValue": "=A2*2"}


# ---- a write clears the caches ------------------------------------------------------


async def test_a_write_clears_the_render_cache(ready, fake):
    first, _ = await call(ready, "render_range", spreadsheet=CANARY_ID, range="comms!B2:F8")
    cached, _ = await call(ready, "render_range", spreadsheet=CANARY_ID, range="comms!B2:F8")
    assert cached.endswith("from cache") and fake.calls.count("export") == 1

    await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1", values="W44")
    again, is_error = await call(ready, "render_range", spreadsheet=CANARY_ID,
                                 range="comms!B2:F8")
    assert not is_error, again
    assert "from cache" not in again and fake.calls.count("export") == 2


async def test_a_write_clears_the_scan_cache(ready, fake):
    scanned, is_error = await call(ready, "scan_sheet", spreadsheet=CANARY_ID)
    assert not is_error, scanned
    exports = len(fake.downloads)
    text, _ = await call(ready, "read_range", spreadsheet=CANARY_ID, range="comms#1")
    assert len(fake.downloads) == exports, "the read took its images from the scan cache"

    await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!A1", values="W44")
    text, is_error = await call(ready, "read_range", spreadsheet=CANARY_ID, range="comms!A1:C3")
    assert not is_error, text
    assert len(fake.downloads) == exports + 1, "the read went back to the fake for its images"
    text, is_error = await call(ready, "read_range", spreadsheet=CANARY_ID, range="comms#1")
    assert is_error, "the scan's aliases went with the cache"


# ---- fixes from the live test ------------------------------------------------------


async def test_the_diff_shows_a_boolean_as_true_or_false_in_capitals(ready, fake):
    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="comms!D4", values=True, dry_run=True)
    assert not is_error, planned
    assert 'D4: "FALSE" → "TRUE"' in planned, planned


async def test_a_format_only_change_since_the_plan_is_named_in_the_refusal(ready, fake):
    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="comms!H20", values="planned", dry_run=True)
    assert not is_error, planned
    _, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!H20",
                             format={"backgroundColor": {"red": 1, "green": 1, "blue": 0}})
    assert not is_error
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                plan_id=_plan_id(planned))
    assert is_error
    assert "H20: format changed" in text, text


async def test_the_receipt_names_a_merge_by_its_whole_range(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!B3", values="into the merge")
    assert not is_error, text
    assert "B3:C3: into the merge" in text, text


async def test_a_format_only_receipt_shows_the_format_that_landed(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, range="comms!H21",
                                format={"backgroundColor": {"red": 1, "green": 0.9, "blue": 0.8}})
    assert not is_error, text
    assert "H21 {s1}: (empty)" in text, text
    assert re.search(r"s1 .*fill #", text), text


async def test_values_has_a_json_type_for_a_cell_a_row_and_a_grid(ready):
    (tool,) = [t for t in await ready.list_tools() if t.name == "write_cells"]
    shapes = tool.input_schema["properties"]["values"]["anyOf"]
    assert {"string", "number", "boolean", "array", "null"} <= {s.get("type") for s in shapes}
    arrays = [s["items"] for s in shapes if s.get("type") == "array"]
    cell = {"string", "integer", "number", "boolean", "null"}
    row = [i for i in arrays if "anyOf" in i]
    grid = [i for i in arrays if i.get("type") == "array"]
    assert row and {t["type"] for t in row[0]["anyOf"]} == cell, arrays
    assert grid and {t["type"] for t in grid[0]["items"]["anyOf"]} == cell, arrays


async def test_a_write_into_hidden_rows_and_a_hidden_column_warns(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="hidden!B5", values=[["a", "b"], ["c", "d"]],
                                dry_run=True)
    assert not is_error, text
    assert "warning: B5:C6: lands in hidden rows 5-6, column C" in text, text


async def test_a_write_into_a_hidden_tab_warns(ready, fake):
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="'hidden-tab'!A1", values="x", dry_run=True)
    assert not is_error, text
    assert "warning: A1: lands in the hidden tab 'hidden-tab'" in text, text


async def test_a_write_over_the_cell_budget_is_refused_before_any_cell_is_read(
        ready, fake, monkeypatch):
    def no_read(*_a, **_k):
        raise AssertionError("the guard read cells for a write it must refuse")

    monkeypatch.setattr(writes, "WRITE_CELLS", 100, raising=False)
    monkeypatch.setattr(guard, "read", no_read)
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="comms!A1:L30", format={"textFormat": {"bold": True}})
    assert is_error
    assert "covers 360 cells; one call takes at most 100" in text, text
    assert fake.batch_updates == []

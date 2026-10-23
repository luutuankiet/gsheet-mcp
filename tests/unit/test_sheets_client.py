"""The Sheets and Drive client against FakeSheets, which serves the canary fixtures."""
from __future__ import annotations

import json

import pytest

from gsheet_mcp import reply, sheets_api

CANARY_TABS = ["comms", "dashboard", "native-table", "multi-region", "hidden", "formulas",
               "hidden-tab"]


def test_a_lean_get_returns_the_seven_tabs_with_merges_notes_and_validation(fake):
    book = sheets_api.get_spreadsheet("canary")
    assert fake.calls == ["spreadsheets.get"]
    assert [s["properties"]["title"] for s in book["sheets"]] == CANARY_TABS
    comms = book["sheets"][0]
    assert len(comms["merges"]) == 8
    cells = [c for r in comms["data"][0]["rowData"] for c in r.get("values", [])]
    assert any(c.get("note") for c in cells)
    assert any("dataValidation" in c for c in cells)
    assert book["sheets"][6]["properties"]["hidden"] is True


def test_the_lean_mask_keeps_out_what_it_does_not_name(fake):
    book = fake.books["canary"]
    book["sheets"][0]["data"][0]["rowData"][0]["values"][0]["effectiveFormat"] = {"x": 1}
    got = sheets_api.get_spreadsheet("canary")
    assert "effectiveFormat" not in got["sheets"][0]["data"][0]["rowData"][0]["values"][0]


def test_a_get_with_ranges_returns_only_those_tabs_offset_to_the_range(fake):
    book = sheets_api.get_spreadsheet("canary", ["'hidden'!B2:C3", "comms!A1:B1"])
    assert [s["properties"]["title"] for s in book["sheets"]] == ["hidden", "comms"]
    grid = book["sheets"][0]["data"][0]
    assert (grid["startRow"], grid["startColumn"], len(grid["rowData"])) == (1, 1, 2)


def test_values_get_pads_trailing_blanks_to_the_tab_width(fake):
    # Google (and the fake) trims row 1 to its 2 valued cells.
    assert fake.values_get("canary", range="'comms'")["values"][0] == [
        "W43", "Weekly status · lorem ipsum"]
    rows = sheets_api.values_get("canary", "'comms'")
    assert {len(r) for r in rows} == {12}          # comms is 12 columns wide
    assert rows[0][:3] == ["W43", "Weekly status · lorem ipsum", ""]


def test_values_get_pads_a_bounded_range_to_its_own_width(fake):
    assert sheets_api.values_get("canary", "comms!A9:F9") == [["Plan link", "Plan", "", "", "", ""]]


def test_a_429_is_retried_once_after_a_backoff(fake, offline):
    fake.rate_limit(1)
    book = sheets_api.get_tabs("canary")
    assert len(book["sheets"]) == 7
    assert fake.calls == ["spreadsheets.get", "spreadsheets.get"]
    assert offline == [sheets_api.RETRY_BACKOFF_SECONDS]


def test_a_second_429_is_a_visible_refusal(fake, offline):
    fake.rate_limit(2)
    with pytest.raises(reply.Refusal) as refused:
        sheets_api.get_tabs("canary")
    assert "retried once" in str(refused.value)
    assert len(fake.calls) == 2                    # one retry, no more


def test_an_export_429_is_retried_once_then_refused(fake, offline):
    url = sheets_api.export_url("canary", "xlsx")
    fake.rate_limit(1)
    assert sheets_api.download(url).startswith(b"PK")
    fake.rate_limit(2)
    with pytest.raises(reply.Refusal):
        sheets_api.download(url)


def test_other_errors_are_not_retried(fake, offline):
    with pytest.raises(sheets_api.SheetsApiError) as failed:
        sheets_api.get_tabs("missing")
    assert failed.value.status == 404 and offline == []


def test_the_fake_records_a_batch_update_and_applies_a_value_write(fake):
    request = {"updateCells": {"start": {"sheetId": 101, "rowIndex": 3, "columnIndex": 1},
                               "rows": [{"values": [{"userEnteredValue": {"stringValue": "x"}}]}],
                               "fields": "userEnteredValue"}}
    sheets_api.batch_update("canary", [request, {"mergeCells": {}}])
    assert fake.batch_updates == [("canary", [request, {"mergeCells": {}}])]
    assert sheets_api.values_get("canary", "comms!B4") == [["x"]]


def test_the_fake_can_change_a_cell_between_two_calls(fake):
    sheets_api.get_spreadsheet("canary", ["comms!B4"])
    fake.after_next_call(lambda: fake.set_cell("canary", "comms!B4", "someone else"))
    before = sheets_api.get_spreadsheet("canary", ["comms!B4"])
    after = sheets_api.get_spreadsheet("canary", ["comms!B4"])

    def value(book):
        return book["sheets"][0]["data"][0]["rowData"][0]["values"][0]["formattedValue"]
    assert value(before) == "Dolor migration (ticket)"
    assert value(after) == "someone else"


def test_export_links_name_the_tab_and_range():
    assert sheets_api.export_url("s1", "xlsx").endswith("/d/s1/export?format=xlsx")
    assert sheets_api.export_url("s1", "csv", gid=105).endswith("format=csv&gid=105")
    assert sheets_api.export_url("s1", "pdf", gid=101, range_="comms!B2:F8").endswith(
        "format=pdf&gid=101&range=B2:F8")


def test_a_csv_export_is_the_tabs_values(fake):
    data = sheets_api.download(sheets_api.export_url("canary", "csv", gid=101, range_="A9:B9"))
    assert data == b"Plan link,Plan\n"


def test_the_export_places_the_drive_thread_on_its_cell(fake):
    threads = sheets_api.xlsx_threads(sheets_api.download(sheets_api.export_url("canary", "xlsx")))
    assert threads == [{"tab": "comms", "cell": "F4", "text": "lorem question?",
                        "replies": ["ipsum answer"], "done": False}]
    [drive] = sheets_api.list_comments("canary")
    assert json.loads(drive["anchor"])["type"] == "workbook-range"   # opaque, no cell
    assert drive["content"] == threads[0]["text"]


def test_a_comment_is_added_unanchored_then_replied_to_and_resolved(fake):
    thread = sheets_api.add_comment("canary", "new question")
    sheets_api.reply_comment("canary", thread["id"], "done", resolve=True)
    stored = [c for c in sheets_api.list_comments("canary") if c["id"] == thread["id"]][0]
    assert stored["resolved"] and stored["replies"][0]["action"] == "resolve"
    assert "anchor" not in stored


@pytest.mark.parametrize("given", [
    "1AbC_d-9",
    "https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=0",
    "https://docs.google.com/spreadsheets/u/1/d/1AbC_d-9/view",
])
def test_spreadsheet_id_from_a_url_or_an_id(given):
    assert sheets_api.spreadsheet_id_from_url(given) == "1AbC_d-9"

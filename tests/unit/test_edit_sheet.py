"""`edit_sheet` through the tool layer, against the fake Sheets API serving the
canary: typed operations compiled into one batchUpdate, the frozen-edge merge
refusal, the structural guard, the note-only mask, Table `appendCells` and
Drive comments. One test per acceptance line of the edit_sheet issue."""
from __future__ import annotations

import re

import pytest

from tests.conftest import call
from tests.fake_sheets import CANARY_ID

COMMS, TABLE = 101, 103


@pytest.fixture
def ready(fake, signed_in, client):
    return client


def _plan_id(text: str) -> str:
    return re.search(r"plan (\S+)", text).group(1)


async def _plan_and_apply(client, operations):
    planned, is_error = await call(client, "edit_sheet", spreadsheet=CANARY_ID,
                                   operations=operations)
    assert not is_error, planned
    return await call(client, "edit_sheet", spreadsheet=CANARY_ID, plan_id=_plan_id(planned))


EXPECTED = [
    ({"op": "freeze", "tab": "comms", "rows": 1, "columns": 2},
     {"updateSheetProperties": {"properties": {"sheetId": COMMS, "gridProperties": {
         "frozenRowCount": 1, "frozenColumnCount": 2}},
         "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}}),
    ({"op": "hide", "range": "'comms'!5:6"},
     {"updateDimensionProperties": {"range": {"sheetId": COMMS, "dimension": "ROWS",
                                              "startIndex": 4, "endIndex": 6},
                                    "properties": {"hiddenByUser": True},
                                    "fields": "hiddenByUser"}}),
    ({"op": "unhide", "tab": "hidden-tab"},
     {"updateSheetProperties": {"properties": {"sheetId": 107, "hidden": False},
                                "fields": "hidden"}}),
    ({"op": "merge", "range": "'comms'!H10:I11"},
     {"mergeCells": {"range": {"sheetId": COMMS, "startRowIndex": 9, "endRowIndex": 11,
                               "startColumnIndex": 7, "endColumnIndex": 9},
                     "mergeType": "MERGE_ALL"}}),
    ({"op": "unmerge", "range": "'comms'!B1:F1"},
     {"unmergeCells": {"range": {"sheetId": COMMS, "startRowIndex": 0, "endRowIndex": 1,
                                 "startColumnIndex": 1, "endColumnIndex": 6}}}),
    ({"op": "insert", "range": "'comms'!C:D"},
     {"insertDimension": {"range": {"sheetId": COMMS, "dimension": "COLUMNS",
                                    "startIndex": 2, "endIndex": 4},
                          "inheritFromBefore": True}}),
    ({"op": "delete", "range": "'comms'!20:21"},
     {"deleteDimension": {"range": {"sheetId": COMMS, "dimension": "ROWS",
                                    "startIndex": 19, "endIndex": 21}}}),
    ({"op": "move", "range": "'comms'!20:20", "to": 25},
     {"moveDimension": {"source": {"sheetId": COMMS, "dimension": "ROWS",
                                   "startIndex": 19, "endIndex": 20},
                        "destinationIndex": 24}}),
    ({"op": "add_tab", "title": "new"}, {"addSheet": {"properties": {"title": "new"}}}),
    ({"op": "rename_tab", "tab": "comms", "title": "comms2"},
     {"updateSheetProperties": {"properties": {"sheetId": COMMS, "title": "comms2"},
                                "fields": "title"}}),
    ({"op": "duplicate_tab", "tab": "comms", "title": "copy"},
     {"duplicateSheet": {"sourceSheetId": COMMS, "insertSheetIndex": 1,
                         "newSheetName": "copy"}}),
    ({"op": "delete_tab", "tab": "hidden-tab"}, {"deleteSheet": {"sheetId": 107}}),
    ({"op": "sort", "range": "'native-table'!A2:F31", "by": [{"column": "B", "order": "desc"}]},
     {"sortRange": {"range": {"sheetId": TABLE, "startRowIndex": 1, "endRowIndex": 31,
                              "startColumnIndex": 0, "endColumnIndex": 6},
                    "sortSpecs": [{"dimensionIndex": 1, "sortOrder": "DESCENDING"}]}}),
    ({"op": "set_filter", "range": "'comms'!A2:F20"},
     {"setBasicFilter": {"filter": {"range": {"sheetId": COMMS, "startRowIndex": 1,
                                              "endRowIndex": 20, "startColumnIndex": 0,
                                              "endColumnIndex": 6}}}}),
    ({"op": "clear_filter", "tab": "hidden"}, {"clearBasicFilter": {"sheetId": 105}}),
    ({"op": "add_filter_view", "range": "'comms'!A2:F20", "title": "mine"},
     {"addFilterView": {"filter": {"range": {"sheetId": COMMS, "startRowIndex": 1,
                                             "endRowIndex": 20, "startColumnIndex": 0,
                                             "endColumnIndex": 6}, "title": "mine"}}}),
    ({"op": "update_filter_view", "id": 42, "title": "renamed"},
     {"updateFilterView": {"filter": {"title": "renamed", "filterViewId": 42},
                           "fields": "title"}}),
    ({"op": "delete_filter_view", "id": 42}, {"deleteFilterView": {"filterId": 42}}),
    ({"op": "checkbox", "range": "'comms'!K3:K5"},
     {"setDataValidation": {"range": {"sheetId": COMMS, "startRowIndex": 2, "endRowIndex": 5,
                                      "startColumnIndex": 10, "endColumnIndex": 11},
                            "rule": {"condition": {"type": "BOOLEAN"}}}}),
    ({"op": "dropdown", "range": "'comms'!L3", "values": ["a", "b"]},
     {"setDataValidation": {"range": {"sheetId": COMMS, "startRowIndex": 2, "endRowIndex": 3,
                                      "startColumnIndex": 11, "endColumnIndex": 12},
                            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                {"userEnteredValue": "a"}, {"userEnteredValue": "b"}]},
                                "strict": True, "showCustomUi": True}}}),
]


@pytest.mark.parametrize(("operation", "request_"), EXPECTED,
                         ids=[op["op"] + str(i) for i, (op, _) in enumerate(EXPECTED)])
async def test_each_operation_compiles_to_the_expected_request_against_the_fake(
        ready, fake, operation, request_):
    text, is_error = await _plan_and_apply(ready, [operation])
    assert not is_error, text
    assert fake.batch_updates[-1] == (CANARY_ID, [request_])


async def test_a_merge_across_the_frozen_column_a_on_comms_is_refused_before_any_request_is_sent(
        ready, fake):
    text, is_error = await call(ready, "edit_sheet", spreadsheet=CANARY_ID,
                                operations=[{"op": "merge", "range": "'comms'!A10:C10"}])
    assert is_error
    assert "frozen column" in text, text
    assert fake.batch_updates == []


async def test_a_sort_with_a_row_inserted_on_the_fake_between_plan_and_apply_is_refused(
        ready, fake):
    planned, _ = await call(ready, "edit_sheet", spreadsheet=CANARY_ID, operations=[
        {"op": "sort", "range": "'native-table'!A2:F31", "by": [{"column": "B"}]}])
    fake.insert_rows(CANARY_ID, "native-table", 5)
    text, is_error = await call(ready, "edit_sheet", spreadsheet=CANARY_ID,
                                plan_id=_plan_id(planned))
    assert is_error
    assert "40 rows x 8 columns → 41 rows x 8 columns" in text, text
    assert fake.batch_updates == []
    again, is_error = await call(ready, "edit_sheet", spreadsheet=CANARY_ID,
                                 plan_id=_plan_id(planned))
    assert is_error and "used" in again             # single use, even when refused


async def test_a_note_write_records_a_mask_that_names_only_note(ready, fake):
    text, is_error = await _plan_and_apply(ready, [
        {"op": "note", "range": "'comms'!H3:H4", "note": "check this"}])
    assert not is_error, text
    (request,) = fake.batch_updates[-1][1]
    assert request["updateCells"]["fields"] == "note"
    assert request["updateCells"]["rows"] == [{"values": [{"note": "check this"}]}] * 2
    sheet = fake.books[CANARY_ID]["sheets"][0]
    assert sheet["data"][0]["rowData"][2]["values"][7]["note"] == "check this"


async def test_appending_to_table_orders_uses_appendcells_with_its_tableid(ready, fake):
    text, is_error = await _plan_and_apply(ready, [
        {"op": "append_rows", "table": "Orders", "rows": [["A-999", 3, True, "=1+1"]]}])
    assert not is_error, text
    (request,) = fake.batch_updates[-1][1]
    assert request == {"appendCells": {
        "sheetId": TABLE, "tableId": "2124794872", "fields": "userEnteredValue",
        "rows": [{"values": [{"userEnteredValue": {"stringValue": "A-999"}},
                             {"userEnteredValue": {"numberValue": 3}},
                             {"userEnteredValue": {"boolValue": True}},
                             {"userEnteredValue": {"formulaValue": "=1+1"}}]}]}}


async def test_comments_go_through_drive_add_reply_and_resolve(ready, fake):
    text, is_error = await _plan_and_apply(ready, [{"op": "comment", "text": "hello"}])
    assert not is_error, text
    thread = fake.comments[CANARY_ID][-1]
    assert thread["content"] == "hello"
    text, is_error = await _plan_and_apply(ready, [
        {"op": "resolve", "comment_id": thread["id"], "text": "done"}])
    assert not is_error, text
    assert fake.comments[CANARY_ID][-1]["resolved"] is True
    assert fake.batch_updates == []

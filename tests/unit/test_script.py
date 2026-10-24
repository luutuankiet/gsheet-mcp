"""`run_sheet_script` and raw `batch_update` through the tool layer, against the
fake Sheets API serving the canary. One test per acceptance line of the issue,
then the plan rules those lines lean on."""
from __future__ import annotations

import re

import pytest

from gsheet_mcp import state_store
from tests.conftest import call
from tests.fake_sheets import CANARY_ID

LOOP = "for (let i = 0; i < 10; i++) { sheet.write(`multi-region!N${i + 21}`, 'row ' + i); }"
CHART = {"addChart": {"chart": {
    "spec": {"title": "Script chart", "basicChart": {"chartType": "COLUMN"}},
    "position": {"overlayPosition": {"anchorCell": {"sheetId": 101, "rowIndex": 5,
                                                    "columnIndex": 8}}}}}}


@pytest.fixture
def ready(fake, signed_in, client):
    return client


def _plan_id(text: str) -> str:
    return re.search(r"plan (\S+)", text).group(1)


# ---- the acceptance lines ---------------------------------------------------------


async def test_a_script_that_loops_over_10_rows_and_writes_each_one_produces_one_plan_with_10_writes(
        ready, fake):
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=LOOP)
    assert not is_error, text
    assert "10 writes, 0 raw requests, nothing written yet" in text
    assert text.count("plan ") == 1 and not fake.batch_updates
    payload = state_store.current().load_plan(_plan_id(text), spreadsheet_id=CANARY_ID,
                                              caller=None) or {}
    if payload:
        assert len(payload["writes"]) == 10
    written, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(text), dry_run=False)
    assert not is_error, written
    assert "wrote 10 cells" in written
    assert len(fake.batch_updates) == 1


async def test_a_script_cannot_reach_the_network_or_the_filesystem(ready, fake):
    probe = ("return ['fetch', 'require', 'XMLHttpRequest', 'process', 'Deno', 'Bun', "
             "'WebSocket', 'importScripts'].filter(n => typeof globalThis[n] !== 'undefined');")
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=probe)
    assert not is_error, text
    assert "return: []" in text
    for attempt in ("await fetch('https://example.com');",
                    "require('fs').readFileSync('/etc/passwd');",
                    "await import('fs');"):
        text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                    script=attempt)
        assert is_error and "error" in text, (attempt, text)
    assert not fake.batch_updates


async def test_raw_batch_update_without_a_plan_id_is_refused(ready, fake):
    text, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                requests=[CHART], dry_run=False)
    assert is_error and "always needs a plan" in text
    assert not fake.batch_updates


async def test_a_raw_add_chart_request_applies_and_the_scan_then_lists_the_chart(ready, fake):
    before, _ = await call(ready, "scan_sheet", spreadsheet=CANARY_ID, tab="comms")
    assert "Script chart" not in before
    planned, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                   requests=[CHART])
    assert not is_error, planned
    assert "affected: comms!I6" in planned and not fake.batch_updates
    applied, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(planned), dry_run=False)
    assert not is_error, applied
    assert "applied 1 raw request; affected: comms!I6" in applied
    after, _ = await call(ready, "scan_sheet", spreadsheet=CANARY_ID, tab="comms")
    assert 'chart column "Script chart" at I6' in after


# ---- the plan rules -----------------------------------------------------------------


async def test_a_script_reads_a_range_and_its_raw_request_shares_the_plan(ready, fake):
    script = ("const rows = sheet.read('comms!A1:B2'); sheet.request(" + repr(CHART).replace(
        "'", '"') + "); return rows.length;")
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=script)
    assert not is_error, text
    assert "0 writes, 1 raw request" in text and "return: 2" in text


async def test_a_plan_is_used_once_and_refused_when_its_cells_moved(ready, fake):
    text, _ = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=LOOP)
    fake.set_cell(CANARY_ID, "multi-region!N21", "someone else")
    refused, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(text), dry_run=False)
    assert is_error and "changed since the plan" in refused, refused
    again, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                 plan_id=_plan_id(text), dry_run=False)
    assert is_error and "No plan" in again
    assert not fake.batch_updates


async def test_a_plan_from_one_tool_is_refused_by_the_other(ready, fake):
    text, _ = await call(ready, "batch_update", spreadsheet=CANARY_ID, requests=[CHART])
    refused, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(text), dry_run=False)
    assert is_error and "not made by run_sheet_script" in refused


async def test_the_apply_hint_in_a_dry_run_is_the_exact_call_that_applies(ready, fake):
    text, _ = await call(ready, "batch_update", spreadsheet=CANARY_ID, requests=[CHART])
    hint = re.search(r'apply: (\w+) with plan_id="([^"]+)" and dry_run=false', text)
    assert hint, text
    applied, is_error = await call(ready, hint.group(1), spreadsheet=CANARY_ID,
                                   plan_id=hint.group(2), dry_run=False)
    assert not is_error and "applied 1 raw request" in applied, applied


def _grow(fake, rows: int) -> None:
    sheet = next(s for s in fake.books[CANARY_ID]["sheets"]
                 if s["properties"]["sheetId"] == 101)
    sheet["properties"]["gridProperties"]["rowCount"] = rows


def _record_reads(monkeypatch) -> list[list[str]]:
    from gsheet_mcp import sheets_api
    seen: list[list[str]] = []
    real = sheets_api.get_spreadsheet

    def recording(spreadsheet_id, ranges=None, **kw):
        if ranges:
            seen.append([str(r) for r in ranges])
        return real(spreadsheet_id, ranges, **kw)
    monkeypatch.setattr(sheets_api, "get_spreadsheet", recording)
    return seen


def _cells(text: str) -> int:
    from gsheet_mcp import a1
    r = a1.parse(text)
    return (r.end_row - r.start_row) * (r.end_col - r.start_col)


async def test_a_raw_request_over_a_huge_range_gets_a_bounded_partial_guard(
        ready, fake, monkeypatch):
    _grow(fake, 60000)
    seen = _record_reads(monkeypatch)
    rule = {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": 101, "startRowIndex": 1, "endRowIndex": 50001,
                    "startColumnIndex": 11, "endColumnIndex": 12}],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "x"}]},
                        "format": {"textFormat": {"bold": True}}}}}}
    small = {"repeatCell": {"range": {"sheetId": 101, "startRowIndex": 0, "endRowIndex": 2,
                                      "startColumnIndex": 0, "endColumnIndex": 3},
                            "cell": {"userEnteredFormat": {"textFormat": {"italic": True}}},
                            "fields": "userEnteredFormat.textFormat.italic"}}
    planned, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                   requests=[rule, small])
    assert not is_error, planned
    assert ("stale-write check partial: comms!L2:L50001 (first 4,994 of 50,000 cells, "
            "plus the tab's size)") in planned, planned
    assert "comms!A1:C2" not in planned.split("partial")[-1]
    assert seen and all(sum(_cells(r) for r in ranges) <= 5000 for ranges in seen), seen
    applied, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(planned), dry_run=False)
    assert not is_error and "applied 2 raw requests" in applied, applied


async def test_a_raw_request_over_a_few_cells_keeps_the_full_guard(ready, fake):
    small = {"repeatCell": {"range": {"sheetId": 101, "startRowIndex": 0, "endRowIndex": 2,
                                      "startColumnIndex": 0, "endColumnIndex": 3},
                            "cell": {"userEnteredFormat": {"textFormat": {"italic": True}}},
                            "fields": "userEnteredFormat.textFormat.italic"}}
    planned, _ = await call(ready, "batch_update", spreadsheet=CANARY_ID, requests=[small])
    assert "partial" not in planned, planned
    fake.set_cell(CANARY_ID, "comms!C2", "someone else")
    refused, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                   plan_id=_plan_id(planned), dry_run=False)
    assert is_error and "changed since the plan" in refused, refused
    assert not fake.batch_updates


async def test_script_reads_over_the_cell_cap_are_refused_before_reading(ready, fake):
    _grow(fake, 60000)
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                script="return sheet.read('comms').length;")
    assert is_error and "at most 100,000 cells" in text and "pull_tables" in text, text
    fine, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                script="return sheet.read('comms!A1:B2').length;")
    assert not is_error and "return: 2" in fine, fine


@pytest.mark.parametrize("request_, expected", [
    ({"notARequest": {"foo": 1}}, "request 1 (notARequest) is not a Sheets request kind"),
    ({"repeatCell": {"range": {"sheetId": 999999, "startRowIndex": 0, "endRowIndex": 1},
                     "cell": {}, "fields": "userEnteredFormat"}},
     "request 1 (repeatCell) names sheetId 999999, which matches no tab. Tabs: 'comms' (gid 101)"),
])
async def test_an_unknown_request_kind_or_sheet_id_is_refused_at_the_dry_run(
        ready, fake, request_, expected):
    text, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID,
                                requests=[request_])
    assert is_error and "Nothing was planned" in text and expected in text, text
    assert not fake.batch_updates


async def test_a_sheet_id_the_same_batch_creates_is_not_refused(ready, fake):
    text, is_error = await call(ready, "batch_update", spreadsheet=CANARY_ID, requests=[
        {"addSheet": {"properties": {"sheetId": 4242, "title": "new"}}},
        {"repeatCell": {"range": {"sheetId": 4242}, "cell": {}, "fields": "userEnteredFormat"}}])
    assert not is_error and "2 raw requests" in text, text


async def test_googles_request_index_is_mapped_to_the_callers_own(ready, fake, monkeypatch):
    from tests.fake_sheets import http_error
    script = ("sheet.write('multi-region!N21', 'a'); sheet.request({mergeCells: {range: "
              "{sheetId: 101, startRowIndex: 9, endRowIndex: 3}, mergeType: 'MERGE_ALL'}});")
    planned, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                   script=script)
    assert not is_error, planned
    cited = []

    def refusing(spreadsheetId, body):  # noqa: N803
        n = len(body["requests"]) - 1 if not cited else 0
        cited.append(n)
        raise http_error(400, f"Invalid requests[{n}].mergeCells: endRowIndex[3] cannot be "
                              "before startRowIndex[9]")
    monkeypatch.setattr(fake, "batch_update", refusing)
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                plan_id=_plan_id(planned), dry_run=False)
    assert is_error and cited[0] > 0, (cited, text)
    assert "Cannot write to spreadsheet" in text and "Invalid requests[0].mergeCells" in text, text
    again, _ = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=script)
    text, _ = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                         plan_id=_plan_id(again), dry_run=False)
    assert "Invalid the cell writes.mergeCells" in text, text


@pytest.mark.parametrize("literal", ['"\'comms\'!A1:B2"', "'\\'comms\\'!A1:B2'",
                                     "`'comms'!A1:B2`"])
async def test_a_string_literal_read_of_a_quoted_tab_name_is_read_for_the_script(
        ready, fake, literal):
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                script=f"return sheet.read({literal}).length;")
    assert not is_error, text
    assert "return: 2" in text


async def test_a_computed_template_read_is_not_taken_for_a_literal(ready, fake):
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                script="const n = 2;\nreturn sheet.read(`comms!A1:B${n}`);")
    assert is_error and "not read before the script ran" in text, text


async def test_an_error_thrown_by_a_sheet_call_reports_the_callers_line(ready, fake):
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                                script="const x = 1;\nreturn sheet.read('comms!' + 'A1');")
    assert is_error, text
    assert text.startswith("Script error at line 2:"), text


async def test_an_error_thrown_by_the_script_reports_its_own_line_and_column(ready, fake):
    script = "const a = 1;\nconst b = 2;\nif (a < b) { throw new Error('boom'); }"
    text, is_error = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID, script=script)
    assert is_error, text
    assert text.startswith("Script error at line 3:20: Error: boom."), text
    syntax, _ = await call(ready, "run_sheet_script", spreadsheet=CANARY_ID,
                           script="const a = 1;\nconst = ;")
    assert syntax.startswith("Script syntax error at line 2"), syntax

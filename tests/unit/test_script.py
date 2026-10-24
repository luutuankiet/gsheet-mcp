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

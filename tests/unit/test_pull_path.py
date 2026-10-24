"""The pull path through the tool layer: `pull_tables`, `table_status`,
`upload_slot`, and an upload landing through `write_cells` behind a plan."""
from __future__ import annotations

import json
import re

import pytest

from gsheet_mcp import pull, scan, sheets_api
from tests.conftest import call
from tests.fake_sheets import CANARY_ID


@pytest.fixture
def ready(fake, signed_in, client):
    return client


def _csv_objects(store) -> dict[str, bytes]:
    return {h: data for h, (data, kind) in store.objects.items() if kind == "text/csv"}


async def test_pull_tables_streams_the_csv_export_into_the_bucket_with_a_link_and_regions(
        ready, fake, store):
    text, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID, tab="native-table")
    assert not is_error, text
    objects = _csv_objects(store)
    assert len(objects) == 1
    handle, data = next(iter(objects.items()))
    # Byte for byte the tab's CSV export: copied, never parsed.
    assert data == fake.download(sheets_api.export_url(CANARY_ID, "csv", gid=103), 0)
    assert f"csv memory://{handle}" in text
    assert re.search(r"fingerprint 40x8:[0-9a-f]{12}:[0-9a-f]{12}", text)
    assert re.search(r"native-table#\d+ 'native-table'!A\d+:[A-Z]+\d+ table", text), text


async def test_pull_tables_hands_out_a_manifest_listing_the_csv_links_and_regions(
        ready, store):
    text, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID, tab="native-table")
    assert not is_error, text
    first, second = text.splitlines()[:2]
    url = re.fullmatch(r"manifest (memory://(\S+\.json))", second).group(1)
    data, kind = store.objects[url.removeprefix("memory://")]
    assert kind == "application/json"
    manifest = json.loads(data)
    assert manifest["format"] == "gsheet-pull/1" and manifest["spreadsheet_id"] == CANARY_ID
    assert manifest["title"] and manifest["title"] in first and manifest["pulled_at"]
    (tab,) = manifest["tabs"]
    assert tab["tab"] == "native-table" and tab["gid"] == 103 and tab["rows"] == 40
    assert f"csv {tab['csv']}" in text
    assert store.objects[tab["csv"].removeprefix("memory://")][1] == "text/csv"
    assert re.fullmatch(r"40x8:[0-9a-f]{12}:[0-9a-f]{12}", tab["fingerprint"])
    native = [r for r in tab["regions"] if r.get("table_name")]
    assert [r["table_name"] for r in native] == ["Orders"]
    assert native[0]["range"] == "'native-table'!A1:F31" and native[0]["kind"] == "table"
    assert all(r["alias"].startswith("native-table#") for r in tab["regions"])


async def test_pull_tables_on_a_cold_scan_cache_scans_only_the_tab_asked_for(
        ready, store, monkeypatch):
    scanned: list[str | None] = []
    real = scan.scan

    def spy(spreadsheet_id, *, tab=None, include_hidden=False):
        scanned.append(tab)
        return real(spreadsheet_id, tab=tab, include_hidden=include_hidden)

    monkeypatch.setattr(scan, "scan", spy)
    assert scan.load_cached(CANARY_ID) is None
    text, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID, tab="native-table")
    assert not is_error, text
    assert scanned == ["native-table"]
    assert 'tab "native-table"' in text and "native-table#" in text

    scanned.clear()
    store.objects.clear()
    scan.clear(CANARY_ID)
    text, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID,
                                range="'multi-region'!A1:C4")
    assert not is_error, text
    assert scanned == ["multi-region"]


async def test_pull_tables_leaves_out_communication_blocks_unless_named(ready, store):
    text, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID)
    assert not is_error, text
    assert 'tab "comms"' not in text
    assert re.search(r"not pulled \(communication blocks.*comms#1", text), text

    store.objects.clear()
    named, is_error = await call(ready, "pull_tables", spreadsheet=CANARY_ID, range="comms#1")
    assert not is_error, named
    assert 'tab "comms"' in named and re.search(r"comms#1 '?comms'?!\S+ comms", named), named
    assert len(_csv_objects(store)) == 1


async def test_table_status_sees_head_and_tail_edits_but_not_middle_ones(
        ready, fake, monkeypatch):
    monkeypatch.setattr(pull, "HEAD_ROWS", 5)
    monkeypatch.setattr(pull, "TAIL_ROWS", 5)          # native-table is 40 rows: 1-5 and 36-40

    async def status(since=None):
        args = {"since": since} if since else {}
        text, is_error = await call(ready, "table_status", spreadsheet=CANARY_ID,
                                    tab="native-table", **args)
        assert not is_error, text
        return text

    base = re.search(r"fingerprint (\S+)", await status()).group(1)

    fake.set_cell(CANARY_ID, "'native-table'!B20", "edited in the middle")
    middle = await status(base)
    assert "unchanged at head and tail" in middle and "not compared" in middle
    assert re.search(r"fingerprint (\S+)", middle).group(1) == base

    fake.set_cell(CANARY_ID, "'native-table'!B2", "edited at the head")
    head = await status(base)
    assert "changed: first 5 rows" in head and "last" not in head

    tail_base = re.search(r"fingerprint (\S+)", head).group(1)
    fake.set_cell(CANARY_ID, "'native-table'!C40", "edited at the tail")
    tail = await status(tail_base)
    assert "changed: last 5 rows" in tail and "first" not in tail


async def _uploaded(ready, store, csv: bytes) -> str:
    text, is_error = await call(ready, "upload_slot")
    assert not is_error, text
    handle = re.search(r"upload handle (\S+)", text).group(1)
    assert "Content-Type: text/csv" in text and handle.endswith(".csv")
    store.objects[handle] = (csv, "text/csv")           # what the CLI's PUT does
    return handle


async def test_a_csv_in_an_upload_slot_lands_in_an_empty_range_through_a_plan(
        ready, fake, store):
    handle = await _uploaded(ready, store, b"region,total\nnorth,12.5\nsouth,7\n")
    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="'formulas'!B12", upload=handle)
    assert not is_error, planned
    assert "formulas!B12:C14" in planned and not fake.batch_updates
    plan_id = re.search(r"plan (\S+)", planned).group(1)

    landed, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, plan_id=plan_id)
    assert not is_error, landed
    (_, requests), = fake.batch_updates
    assert requests[0]["updateCells"]["fields"] == "userEnteredValue"
    assert sheets_api.values_get(CANARY_ID, "'formulas'!B12:C14") == [
        ["region", "total"], ["north", "12.5"], ["south", "7"]]
    assert "landed 3 rows x 2 columns" in landed
    # The receipt is the read notation, as write_cells gives it.
    assert "C13: 12.5" in landed.splitlines() and "B12: region" in landed.splitlines()
    assert "C13 '" not in landed
    assert handle not in store.objects

    again, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, plan_id=plan_id)
    assert is_error and "Make a new plan" in again


async def test_landing_refuses_a_filled_range_and_a_range_filled_since_the_plan(
        ready, fake, store):
    handle = await _uploaded(ready, store, b"a,b\n1,2\n")
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="'native-table'!A1", upload=handle)
    assert is_error and "is not empty" in text

    planned, _ = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                            range="'formulas'!B12", upload=handle)
    plan_id = re.search(r"plan (\S+)", planned).group(1)
    fake.set_cell(CANARY_ID, "'formulas'!C13", "someone typed here")
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, plan_id=plan_id)
    assert is_error and "Nothing was written" in text and "C13" in text
    assert not fake.batch_updates


async def test_landing_makes_only_plain_decimals_numbers(ready, fake, store):
    handle = await _uploaded(ready, store, b"007\n1e3\n-12\n0.5\n0\n1.\n.5\n-0.25\n")
    planned, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                   range="'formulas'!B12", upload=handle)
    assert not is_error, planned
    plan_id = re.search(r"plan (\S+)", planned).group(1)
    landed, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID, plan_id=plan_id)
    assert not is_error, landed
    (_, requests), = fake.batch_updates
    values = [row["values"][0]["userEnteredValue"]
              for row in requests[0]["updateCells"]["rows"]]
    assert values == [{"stringValue": "007"}, {"stringValue": "1e3"}, {"numberValue": -12},
                      {"numberValue": 0.5}, {"numberValue": 0}, {"stringValue": "1."},
                      {"stringValue": ".5"}, {"numberValue": -0.25}]


async def test_landing_in_a_tab_that_does_not_exist_says_to_add_it_first(ready, store):
    handle = await _uploaded(ready, store, b"a\n1\n")
    text, is_error = await call(ready, "write_cells", spreadsheet=CANARY_ID,
                                range="'not-a-tab'!A1", upload=handle)
    assert is_error and "No tab 'not-a-tab'" in text
    assert "edit_sheet (add_tab)" in text

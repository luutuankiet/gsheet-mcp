# Copied from google-docs-mcp@2fb4652 tests/smoke/test_live_canary.py; renamed, runs the tool layer against live Sheets and Drive on a Drive copy of the canary, deployed-URL checks kept.
"""Live smoke test against real Google APIs and a Drive copy of the canary. Opt-in.

Skipped unless GSHEET_LIVE=1. It then needs:

  GSHEET_LIVE_TOKEN  a Google access token with the spreadsheets and drive scopes
  CANARY_SHEET_ID    the canary spreadsheet (from .env)
  GSHEET_LIVE_URL    optional: the deployed service, for the no-sign-in checks

    GSHEET_LIVE=1 GSHEET_LIVE_TOKEN=... CANARY_SHEET_ID=... uv run pytest tests/smoke -v

The canary itself is only ever copied: every read and the one write go to the
copy, which is trashed in a `finally` whatever happens.
"""
from __future__ import annotations

import os
import re
import time

import httpx
import pytest
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from gsheet_mcp import http_mode, pages, reader, sheets_api
from tests.conftest import call
from tests.fake_sheets import CANARY_ID, FakeSheets

LIVE = os.environ.get("GSHEET_LIVE") == "1"
BASE_URL = (os.environ.get("GSHEET_LIVE_URL") or "").rstrip("/")

pytestmark = pytest.mark.skipif(not LIVE, reason="set GSHEET_LIVE=1 to run")

# The real network boundary, captured before the autouse `offline` fixture
# swaps it out, so the live fixture can put it back.
_REAL = {name: getattr(sheets_api, name)
         for name in ("_sheets_service", "_drive_service", "_download", "_open_download")}

# Two ranges on one tab and one on another: the shape the fake builds for
# `spreadsheets.get` with ranges (one sheet per tab, one data block per range,
# in request order) is what this checks the live API agrees with.
RANGES = ["comms!A1:C3", "comms!B4:D6"]


def _need(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        pytest.fail(f"GSHEET_LIVE=1 needs {name}")
    return value


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, offline, store):
    """Real Sheets, Drive and export link, and a caller holding GSHEET_LIVE_TOKEN."""
    for name, real in _REAL.items():
        monkeypatch.setattr(sheets_api, name, real)
    token = AccessToken(token=_need("GSHEET_LIVE_TOKEN"), client_id="live-smoke",
                        scopes=list(http_mode.GOOGLE_SCOPES), expires_at=None,
                        claims={"sub": "live-smoke"})
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield _need("CANARY_SHEET_ID")
    finally:
        auth_context_var.reset(reset)


def _shape(book: dict) -> list[tuple]:
    return [(sheet["properties"]["title"],
             [(grid.get("startRow", 0), grid.get("startColumn", 0)) for grid in sheet["data"]])
            for sheet in book["sheets"]]


async def test_copy_scan_read_write_and_print_then_trash(live, client):
    canary = live
    drive = sheets_api._drive_service().files()
    copy = drive.copy(fileId=canary, fields="id", supportsAllDrives=True,
                      body={"name": f"gsheet-mcp live smoke {time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"}
                      ).execute()["id"]
    try:
        # Scan, read, then one cell through a plan.
        text, is_error = await call(client, "scan_sheet", spreadsheet=copy)
        assert not is_error and "comms" in text, text

        text, is_error = await call(client, "read_range", spreadsheet=copy, range="comms!A1:D6")
        assert not is_error, text

        marker = f"live smoke {int(time.time())}"
        planned, is_error = await call(client, "write_cells", spreadsheet=copy,
                                       range="comms!A1", values=marker, dry_run=True)
        assert not is_error, planned
        plan_id = re.search(r"plan (\S+)", planned).group(1)
        applied, is_error = await call(client, "write_cells", spreadsheet=copy, plan_id=plan_id)
        assert not is_error, applied
        assert sheets_api.values_get(copy, "comms!A1") == [[marker]]

        # `get` with ranges: the live answer has the shape the fake assumes.
        other = next(s["properties"]["title"]
                     for s in sheets_api.get_tabs(copy)["sheets"]
                     if s["properties"]["title"] != "comms")
        ranges = [*RANGES, f"'{other}'!A1:B2"]
        live_book = sheets_api.get_spreadsheet(copy, ranges, fields=reader.READ_FIELDS)
        fake = FakeSheets()
        fake.load_canary()
        fake_book = fake.spreadsheets().get(spreadsheetId=CANARY_ID, ranges=ranges,
                                            fields=reader.READ_FIELDS,
                                            includeGridData=True).execute()
        assert _shape(live_book) == _shape(fake_book)

        # The PDF print options: a landscape letter page, as pages.PRINT_OPTIONS asks.
        gid = next(s["properties"]["sheetId"] for s in live_book["sheets"]
                   if s["properties"]["title"] == "comms")
        pdf = sheets_api.download(sheets_api.export_url(copy, "pdf", gid=gid, range_="comms!A1:D6")
                                  + pages.PRINT_OPTIONS)
        assert pdf.startswith(b"%PDF")
        import pypdfium2 as pdfium

        width, height = pdfium.PdfDocument(pdf)[0].get_size()
        assert (round(width), round(height)) == (792, 612)
    finally:
        drive.update(fileId=copy, body={"trashed": True}, supportsAllDrives=True).execute()

    assert drive.get(fileId=copy, fields="trashed", supportsAllDrives=True).execute()["trashed"]


# ---- the deployed service, no sign-in needed --------------------------------

deployed = pytest.mark.skipif(not BASE_URL, reason="set GSHEET_LIVE_URL to check the service")


@deployed
def test_health_answers():
    r = httpx.get(f"{BASE_URL}/health", timeout=60)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@deployed
def test_protected_resource_metadata_names_the_service():
    r = httpx.get(f"{BASE_URL}/.well-known/oauth-protected-resource/mcp", timeout=60)
    assert r.status_code == 200
    assert r.json()["resource"].rstrip("/") == f"{BASE_URL}/mcp"


@deployed
def test_mcp_refuses_a_caller_without_a_token():
    r = httpx.post(f"{BASE_URL}/mcp", json={}, timeout=60)
    assert r.status_code == 401

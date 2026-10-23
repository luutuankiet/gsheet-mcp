"""`read_range` through the tool layer, against the fake Sheets API serving the canary.

Golden text lives under `tests/golden/<tab>.txt`; `UPDATE_GOLDEN=1 uv run pytest
tests/unit/test_read_range.py` rewrites it after a deliberate change."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from gsheet_mcp import reader
from tests.conftest import call
from tests.fake_sheets import CANARY_ID

GOLDEN = Path(__file__).resolve().parent.parent / "golden"
TABS = ["comms", "dashboard", "native-table", "multi-region", "hidden", "formulas", "hidden-tab"]


async def _read(client, **arguments) -> str:
    text, is_error = await call(client, "read_range", spreadsheet=CANARY_ID, **arguments)
    assert not is_error, text
    return text


@pytest.mark.parametrize("tab", TABS)
async def test_golden_text_per_canary_tab(fake, signed_in, client, tab):
    text = await _read(client, range=f"'{tab}'")
    path = GOLDEN / f"{tab}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text + "\n")
    assert text + "\n" == path.read_text()


async def test_f8_is_cut_at_500_characters_and_max_chars_0_shows_all(fake, signed_in, client):
    # The issue says 714 characters (+214); the canary fixture holds 744, so +244.
    cut = await _read(client, range="'comms'!F8")
    line = next(x for x in cut.splitlines() if x.startswith("F8"))
    assert line.endswith("…[+244 chars]"), cut[:80] + " ... " + line[-60:]
    assert len(line.removeprefix("F8: ").removesuffix("…[+244 chars]")) == 500
    full = await _read(client, range="'comms'!F8", max_chars=0)
    line = next(x for x in full.splitlines() if x.startswith("F8"))
    assert len(line.removeprefix("F8: ")) == 744
    assert "chars]" not in line


async def test_hidden_rows_and_columns_are_announced_and_skipped(fake, signed_in, client):
    text = await _read(client, range="'hidden'")
    assert "rows 5-6 hidden by the user (2 rows)" in text
    assert "row 10 hidden by filter (1 row)" in text
    assert "column C hidden (1 column)" in text
    rows = {int(m) for m in re.findall(r"^\| (\d+) \|", text, re.MULTILINE)}
    rows |= {int(m) for m in re.findall(r"^── row (\d+)", text, re.MULTILINE)}
    assert rows and not rows & {5, 6, 10}
    assert not re.search(r"^C\d", text, re.MULTILINE)
    shown = await _read(client, range="'hidden'", include_hidden=True)
    assert {5, 6, 10} <= {int(m) for m in re.findall(r"^\| (\d+) \|", shown, re.MULTILINE)}


async def test_comms_notation(fake, signed_in, client):
    text = await _read(client, range="'comms'")
    lines = text.splitlines()
    b3 = next(i for i, x in enumerate(lines) if x.startswith("B3:C3"))
    assert "**ipsum**" in lines[b3] and "[the brief](https://example.com/brief)" in lines[b3]
    assert lines[b3 + 1] == "  note: Owner note: lorem ipsum."
    assert "D3: [x]" in lines and "D4: [ ]" in lines
    assert "E3: On track ▾v1" in lines
    assert "v1 dropdown: On track | At risk | Blocked" in lines
    assert "G3: ★★★☆☆ (3)" in lines
    assert "H3 [image 96x40 over H3:I5, floating]" in lines
    assert any(x.startswith("B1:F1 {s1}: Weekly status") for x in lines)
    assert any(x.startswith("A10: =IMAGE(") for x in lines)
    assert lines[-1].startswith("this range has 1 floating image")
    assert lines[-1].endswith("render_range(\"'comms'\")")


async def test_dropdown_value_outside_the_list_gets_a_bang(fake, signed_in, client):
    fake.set_cell(CANARY_ID, "'comms'!E4", "Parked")
    text = await _read(client, range="'comms'!E4")
    assert "E4: Parked ▾v1!" in text.splitlines()


async def test_cursor_round_trip_without_overlap(fake, signed_in, client, monkeypatch):
    whole = await _read(client, range="'comms'")
    monkeypatch.setattr(reader, "BUDGET_TOKENS", 150)
    seen, cursor, pages = [], None, 0
    while True:
        page = await _read(client, range="'comms'", **({"cursor": cursor} if cursor else {}))
        pages += 1
        seen += [x for x in page.splitlines() if x.startswith("── row ")]
        found = re.search(r"· next: ([A-Z]+[0-9]+)$", page.splitlines()[0])
        if not found:
            break
        cursor = found.group(1)
        assert pages < 30
    assert pages > 1
    assert seen == [x for x in whole.splitlines() if x.startswith("── row ")]


async def test_a_cursor_that_starts_no_row_is_refused(fake, signed_in, client):
    text, is_error = await call(client, "read_range", spreadsheet=CANARY_ID,
                                range="'comms'", cursor="Z99")
    assert is_error and "read again without a cursor" in text


async def test_read_range_accepts_a_scan_alias(fake, signed_in, client):
    scanned, _ = await call(client, "scan_sheet", spreadsheet=CANARY_ID)
    assert "#1" in scanned
    by_alias = await _read(client, range="comms#1")
    region = re.search(r"^\s*#1 ([A-Z]+[0-9]+:[A-Z]+[0-9]+) comms ", scanned, re.MULTILINE)
    assert region, scanned
    assert by_alias == await _read(client, range=f"comms!{region.group(1)}")


async def test_a_dead_alias_is_refused_with_a_rescan_hint(fake, signed_in, client):
    text, is_error = await call(client, "read_range", spreadsheet=CANARY_ID, range="comms#1")
    assert is_error and "scan_sheet" in text

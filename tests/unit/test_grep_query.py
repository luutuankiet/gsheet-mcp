"""`grep_sheet` and `query_range` through the tool layer, against the fake
Sheets API serving the canary."""
from __future__ import annotations

import re

from tests.conftest import call
from tests.fake_sheets import CANARY_ID


async def _call(client, tool: str, **arguments) -> str:
    text, is_error = await call(client, tool, spreadsheet=CANARY_ID, **arguments)
    assert not is_error, text
    return text


def _hits(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("'")]


# ---- grep_sheet ----------------------------------------------------------------------


async def test_grep_lorem_on_comms_hits_value_note_and_comment(fake, signed_in, client):
    plain = await _call(client, "grep_sheet", pattern="lorem", range="'comms'")
    tags = {re.search(r"\[(\w+)", line)[1] for line in _hits(plain)}
    assert {"value", "note"} <= tags and "comment" not in tags
    with_comments = await _call(client, "grep_sheet", pattern="lorem", range="'comms'",
                                comments=True)
    comment = [x for x in _hits(with_comments) if "[comment" in x]
    assert comment == ["'comms'!F4  [comment]  lorem question? / " + comment[0].split(" / ", 1)[1]]


async def test_grep_hit_inside_the_comms_header_merge_reports_the_merge(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern="Weekly status")
    assert "'comms'!B1:F1  [value]  Weekly status · lorem ipsum" in _hits(text)


async def test_grep_hit_in_a_hidden_row_is_tagged_hidden(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern=r"^row [459]$", range="'hidden'")
    lines = _hits(text)
    assert "'hidden'!A5  [value · hidden row]  row 4" in lines
    assert "'hidden'!A10  [value · hidden row]  row 9" in lines
    assert "'hidden'!A2  [value]  row 1" not in lines   # not matched at all
    shown = await _call(client, "grep_sheet", pattern=r"^row 1$", range="'hidden'")
    assert _hits(shown) == ["'hidden'!A2  [value]  row 1"]


async def test_grep_hit_in_a_hidden_tab_is_tagged(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern="lorem")
    assert any(line.startswith("'hidden-tab'!") and "hidden tab" in line
               for line in _hits(text))


async def test_grep_over_50_hits_ends_with_more_in_tab(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern=".", range="'native-table'")
    lines = text.splitlines()
    assert len(_hits(text)) == 50
    total = int(lines[0].split()[0])
    assert total > 50 and "showing the first 50" in lines[0]
    assert lines[-1] == f"+{total - 50} more in 'native-table'"


async def test_grep_searches_formulas_and_refuses_a_bad_regex(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern=r"=SUM\(", range="'formulas'")
    assert any("[formula]" in line for line in _hits(text)), text
    error, is_error = await call(client, "grep_sheet", spreadsheet=CANARY_ID, pattern="(")
    assert is_error and "not a valid Python regex" in error


async def test_grep_no_hits(fake, signed_in, client):
    text = await _call(client, "grep_sheet", pattern="zzz-not-there")
    assert text == "no hits for /zzz-not-there/ in every tab"


# ---- query_range ---------------------------------------------------------------------


async def test_query_native_table_qty_over_50_sorted_by_price(fake, signed_in, client):
    text = await _call(client, "query_range", range="'native-table'", where=["qty > 50"],
                       sort="price", columns=["id", "qty", "price"])
    lines = text.splitlines()
    assert lines[0].startswith("'native-table'!A1:F31 · Table Orders · ")
    assert "rows match qty > 50 · sorted by price" in lines[0]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in lines[4:]]
    assert lines[2] == "| row | id | qty | price |"
    assert rows and all(int(r[2]) > 50 for r in rows)
    prices = [float(r[3].lstrip("£")) for r in rows]
    assert prices == sorted(prices)
    # the same answer from the fixture itself, every row with qty above 50
    full = await _call(client, "read_range", range="'native-table'")
    expected = sorted(
        ([c.strip() for c in line.strip("|").split("|")] for line in full.splitlines()
         if re.match(r"^\| \d+ \| ORD-", line)), key=lambda r: float(r[4].lstrip("£")))
    expected = [[r[0], r[1], r[3], r[4]] for r in expected if int(r[3]) > 50]
    assert rows == expected
    assert f"{len(expected)} of 30 rows match" in lines[0]


async def test_query_count_desc_sort_and_text_conditions(fake, signed_in, client):
    counted = await _call(client, "query_range", range="'native-table'",
                          where=["item = lorem"], count=True)
    assert counted.splitlines() == [
        "'native-table'!A1:F31 · Table Orders · 4 of 30 rows match item = lorem"]
    desc = await _call(client, "query_range", range="'native-table'",
                       where=["remark is not empty"], sort="-qty", columns=["qty", "remark"])
    qtys = [int(line.split("|")[2]) for line in desc.splitlines()[4:]]
    assert qtys == sorted(qtys, reverse=True) and all("rush" in line or "|" in line
                                                      for line in desc.splitlines()[4:])


async def test_query_leaves_out_hidden_rows_and_names_bad_columns(fake, signed_in, client):
    text = await _call(client, "query_range", range="'hidden'", where=["status = drop"])
    assert "hidden rows left out" in text or "hidden row left out" in text
    error, is_error = await call(client, "query_range", spreadsheet=CANARY_ID,
                                 range="'native-table'", where=["nope > 1"])
    assert is_error and "no column 'nope'" in error and "qty" in error

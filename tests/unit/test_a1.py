"""A1 addressing: handles parse to grid bounds and print back unchanged."""
from __future__ import annotations

import pytest

from gsheet_mcp import a1


@pytest.mark.parametrize("handle, bounds", [
    ("'table for comms'!B2:F8", ("table for comms", 1, 8, 1, 6)),
    ("'hidden'", ("hidden", None, None, None, None)),
    ("A:C", (None, None, None, 0, 3)),
    ("3:5", (None, 2, 5, None, None)),
    ("comms!B2", ("comms", 1, 2, 1, 2)),
    ("comms!B2:B", ("comms", 1, None, 1, 2)),
    ("'it''s'!A1:B2", ("it's", 0, 2, 0, 2)),
    ("'hidden-tab'!AA10:AB12", ("hidden-tab", 9, 12, 26, 28)),
])
def test_a_handle_round_trips(handle, bounds):
    rng = a1.parse(handle)
    assert (rng.tab, rng.start_row, rng.end_row, rng.start_col, rng.end_col) == bounds
    assert str(rng) == handle


def test_a_tab_with_spaces_or_punctuation_is_quoted_and_a_plain_one_is_not():
    assert str(a1.Range("table for comms", 0, 1, 0, 1)) == "'table for comms'!A1"
    assert str(a1.Range("comms", 0, 1, 0, 1)) == "comms!A1"
    assert str(a1.Range("AB12", 0, 1, 0, 1)) == "'AB12'!A1"   # looks like a cell


def test_a_bare_tab_name_is_the_whole_tab():
    assert a1.parse("comms") == a1.Range("comms")


def test_a_merge_grid_range_reads_as_its_a1_range():
    merge = {"sheetId": 101, "startRowIndex": 0, "endRowIndex": 1,
             "startColumnIndex": 1, "endColumnIndex": 6}
    rng = a1.Range.from_grid(merge, "comms")
    assert str(rng) == "comms!B1:F1"
    assert rng.to_grid(101) == merge


def test_google_leaves_a_zero_start_out_of_a_grid_range():
    assert a1.Range.from_grid({"sheetId": 1, "endRowIndex": 2, "endColumnIndex": 2}).a1 == "A1:B2"


@pytest.mark.parametrize("column, letters", [(0, "A"), (25, "Z"), (26, "AA"), (701, "ZZ"),
                                             (702, "AAA")])
def test_column_letters_both_ways(column, letters):
    assert a1.column_letters(column) == letters
    assert a1.column_index(letters) == column


@pytest.mark.parametrize("bad", ["", "'unclosed", "comms!", "B5:A1", "'x'y", "A1:B2:C3"])
def test_unparseable_handles_are_refused(bad):
    with pytest.raises(a1.A1Error):
        a1.parse(bad)

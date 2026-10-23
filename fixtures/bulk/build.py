#!/usr/bin/env python3
"""Builds the public bulk spreadsheet: the generated golden tables, at full size.

Two tabs are copied from the working sheet where they were first imported
(SAMPLE_SHEET_ID in `.env`), since one carries two small tables added by hand.
The two 200k-row tabs are written from the CSV in 10k-row chunks, because
`copyTo` fails with a 500 on a tab that size:

    clean-wide-50k              copied: clean-wide-50k.csv, plus tables at Q18:X36 and O55:V73
    side-by-side-small          copied: side-by-side-small.csv
    messy-sales-200k-converted  written USER_ENTERED, as an import with text conversion on
    messy-sales-200k-raw        written RAW, as an import with text conversion off

Every written cell is trimmed of leading and trailing spaces, as the Sheets
import does. Run `generate.py` first so the CSV exists.

`generate.py` recreates the CSVs byte for byte (fixed seed) and
`golden-data-ground-truth.json` holds the totals tests compare against.
The sheet is shared as anyone-with-the-link-can-view; its id goes into `.env`
as BULK_SHEET_ID. Standard library only.
"""
import csv
import json
import time
import urllib.parse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
TITLE = "gsheet-mcp bulk (public, synthetic)"
COPIED = {  # title in the source sheet -> title in the bulk sheet
    "clean-wide-50k": "clean-wide-50k",
    "side-by-side-small": "side-by-side-small",
}
WRITTEN = {"messy-sales-200k-converted": "USER_ENTERED", "messy-sales-200k-raw": "RAW"}
TABS = {**COPIED, **{t: t for t in WRITTEN}}
CHUNK = 10_000

ENV = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
TOKEN = subprocess.run(
    ["gcpx", "exec", ENV["GCPX_ALIAS"], "--", "gcloud", "auth", "print-access-token", "--quiet"],
    capture_output=True, text=True, check=True).stdout.strip()


def call(method, url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={
                                     "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                     "x-goog-user-project": ENV["GCP_PROJECT"]})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url} -> {e.code}\n{e.read().decode()[:2000]}")


def write_csv(dst, title, option):
    with open(Path(__file__).parent / "messy-sales-200k.csv", newline="", encoding="utf-8") as f:
        rows = [[v.strip() for v in r] for r in csv.reader(f)]
    width = max(len(r) for r in rows)
    call("POST", f"{SHEETS}/{dst}:batchUpdate", {"requests": [{"addSheet": {"properties": {
        "title": title, "gridProperties": {"rowCount": len(rows), "columnCount": width}}}}]})
    for start in range(0, len(rows), CHUNK):
        rng = urllib.parse.quote(f"'{title}'!A{start + 1}")
        call("PUT", f"{SHEETS}/{dst}/values/{rng}?valueInputOption={option}",
             {"values": rows[start:start + CHUNK]})
        time.sleep(1.1)  # stay under 60 writes a minute
    print(f"wrote {title}: {len(rows)} rows x {width} columns, {option}")


def main():
    src = ENV["SAMPLE_SHEET_ID"]
    meta = call("GET", f"{SHEETS}/{src}?fields=sheets.properties(sheetId,title)")
    ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    if "--into" in sys.argv:  # resume a build that stopped part way
        dst = sys.argv[sys.argv.index("--into") + 1]
    else:
        dst = call("POST", SHEETS, {"properties": {"title": TITLE}})["spreadsheetId"]
        print(f"created {dst}")
    # A new sheet takes the account's locale; USER_ENTERED parses like the en_US import only under en_US
    call("POST", f"{SHEETS}/{dst}:batchUpdate", {"requests": [{"updateSpreadsheetProperties": {
        "properties": {"locale": "en_US"}, "fields": "locale"}}]})
    have = {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in call("GET", f"{SHEETS}/{dst}?fields=sheets.properties(sheetId,title)")["sheets"]}
    for old, new in TABS.items():  # copyTo names a copy "Copy of <title>"
        if f"Copy of {old}" in have and new not in have:
            have[new] = have.pop(f"Copy of {old}")
    blank = [sid for t, sid in have.items() if t not in TABS.values()]
    renames = []
    for i, (old, new) in enumerate(TABS.items()):
        if new in WRITTEN:
            continue
        if new in have:
            renames.append({"updateSheetProperties": {
                "properties": {"sheetId": have[new], "title": new, "index": i}, "fields": "title,index"}})
            continue
        copied = call("POST", f"{SHEETS}/{src}/sheets/{ids[old]}:copyTo", {"destinationSpreadsheetId": dst})
        renames.append({"updateSheetProperties": {
            "properties": {"sheetId": copied["sheetId"], "title": new, "index": i}, "fields": "title,index"}})
        print(f"copied {old} -> {new}")
    call("POST", f"{SHEETS}/{dst}:batchUpdate", {"requests": renames + [{"deleteSheet": {"sheetId": b}} for b in blank]})
    for title, option in WRITTEN.items():
        if title not in have:
            write_csv(dst, title, option)
    call("POST", f"https://www.googleapis.com/drive/v3/files/{dst}/permissions", {"type": "anyone", "role": "reader"})
    text = (ROOT / ".env").read_text()
    if "\nBULK_SHEET_ID=" not in "\n" + text:
        (ROOT / ".env").write_text(text.rstrip("\n") + f"\nBULK_SHEET_ID={dst}\n")
    print("done; id saved to .env as BULK_SHEET_ID")


if __name__ == "__main__":
    main()
